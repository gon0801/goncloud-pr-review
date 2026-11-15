"""El modo coordinado: el coordinador que converge el PR en cada evento y los pasos del worker que preparan la solicitud y cierran el resultado."""

import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

import review_document
import review_domain
import review_github
from review_commands import leer_comando
from review_config import (
    ERROR_KEY,
    IDENTIDADES,
    PROVIDERS,
    env,
    get_provider,
    politica_de_identidad,
    politica_de_revision,
    secretos_del_entorno,
)
from review_context import GitRepository, digest_de_politica
from review_domain import (
    leer_cierre,
    parse_model_findings,
    strip_model_findings_block,
)
from review_github import PublishReceipt, Unconfirmed
from review_render import (
    caution_banner,
    neutralizar_marcadores,
    prosa_del_modelo,
    redact,
    strip_model_verdict,
)
from review_sticky import (
    cobertura_declarada,
    continuacion_terminal,
    libro_fusionado,
    modelo_utilizable,
    prev_de_memoria,
    recortes_del_manifest,
    sha_hexa,
    sticky_from_comments,
)


@dataclass
class AuthenticatedResult:
    observaciones: tuple
    cobertura: str
    run_id: int
    attempt: int


@dataclass
class Rejected:
    """Resultado rechazado: nunca ejecuta contenido ni satisface la solicitud."""

    motivo: str


def _login_de(comentario):
    user = comentario.get("user")
    if isinstance(user, str):
        return user
    return comentario.get("login") or (user or {}).get("login")


def request_de_solicitud(
    solicitud, repository="", workflow="ai-review-worker", ref=None, workflow_sha=None
):
    """Contexto de confianza del resultado: el target sale de la solicitud
    persistida, nunca del artifact que el propio worker escribió."""
    return {
        "id": solicitud.id,
        "target": dict(solicitud.target),
        "repository": repository,
        "workflow": workflow,
        "ref": ref,
        "workflow_sha": workflow_sha,
    }


def dispatch_confirmed(recibo: PublishReceipt, *, despachar):
    for solicitud in recibo.trabajo:
        despachar(solicitud)


def forma_utilizable_del_artifact(artifact):
    modelo = parse_model_findings(artifact.get("result") or "", conservar_anclas=True)
    return modelo_utilizable({"findings": artifact.get("observaciones") or ()}) and (
        modelo is None or modelo_utilizable(modelo)
    )


def rechazo_del_run(run_metadata, request):
    """Por qué el run no es el worker confiable (repo, workflow, ref y SHA del
    código), o None si lo es."""
    if run_metadata.get("repository") != request.get("repository"):
        return "repositorio del run incorrecto"
    if run_metadata.get("workflow") != request.get("workflow"):
        return "workflow del run incorrecto"
    if run_metadata.get("ref") != request.get("ref"):
        return "ref del run incorrecta"
    if run_metadata.get("sha") != request.get("workflow_sha"):
        return "SHA del workflow no es el código confiable"
    return None


def authenticate_result(run_metadata, artifact, request):
    """El SHA del workflow (código confiable) y el HEAD del PR se validan por
    separado; el contenido del artifact nunca se ejecuta.
    """

    def rechazo(motivo):
        return Rejected(motivo=motivo)

    motivo = rechazo_del_run(run_metadata, request)
    if motivo is not None:
        return rechazo(motivo)
    if run_metadata.get("run_id") != artifact.get("run_id"):
        return rechazo("run_id no coincide con el artifact")
    if run_metadata.get("attempt") != artifact.get("attempt"):
        return rechazo("attempt no coincide con el artifact")
    if artifact.get("request_id") != request.get("id"):
        return rechazo("request_id del artifact incorrecto")
    target = request.get("target") or {}
    if artifact.get("pr_head_sha") != target.get("head_sha"):
        return rechazo("HEAD del PR del artifact no es el target vigente")
    if artifact.get("policy_digest") != target.get("policy_digest"):
        return rechazo("digest de política del artifact incorrecto")
    modelo = parse_model_findings(
        artifact.get("result") or "",
        conservar_anclas=artifact.get("finding_identity") == "anchors",
    )
    return AuthenticatedResult(
        observaciones=tuple(
            modelo["findings"]
            if modelo is not None
            else artifact.get("observaciones") or ()
        ),
        cobertura=(artifact.get("cobertura") or review_domain.UNKNOWN)
        if forma_utilizable_del_artifact(artifact)
        else review_domain.PARTIAL,
        run_id=artifact.get("run_id"),
        attempt=artifact.get("attempt"),
    )


class ComentariosGh:
    def __init__(self, repo, pr):
        self.repo, self.pr = repo, pr

    def leer(self):
        return review_github.fetch_all_comments(self.repo, self.pr)

    def parchar(self, cid, body):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write(json.dumps({"body": body}))
            ruta = fh.name
        try:
            review_github.sh(
                "gh",
                "api",
                "-X",
                "PATCH",
                f"repos/{self.repo}/issues/comments/{cid}",
                "--input",
                ruta,
            )
        finally:
            Path(ruta).unlink(missing_ok=True)

    def crear(self, body):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write(json.dumps({"body": body}))
            ruta = fh.name
        try:
            review_github.sh(
                "gh",
                "api",
                "-X",
                "POST",
                f"repos/{self.repo}/issues/{self.pr}/comments",
                "--input",
                ruta,
            )
        finally:
            Path(ruta).unlink(missing_ok=True)


def despachar_worker(solicitud, *, repo, ref, run_id, pr_number):
    review_github.sh(
        "gh",
        "workflow",
        "run",
        "ai-review-worker.yml",
        "--repo",
        repo,
        "--ref",
        ref,
        "-f",
        f"request_id={solicitud.id}",
        "-f",
        f"kind={solicitud.kind}",
        "-f",
        f"pr_number={pr_number}",
        "-f",
        f"head_sha={solicitud.target.get('head_sha', '')}",
        "-f",
        f"base_sha={solicitud.target.get('base_sha', '')}",
        "-f",
        f"coordinator_run_id={run_id}",
        "-f",
        f"finding_identity={politica_de_identidad()}",
    )


def _pr_admisible(pr_data, repo):
    """Lo que el `if:` del workflow exige a un push: PR abierto, no borrador y
    del propio repo. Sin datos del PR es un push, que el workflow ya filtró."""
    if pr_data is None:
        return True
    cabeza = pr_data.get("head") or {}
    return (
        pr_data.get("state") == "open"
        and pr_data.get("draft") is False
        and (cabeza.get("repo") or {}).get("full_name") == repo
    )


def _admision(target, pr_data, repo):
    """La revisión que pide cualquier evento. El push trae su head y reintenta
    una revisión fallida; la recuperación manual siempre pide una nueva; el
    resto admite el head vivo solo si el PR es admisible."""
    nombre = os.environ.get("GITHUB_EVENT_NAME", "")
    recuperacion = nombre == "workflow_dispatch" and not os.environ.get(
        "WORKER_RUN_ID"
    )
    if not recuperacion and not _pr_admisible(pr_data, repo):
        return None
    return review_domain.RequestReview(
        origin=review_domain.Origin(
            kind="re-run" if recuperacion else "push", run_id=_run_id()
        ),
        target=target,
        reintenta=nombre in ("pull_request", "pull_request_target"),
    )


def _run_id():
    valor = os.environ.get("GITHUB_RUN_ID") or "0"
    return int(valor) if valor.isdigit() else 0


def _comandos_de_comentarios(comentarios, login, cursor):
    """El checkpoint es datos, nunca comandos: fuera los comentarios del
    propio bot y el texto que no contiene un comando."""

    comandos = []
    for c in comentarios or []:
        cid = c.get("id")
        if not isinstance(cid, int) or cid <= cursor:
            continue
        autor = _login_de(c)
        if autor is None or (login and autor == login):
            continue
        cuerpo = c.get("body") or ""
        parsed = leer_comando(cuerpo)
        if parsed is None:
            continue
        comando = {
            "action": parsed.accion,
            "arg": next(iter(parsed.ids)) if parsed.accion == "explicar" else "",
            "extra": parsed.extra,
        }
        if parsed.accion == "descartar":
            comando.update(ids=parsed.ids, todo=parsed.todo)
        comandos.append(
            {
                "cid": cid,
                "login": autor,
                "creado": c.get("created_at") or "",
                "editado": c.get("updated_at") or "",
                **comando,
            }
        )
    return tuple(comandos)


def fallo_de_resultado(artifact, run_id, attempt):
    # La presencia de la clave decide, como reviewed= en el worker y el aviso
    # de la vista tipada: un motivo vacío sigue siendo un fallo.
    if ERROR_KEY not in (artifact or {}):
        return None
    return review_domain.ReportFailed(
        request_id=artifact.get("request_id"),
        run_id=run_id,
        attempt=attempt,
        motivo=artifact[ERROR_KEY] or "el worker falló sin motivo",
    )


TITULO_DEL_WORKER = re.compile(r"PR ([0-9]{1,9}) solicitud ([0-9]{1,9})")
CONCLUSIONES_DE_GITHUB = frozenset(
    {
        "action_required",
        "cancelled",
        "failure",
        "neutral",
        "skipped",
        "stale",
        "startup_failure",
        "success",
        "timed_out",
    }
)


@dataclass(frozen=True)
class Entrega:
    """Lo que dejó el run de una solicitud viva: su artifact autenticado, o
    None si terminó sin subirlo."""

    solicitud: object
    meta: dict
    conclusion: str | None
    artifact: dict | None = None
    autenticado: AuthenticatedResult | None = None


def _run_avisado():
    """(run, attempt) del worker que avisó; las entradas del dispatch solo se
    comparan como enteros."""
    run_id = os.environ.get("WORKER_RUN_ID") or ""
    attempt = os.environ.get("WORKER_ATTEMPT") or "1"
    if re.fullmatch(r"[0-9]{1,19}", run_id) and re.fullmatch(r"[0-9]{1,9}", attempt):
        return int(run_id), int(attempt)
    return None


def corridas_por_solicitud(repo, pr):
    """Los runs del worker de este PR por solicitud, del más nuevo al más viejo.
    El run-name del worker es `PR <n> solicitud <id>`: así se atribuye también
    un run que terminó sin artifact."""
    datos = json.loads(
        review_github.sh(
            "gh",
            "api",
            f"repos/{repo}/actions/workflows/ai-review-worker.yml/runs"
            "?event=workflow_dispatch&per_page=100",
        ).stdout
    )
    corridas = {}
    for run in datos.get("workflow_runs") or []:
        titulo = TITULO_DEL_WORKER.fullmatch(str(run.get("display_title") or ""))
        if (
            titulo
            and int(titulo.group(1)) == int(pr)
            and type(run.get("id")) is int
            and type(run.get("run_attempt")) is int
        ):
            corridas.setdefault(int(titulo.group(2)), []).append(run)
    for runs in corridas.values():
        runs.sort(key=lambda run: -run["id"])
    return corridas


def sha_de_confianza(repo, worker_ref, sha_del_run, cache):
    """El SHA del código confiable para un run: la punta de worker_ref, o el del
    run si es un ancestro suyo (main pudo avanzar mientras el worker corría)."""
    if "punta" not in cache:
        cache["punta"] = json.loads(
            review_github.sh("gh", "api", f"repos/{repo}/commits/{worker_ref}").stdout
        ).get("sha")
    if not sha_hexa(sha_del_run) or sha_del_run == cache["punta"]:
        return cache["punta"]
    if sha_del_run not in cache:
        comparacion = json.loads(
            review_github.sh("gh", "api", f"repos/{repo}/compare/{sha_del_run}...{worker_ref}").stdout
        )
        cache[sha_del_run] = comparacion.get("status") in ("ahead", "identical")
    return sha_del_run if cache[sha_del_run] else cache["punta"]


def artefacto_del_run(repo, run, request_id, work):
    """El result.json del artifact del attempt vigente, o None si no lo subió.
    El nombre y la carpeta salen de enteros, nunca del texto de la API."""
    nombre = (
        f"ai-review-result-request-{request_id}"
        f"-run-{run['id']}-attempt-{run['run_attempt']}"
    )
    listado = json.loads(
        review_github.sh(
            "gh", "api", f"repos/{repo}/actions/runs/{run['id']}/artifacts?per_page=100"
        ).stdout
    )
    if not any(
        a.get("name") == nombre and not a.get("expired")
        for a in listado.get("artifacts") or []
    ):
        return None
    destino = Path(work) / "cosecha" / f"{run['id']}-{run['run_attempt']}"
    review_github.sh(
        "gh",
        "run",
        "download",
        str(run["id"]),
        "--repo",
        repo,
        "--name",
        nombre,
        "--dir",
        str(destino),
    )
    encontrados = list(destino.rglob("result.json"))
    if len(encontrados) != 1:
        raise RuntimeError(f"{nombre} trae {len(encontrados)} result.json, no uno")
    return json.loads(encontrados[0].read_text())


def cosechar(estado, repo, pr, worker_ref, work, avisado):
    """Lo que el worker ya dejó para las solicitudes vivas. De cada una cuenta
    su run más nuevo y confiable: con artifact del attempt vigente, o
    terminado sin él (el avisado ya terminó su execute aunque su job de aviso
    siga en curso); si sigue en curso, espera. Una pendiente sin ningún run
    perdió su despacho; una fallida sin run no se despacha, porque reintentar
    es cosa de un push, de `revisar` o de la recuperación. De una fallida
    solo cuenta un artifact, el de un "Re-run jobs" exitoso del worker:
    volver a reportar su fallo pintaría el aviso encima de la revisión nueva.
    Devuelve (entregas, sin_corrida, hubo_rechazo)."""
    vivas = {r.id: r for r in estado.pending_requests if r.viva}
    if avisado is None and not vivas:
        return [], [], False
    corridas, rechazo, rid_avisado = corridas_por_solicitud(repo, pr), False, None
    for rid, runs in corridas.items():
        if avisado and any(run["id"] == avisado[0] for run in runs):
            rid_avisado = rid
    if avisado and rid_avisado is None:
        print(
            f"ai-review: el run avisado {avisado[0]} no es un worker de este PR",
            file=sys.stderr,
        )
        rechazo = True
    elif rid_avisado is not None and rid_avisado not in vivas:
        if not 0 < rid_avisado <= estado.request_count:
            sys.exit(f"ai-review: resultado de solicitud desconocida {rid_avisado!r}")
        # La podó una solicitud más nueva, cuyo despacho cancela este worker;
        # si el run alcanzó a avisar antes, no queda nada que publicar.
        print(
            f"ai-review: resultado de la solicitud {rid_avisado}"
            " ignorado: ya no está pendiente",
            file=sys.stderr,
        )
    confianza, entregas, sin_corrida = {}, [], []
    for solicitud in vivas.values():
        fallida = solicitud.fallida and solicitud.id != rid_avisado
        if solicitud.id not in corridas:
            if not fallida:
                sin_corrida.append(solicitud)
            continue
        for run in corridas[solicitud.id]:
            forma = forma_del_run(run)
            meta = {
                "repository": repo,
                "workflow": forma["name"],
                "ref": forma["head_branch"],
                "sha": forma["head_sha"],
                "run_id": forma["id"],
                "attempt": forma["run_attempt"],
            }
            request = request_de_solicitud(
                solicitud,
                repository=repo,
                workflow="ai-review-worker",
                ref=worker_ref,
                workflow_sha=sha_de_confianza(repo, worker_ref, meta["sha"], confianza),
            )
            motivo = rechazo_del_run(meta, request)
            if motivo is not None:
                print(
                    f"ai-review: run {run['id']} ignorado ({motivo})", file=sys.stderr
                )
                rechazo = True
                continue
            conclusion = run.get("conclusion")
            if conclusion not in CONCLUSIONES_DE_GITHUB:
                conclusion = None
            terminado = run.get("status") == "completed" or avisado == (
                run["id"],
                run["run_attempt"],
            )
            artifact = None
            if terminado or run.get("status") == "in_progress":
                artifact = artefacto_del_run(repo, run, solicitud.id, work)
            if artifact is not None:
                autenticado = authenticate_result(meta, artifact, request)
                if isinstance(autenticado, Rejected):
                    print(
                        f"ai-review: resultado rechazado ({autenticado.motivo})",
                        file=sys.stderr,
                    )
                    rechazo = True
                    break
                entregas.append(
                    Entrega(
                        solicitud, meta, conclusion, artifact, autenticado
                    )
                )
            elif terminado and not fallida:
                entregas.append(Entrega(solicitud, meta, conclusion))
            break
    return entregas, sin_corrida, rechazo


def evento_de_entrega(entrega, facts, estado):
    run_id, attempt = entrega.meta["run_id"], entrega.meta["attempt"]
    if entrega.artifact is None:
        motivo = (
            f"el worker terminó sin resultado (run {run_id}, "
            f"{entrega.conclusion or 'sin conclusión'})"
        )
        return (
            review_domain.ReportFailed(
                request_id=entrega.solicitud.id,
                run_id=run_id,
                attempt=attempt,
                motivo=motivo,
            ),
            facts,
        )
    fallo = fallo_de_resultado(entrega.artifact, run_id, attempt)
    if fallo is not None:
        return fallo, facts
    hechos = hechos_con_delta_del_worker(facts, entrega.artifact, estado)
    hechos = hechos_con_recortes_del_worker(hechos, entrega.artifact)
    return (
        review_domain.ReportReady(
            origin=review_domain.Origin(kind="re-run", run_id=run_id),
            request_id=entrega.solicitud.id,
            run_id=entrega.autenticado.run_id,
            attempt=entrega.autenticado.attempt,
            observaciones=tuple(
                review_domain.observation_de_entrada(e)
                for e in entrega.autenticado.observaciones
            ),
            cobertura=entrega.autenticado.cobertura,
        ),
        hechos,
    )


def forma_del_run(datos):
    """Un run de la lista de runs del worker en la forma que autentica el
    coordinador. Con run-name, "name" trae el título del run; el workflow sale
    del path, que el título no puede imitar."""
    ruta = datos.get("path") or ""
    return {
        "id": datos.get("id"),
        "name": Path(ruta).stem if ruta.startswith(".github/workflows/") else None,
        "head_branch": datos.get("head_branch"),
        "head_sha": datos.get("head_sha"),
        "run_attempt": datos.get("run_attempt"),
    }


def hechos_con_delta_del_worker(facts, artifact, current):
    """Los hechos del coordinador con el delta que calculó el worker, para que
    una resolución del modelo se pueda verificar contra un cambio pertinente.

    Se llama solo con un artifact autenticado (run del worker en el ref de
    confianza): el manifest lo escribe el CLI fijado, que trata el PR como
    datos. Además el delta debe partir del head que esta memoria acreditó por
    última vez; si no, es de otra base y se degrada a «sin delta» (el resuelto
    del modelo queda sin verificar), con aviso."""
    manifest = artifact.get("manifest") or {}
    desde = manifest.get("prev_sha")
    cambiados = manifest.get("changed_files")
    esperado = current.revision.head_sha if current.revision else None
    if (
        sha_hexa(desde)
        and desde == esperado
        and isinstance(cambiados, list)
        and all(isinstance(ruta, str) and ruta for ruta in cambiados)
    ):
        return replace(facts, changed_paths=tuple(cambiados), delta_calculado=True)
    if not current.findings:
        return facts  # nada que resolver: la primera revisión no lleva delta
    print(
        f"ai-review: sin delta verificable del worker (prev_sha {str(desde)[:7]} "
        f"contra el último revisado {str(esperado)[:7]}); las resoluciones del "
        f"modelo quedan sin verificar",
        file=sys.stderr,
    )
    return facts


def hechos_con_recortes_del_worker(facts, artifact):
    """Las omisiones que el worker ya aplicó (archivos fuera por tamaño del
    diff o por encoding), plegadas en las omisiones con el mismo formato de la
    ruta directa: sin él, el «complete» que declara el modelo acredita
    archivos que nadie leyó. El manifest viaja en el artifact autenticado y el
    dominio baja la cobertura con cualquier omisión."""
    recortes = tuple(
        f"archivo excluido ({e.get('reason')}): {e.get('path')}"
        for e in recortes_del_manifest(artifact.get("manifest"))
    )
    if not recortes:
        return facts
    return replace(facts, omissions=facts.omissions + recortes)


def _ultimo_comentario(adaptador):
    return max(
        (c["id"] for c in adaptador.leer() if isinstance(c.get("id"), int)),
        default=0,
    )


@dataclass(frozen=True)
class Plan:
    """Lo que decidió una corrida del coordinador; cmd_reconcile solo lo publica y lo despacha."""

    estado: object
    trabajo: tuple
    terminadas: tuple
    sin_cambios: bool
    document: review_document.ReviewDocument | None = None


def decidir(
    current,
    estado,
    trabajo,
    avisos,
    entregas,
    sin_corrida,
    admision,
    *,
    facts,
    policy,
    admisible,
    hay_sticky,
    permisos,
    ultimo_comentario,
    document,
):
    # Se despacha otra vez; si el run solo tardó en aparecer en la lista, el
    # grupo del worker cancela al anterior y cuenta el más nuevo. Un PR
    # cerrado o en borrador no gasta otra corrida del modelo.
    if admisible:
        por_despachar = {w.id for w in trabajo}
        trabajo = (*trabajo, *(s for s in sin_corrida if s.id not in por_despachar))
    previo = estado
    revision_entregada, explicaciones = None, []
    for entrega in entregas:
        evento, hechos = evento_de_entrega(entrega, facts, estado)
        decision = review_domain.reconcile(estado, evento, hechos, policy)
        if not isinstance(decision, review_domain.Commit):
            continue
        solicitud, artifact = entrega.solicitud, entrega.artifact
        # Solo un reporte del target vigente se muestra como revisión: el
        # dominio también hace Commit al descartar un head viejo.
        if review_domain._target_vigente(solicitud, facts, policy):
            if solicitud.kind == "review":
                revision_entregada = (
                    artifact or {ERROR_KEY: evento.motivo},
                    {f.id for f in estado.findings},
                    solicitud.target.get("head_sha") or facts.revision.head_sha,
                )
            elif artifact is not None and ERROR_KEY not in artifact:
                explicaciones.append(
                    (solicitud.finding_id, texto_de_explicacion(artifact))
                )
        estado = decision.snapshot
    estado = review_domain.marcar_hallazgos_nuevos(previo, estado, ultimo_comentario)
    if admision is not None:
        admitido = review_domain.reconcile(estado, admision, facts, policy)
        estado = admitido.snapshot
        # Una revisión nueva poda la anterior, que ya no se despacha.
        vivas = {r.id for r in estado.pending_requests}
        trabajo = tuple(w for w in trabajo if w.id in vivas)
        trabajo = (*trabajo, *admitido.work_after_commit)

    terminadas = ()
    if trabajo:
        snapshot_ra, despachables, terminadas = review_domain.reautorizar(
            estado, trabajo, permisos
        )
        estado, trabajo = snapshot_ra or estado, tuple(despachables)

    if not hay_sticky and admision is None:
        avisos = ()

    if estado == current and not trabajo:
        return Plan(estado, trabajo, terminadas, sin_cambios=True)

    visible = replace(document, memory=estado)
    if revision_entregada is not None:
        artifact, previos, cabeza = revision_entregada
        if ERROR_KEY in artifact:
            visible = replace(
                visible,
                failure=review_document.FailureView(
                    cabeza, "worker-failed",
                    review_document.SafeProse(str(artifact[ERROR_KEY])),
                ),
            )
        else:
            proveedor = artifact.get("review_provider")
            if proveedor not in PROVIDERS:
                proveedor = get_provider()[0]
            manifest = {"reviewed": [], "excluded": [], **(artifact.get("manifest") or {})}
            libro = artifact.get("covered")
            if libro is not None and not (
                artifact.get("model_ok", True) and forma_utilizable_del_artifact(artifact)
            ):
                previo = dict(document.covered.entries) if document.covered else {}
                libro = {
                    ruta: blob for ruta, blob in libro.items()
                    if previo.get(ruta) == blob
                }
            visible = review_document.document_from_result(
                visible,
                estado,
                artifact,
                manifest,
                proveedor,
                cabeza,
                [finding.id for finding in estado.findings if finding.id not in previos],
                libro,
            )
    visible = review_document.update_presentation(
        visible, estado, explicaciones, avisos,
    )
    return Plan(estado, trabajo, terminadas, sin_cambios=False, document=visible)


def cmd_reconcile(args):
    """Un run converge el PR sin importar qué evento lo despertó: los comandos
    sin procesar (el cursor los hace idempotentes), los resultados que el
    worker ya dejó, la admisión del head y al final una sola escritura."""
    repo, pr = env("REPO"), env("PR_NUMBER")
    worker_ref = env("WORKER_REF")
    login = os.environ.get("BOT_LOGIN") or "github-actions[bot]"
    policy = politica_de_revision()
    digest = digest_de_politica(policy)
    adaptador = ComentariosGh(repo, pr)
    comments = adaptador.leer()
    sticky = sticky_from_comments(comments, login)
    document = review_document.read_document(sticky["body"] if sticky else "")
    if isinstance(document, review_document.DocumentBlocked):
        print(
            "ai-review: memoria inválida o de versión futura; se conserva sin tocar",
            file=sys.stderr,
        )
        return
    current = document.memory
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    if event_name == "issue_comment":
        payload = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        if payload.get("action") != "created":
            accion = payload.get("action")
            print(
                f"ai-review: issue_comment {accion!r} no admite comandos",
                file=sys.stderr,
            )
            return
    head = os.environ.get("HEAD_SHA") or ""
    base = os.environ.get("BASE_SHA") or ""
    pr_data = None
    if event_name in ("issue_comment", "workflow_dispatch"):
        # el head/base vivos salen del PR por API, nunca del checkpoint
        try:
            pr_data = json.loads(review_github.sh("gh", "api", f"repos/{repo}/pulls/{pr}").stdout)
            head = pr_data["head"]["sha"]
            base = pr_data["base"]["sha"]
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            print(
                f"ai-review: no se pudo consultar el PR ({exc}); "
                "queda pendiente para el próximo evento",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        head = head or (current.revision.head_sha if current.revision else "")
        base = base or (current.revision.base_sha if current.revision else "")
        if not head or not base:
            sys.exit(
                "ai-review: faltan HEAD_SHA/BASE_SHA y el checkpoint no tiene revisión"
            )
    facts = review_domain.RepositoryFacts(
        revision=review_domain.Revision(
            base_sha=base, head_sha=head, policy_digest=digest
        )
    )
    target = review_domain.ReviewTarget(
        repository=repo,
        pr_number=int(pr),
        head_sha=head,
        base_sha=base,
        policy_digest=digest,
    )

    def permisos(login_consultado):
        return review_github.collaborator_permission(repo, login_consultado)

    estado, trabajo, rechazos = review_domain.procesar_comandos(
        current,
        _comandos_de_comentarios(comments, login=login, cursor=current.command_cursor),
        permisos,
        target,
    )
    for cid, effect in rechazos:
        print(f"ai-review: comando {cid} rechazado ({effect})")
    avisos = tuple(
        aviso
        for cid, effect in rechazos
        if (aviso := notice_de_rechazo(cid, effect)) is not None
    )

    if event_name not in (
        "issue_comment",
        "workflow_dispatch",
        "pull_request",
        "pull_request_target",
    ):
        sys.exit(
            "ai-review: evento sin admisión en reconcile "
            "(pull_request, pull_request_target, issue_comment o workflow_dispatch)"
        )

    try:
        entregas, sin_corrida, rechazo = cosechar(
            estado, repo, pr, worker_ref, args.work, _run_avisado()
        )
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        print(
            f"ai-review: no se pudo cosechar el worker ({exc}); "
            "se reintenta en el próximo evento",
            file=sys.stderr,
        )
        entregas, sin_corrida, rechazo = [], [], True
    admision = _admision(target, pr_data, repo)
    plan = decidir(
        current,
        estado,
        trabajo,
        avisos,
        entregas,
        sin_corrida,
        admision,
        facts=facts,
        policy=policy,
        admisible=_pr_admisible(pr_data, repo),
        hay_sticky=sticky is not None,
        permisos=permisos,
        ultimo_comentario=lambda: _ultimo_comentario(adaptador),
        document=document,
    )
    for terminada in plan.terminadas:
        print(f"ai-review: solicitud {terminada.id} terminada ({terminada.motivo})")
    if plan.sin_cambios:
        print("ai-review: nada cambió; el sticky queda como está")
        if rechazo:
            sys.exit(1)
        return

    prepared = review_document.prepare_publication(
        plan.document, review_domain.StorageBudget(), secretos_del_entorno(),
    )
    if isinstance(prepared, review_domain.CapacityExceeded):
        status = review_github.publish_capacity_notice(
            sticky, adaptador, login=login, secrets=secretos_del_entorno(),
        )
        print(f"ai-review: {status.motivo}", file=sys.stderr)
        sys.exit(1)
    if isinstance(prepared, review_document.DocumentBlocked):
        print("ai-review: documento inválido; se conserva sin tocar", file=sys.stderr)
        sys.exit(1)
    resultado = review_github.publish_checkpoint(
        prepared, sticky or None, adaptador, login=login,
        work_after_commit=plan.trabajo,
    )
    if isinstance(resultado, Unconfirmed):
        print(
            f"ai-review: escritura incierta ({resultado.motivo}); se relee en el próximo evento",
            file=sys.stderr,
        )
        sys.exit(1)
    if resultado.trabajo:
        dispatch_confirmed(
            resultado,
            despachar=lambda s: despachar_worker(
                s, repo=repo, ref=worker_ref, run_id=_run_id(), pr_number=pr
            ),
        )
        print(
            f"ai-review: {len(resultado.trabajo)} solicitud(es) confirmada(s) y despachada(s)"
        )
    elif resultado.recibo.get("sin_texto_visible"):
        print("ai-review: nada visible que mostrar; no se crea sticky")
    else:
        print("ai-review: checkpoint publicado sin trabajo nuevo")
    if rechazo:
        sys.exit(1)


def cmd_execute_request(args):
    """Lee la solicitud persistida y escribe el paquete de datos para el
    artifact. Nunca publica ni ejecuta contenido del PR."""
    repo, pr = env("REPO"), env("PR_NUMBER")
    head = env("HEAD_SHA")
    base = os.environ.get("BASE_SHA", "")
    adaptador = ComentariosGh(repo, pr)
    sticky = sticky_from_comments(
        adaptador.leer(), os.environ.get("BOT_LOGIN") or "github-actions[bot]"
    )
    document = review_document.read_document(sticky["body"] if sticky else "")
    if isinstance(document, review_document.DocumentBlocked):
        sys.exit(
            "ai-review: memoria inválida o de versión futura; "
            "no se ejecuta la solicitud"
        )
    current = document.memory
    solicitud = next(
        (
            r
            for r in current.pending_requests
            if r.id == int(args.request_id)
            and r.viva
        ),
        None,
    )
    if solicitud is None:
        sys.exit(f"ai-review: la solicitud {args.request_id!r} no está pendiente")
    policy = replace(
        politica_de_revision(),
        diff_max_bytes=int(os.environ.get("MAX_DIFF_BYTES", "1500000")),
        strict_budget=False,
    )
    previo = prev_de_memoria(sticky["body"] if sticky else None)
    paquete = {
        "request_id": solicitud.id,
        "kind": solicitud.kind,
        "finding_id": solicitud.finding_id,
        "run_id": _run_id(),
        "attempt": int(os.environ.get("GITHUB_RUN_ATTEMPT") or 0),
        "pr_head_sha": head,
        "base_sha": base,
        "policy_digest": digest_de_politica(policy),
        "finding_identity": politica_de_identidad(),
        "target": dict(solicitud.target),
        "observaciones": [],
        "cobertura": review_domain.UNKNOWN,
    }
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    destino = work / "request-package.json"
    destino.write_text(json.dumps(paquete))
    # La misma memoria autenticada que autorizó la solicitud; los comandos ya
    # los aplicó el coordinador hasta su cursor.
    (work / "prev.json").write_text(json.dumps(previo))
    print(
        f"ai-review: paquete de la solicitud {solicitud.id} preparado "
        f"(run {paquete['run_id']} attempt {paquete['attempt']})"
    )


def cmd_close_result(args):
    work = Path(args.work)
    paquete = json.loads((work / "request-package.json").read_text())
    resultado = json.loads((work / "result.json").read_text())
    secretos = secretos_del_entorno()
    texto = redact(resultado.get("result") or "", secretos)
    review_github.avisar_bloque_sin_cierre(texto)
    # La identidad viaja en el paquete desde la solicitud del coordinador.
    identidad = paquete.get("finding_identity") or politica_de_identidad()
    if identidad not in IDENTIDADES:
        sys.exit(f"ai-review: identidad de hallazgos '{identidad}' no admitida")
    modelo = parse_model_findings(texto, conservar_anclas=identidad == "anchors")
    model_ok = modelo_utilizable(parse_model_findings(texto, conservar_anclas=True))
    manifiesto = work / "manifest.json"
    datos = json.loads(manifiesto.read_text()) if manifiesto.exists() else None
    cobertura = cobertura_declarada(texto, modelo)
    if datos is not None and continuacion_terminal(datos):
        # Continuación terminal: el libro ya cubre el alcance y no queda nada
        # por leer; sin esto una pasada sin archivos degradaría a parcial.
        cobertura = review_domain.COMPLETE_CLAIM
    if modelo is not None and not model_ok:
        cobertura = review_domain.PARTIAL
    if (
        resultado.get("subtype") == "error_max_turns"
        and cobertura == review_domain.COMPLETE_CLAIM
    ):
        # El coordinador no mira el subtype: la vuelta de cierre no completa
        # una revisión que agotó el tope, la declare como la declare.
        cobertura = review_domain.PARTIAL
    paquete.update(
        {
            "result": texto,
            "subtype": resultado.get("subtype"),
            "observaciones": (modelo or {}).get("findings", []),
            "cobertura": cobertura,
        }
    )
    for campo in ("total_cost_usd", "usage", "num_turns"):
        if campo in resultado:
            paquete[campo] = resultado[campo]
    paquete["review_provider"] = resultado.get("review_provider") or get_provider()[0]
    paquete["model_ok"] = model_ok
    if datos is not None:
        paquete["manifest"] = {k: datos[k] for k in CAMPOS_DEL_ALCANCE if k in datos}
        if datos.get("head"):
            previo = (
                json.loads((work / "prev.json").read_text())
                if (work / "prev.json").exists()
                else {}
            ).get("covered") or {}
            paquete["covered"] = libro_fusionado(
                previo, datos, resultado, GitRepository(Path.cwd()), model_ok
            )
    if ERROR_KEY in resultado:
        paquete[ERROR_KEY] = redact(resultado[ERROR_KEY], secretos)
    (work / "result.json").write_text(json.dumps(paquete))
    review_github.informar_revision(ERROR_KEY not in paquete)
    resumen = os.environ.get("GITHUB_STEP_SUMMARY")
    if ERROR_KEY in paquete and resumen:
        with open(resumen, "a") as fh:
            aviso = caution_banner(
                paquete[ERROR_KEY], paquete.get("pr_head_sha") or "", has_previous=False
            )
            fh.write(aviso + "\n")


# Lo que el comentario visible necesita del manifest del worker; el diff y el
# contexto se quedan en el worker.
CAMPOS_DEL_ALCANCE = (
    "reviewed",
    "excluded",
    "max_turns",
    "mode",
    "prev_sha",
    "changed_files",
)


EXPLICACION_MAX = 8000


def notice_de_rechazo(cid, efecto):
    kinds = {
        review_domain.RECHAZO_NO_ENTENDIDO: "no-entendido",
        review_domain.RECHAZO_COMANDO_EXTRA: "extra",
        review_domain.RECHAZO_TODO_TARDIO: "todo-tardio",
    }
    if efecto in kinds:
        return review_document.Notice(kinds[efecto], comment_id=cid)
    prefijo = f"{review_domain.RECHAZO_HALLAZGO_INEXISTENTE}: "
    if efecto.startswith(prefijo):
        return review_document.Notice(
            "inexistente", tuple(efecto[len(prefijo):].split(", ")), cid,
        )
    return None


def texto_de_explicacion(artifact):
    """Prosa de una explicación del worker, sin cobertura, bloque ni veredicto.
    Además de neutralizar_marcadores, no puede imitar las secciones que el
    re-render busca (encabezados bajan a ####, también con sangría) ni los
    avisos del programa (una cita `> **Comando no entendido:**` deja de serlo)."""
    texto = leer_cierre(artifact.get("result") or "").prosa
    texto = strip_model_verdict(strip_model_findings_block(texto)).strip()
    texto = re.sub(r"^[ \t]{0,3}#{1,6}[ \t]+", "#### ", texto, flags=re.MULTILINE)
    texto = neutralizar_marcadores(prosa_del_modelo(texto))
    if len(texto) > EXPLICACION_MAX:
        texto = texto[:EXPLICACION_MAX] + "\n\n_(Explicación recortada.)_"
    return texto or "_El revisor no devolvió texto._"
