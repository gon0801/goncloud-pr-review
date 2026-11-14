"""El modo coordinado: el coordinador que converge el PR en cada evento y los pasos del worker que preparan la solicitud y cierran el resultado."""

from dataclasses import dataclass, replace
import html
import json
import os
import re
import sys
import tempfile
from pathlib import Path

from review_commands import leer_comando
from review_config import (
    ERROR_KEY,
    IDENTIDADES,
    PROVIDERS,
    env,
    get_provider,
    politica_de_identidad,
    politica_de_revision,
)
import review_domain
import review_github
from review_render import (
    ALCANCE_INICIO,
    DETALLE_DEL_REVISOR,
    GITHUB_COMMENT_MAX,
    VERDICT_RE,
    aviso_md,
    caution_banner,
    compose,
    cuerpo_publicable,
    insert_caution_banner,
    neutralizar_marcadores,
    prosa_del_modelo,
    redact,
    sections_for,
    strip_model_verdict,
    verdict_for,
)
from review_sticky import (
    COMPLETION_PREFIX,
    COVERED_PREFIX,
    MARKER,
    SHA_PREFIX,
    cobertura_declarada,
    continuacion_terminal,
    libro_de,
    libro_fusionado,
    modelo_utilizable,
    prev_de_memoria,
    sha_hexa,
    sticky_from_comments,
)
from review_domain import (
    leer_cierre,
    parse_model_findings,
    strip_findings_block,
    strip_model_findings_block,
)
from review_context import GitRepository, digest_de_politica


@dataclass
class PublishReceipt:
    generacion: int
    recibo: dict
    trabajo: tuple


@dataclass
class Unconfirmed:
    """Escritura incierta: el siguiente evento relee antes de reintentar."""

    motivo: str


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


def _cuerpo_con_checkpoint(decision):
    encoded = review_domain.encode_snapshot(
        decision.snapshot, review_domain.StorageBudget()
    )
    if isinstance(encoded, review_domain.CapacityExceeded):
        return encoded
    return encoded.block if decision.snapshot.schema == 3 else encoded


def _parchar_aviso(observado, adaptador, banner):
    """El banner CAUTION va al sticky EXISTENTE, best-effort. Sin sticky no
    se crea comentario: un MARKER sin bloque válido secuestraría el sticky
    futuro. Si el PATCH falla, manda el Unconfirmed del llamador."""
    if observado is None:
        return False
    try:
        adaptador.parchar(
            observado["id"],
            insert_caution_banner(observado.get("body") or "", banner),
        )
    except Exception:
        return False
    return True


def _avisar_desborde(desborde, decision, observado, adaptador):
    revision = decision.snapshot.revision
    banner = caution_banner(
        f"memoria de desborde ({desborde.needed} bytes para {desborde.limit}) "
        "conservada; esta versión no la modifica",
        revision.head_sha if revision else "",
        has_previous=True,
    )
    return _parchar_aviso(observado, adaptador, banner)


def _avisar_sin_espacio(decision, observado, adaptador):
    """El comentario no trae el bloque de estado (no cabe el fijo del
    sticky): el checkpoint previo se conserva y el sticky queda visible con
    el aviso. Nunca se escribe un cuerpo sin bloque válido."""
    revision = decision.snapshot.revision
    banner = caution_banner(
        "comentario sin espacio para el estado; el checkpoint previo se "
        "conserva y esta versión no la modifica",
        revision.head_sha if revision else "",
        has_previous=True,
    )
    return _parchar_aviso(observado, adaptador, banner)


def _login_de(comentario):
    user = comentario.get("user")
    if isinstance(user, str):
        return user
    return comentario.get("login") or (user or {}).get("login")


def cuerpo_con_cabecera(bloque, resto):
    """MARKER, sha=, completion= y después el bloque, el orden de compose:
    reviewed_completion exige esas dos líneas justo tras el marcador."""
    lineas = resto.split("\n") if resto else []
    cabecera = []
    while lineas and lineas[0].startswith((SHA_PREFIX, COMPLETION_PREFIX)):
        cabecera.append(lineas.pop(0))
    cuerpo = "\n".join([MARKER, *cabecera, bloque])
    cola = "\n".join(lineas)
    return f"{cuerpo}\n{cola}" if cola else cuerpo


def texto_visible(cuerpo):
    return re.sub(r"<!--.*?-->", "", cuerpo or "", flags=re.S).strip()


def _resto_de(cuerpo):
    """Lo que `visible(bloque, resto)` recibe de un cuerpo: sin el marcador, sin
    el bloque y sin las líneas de estado, que se recalculan en cada escritura."""
    lineas = cuerpo.split("\n")
    resto = "\n".join(lineas[1:]) if lineas and lineas[0] == MARKER else cuerpo
    return "\n".join(
        linea
        for linea in strip_findings_block(resto, last=False).split("\n")
        if not linea.startswith((AVISO_EN_CURSO_PREFIX, AVISO_SIN_REVISION_PREFIX))
    ).strip()


def encadenar(*pasos):
    """Re-renders en orden: cada uno parte del cuerpo que armó el anterior."""

    def armar(bloque, resto):
        cuerpo = pasos[0](bloque, resto)
        for paso in pasos[1:]:
            cuerpo = paso(bloque, _resto_de(cuerpo))
        return cuerpo

    return armar


def publish_checkpoint(decision, observado, adaptador, login=None, visible=None):
    """El sticky gobierna por su PRIMER bloque: la escritura reemplaza ese
    bloque y conserva el resto (la prosa puede citar otros), salvo que
    `visible(bloque, resto)` arme el cuerpo completo. Relee antes de
    escribir (duplicados del bot y checkpoint ya aplicado); una respuesta de
    PATCH/POST perdida devuelve Unconfirmed para que el siguiente paso relea.
    Un Keep no escribe: es un no-op confirmado, igual que crear un sticky que
    no tendría texto visible. La línea de revisión en curso no se arrastra:
    solo estado_visible la vuelve a poner mientras siga pendiente.
    """
    if not isinstance(decision, review_domain.Commit):
        return PublishReceipt(generacion=0, recibo={"sin_cambios": True}, trabajo=())
    encoded = _cuerpo_con_checkpoint(decision)
    if isinstance(encoded, review_domain.CapacityExceeded):
        motivo = "desborde de memoria: se conserva el checkpoint previo"
        if _avisar_desborde(encoded, decision, observado, adaptador):
            motivo += "; aviso publicado en el comentario"
        return Unconfirmed(motivo)
    bloque = encoded
    marcados = [
        c
        for c in adaptador.leer()
        if MARKER in (c.get("body") or "") and (login is None or _login_de(c) == login)
    ]
    if len(marcados) > 1:
        return Unconfirmed("múltiples comentarios con el marcador; escritura detenida")
    carga_observada = review_domain.read_snapshot(
        (observado or {}).get("body") or "", last=False
    )
    if (
        observado is not None
        and isinstance(carga_observada, review_domain.Valid)
        and carga_observada.block == bloque
    ):
        return PublishReceipt(
            generacion=decision.snapshot.generation,
            recibo={"comentario_id": observado.get("id"), "ya_aplicado": True},
            trabajo=decision.work_after_commit,
        )
    if visible is None:
        visible = cuerpo_con_cabecera

    if observado is None:
        cuerpo = visible(bloque, "")
        if not texto_visible(cuerpo):
            return PublishReceipt(
                generacion=0, recibo={"sin_texto_visible": True}, trabajo=()
            )
        if not cuerpo_publicable(cuerpo, bloque):
            return Unconfirmed(
                "el comentario no trae el bloque de estado; no se crea sticky"
            )
        try:
            adaptador.crear(cuerpo)
        except Exception as exc:
            return Unconfirmed(f"respuesta de POST incierta: {exc}")
        creado = next(
            (
                c
                for c in adaptador.leer()
                if MARKER in (c.get("body") or "")
                and (login is None or _login_de(c) == login)
            ),
            None,
        )
        return PublishReceipt(
            generacion=decision.snapshot.generation,
            recibo={"comentario_id": creado.get("id") if creado else None},
            trabajo=decision.work_after_commit,
        )
    cuerpo = visible(bloque, _resto_de(observado.get("body") or ""))
    if not texto_visible(cuerpo):
        # Un sticky que ya existe no puede quedar como un comentario en blanco.
        cuerpo = f"{cuerpo}\n\n{LINEA_SIN_REVISION}"
    if not cuerpo_publicable(cuerpo, bloque):
        motivo = (
            "el comentario no trae el bloque de estado: se conserva el "
            "checkpoint previo"
        )
        if _avisar_sin_espacio(decision, observado, adaptador):
            motivo += "; aviso publicado en el comentario"
        return Unconfirmed(motivo)
    try:
        adaptador.parchar(observado["id"], cuerpo)
    except Exception as exc:
        return Unconfirmed(f"respuesta de PATCH incierta: {exc}")
    return PublishReceipt(
        generacion=decision.snapshot.generation,
        recibo={"comentario_id": observado["id"]},
        trabajo=decision.work_after_commit,
    )


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


def _estado_actual(sticky):
    """Estado migrado a v3 para el coordinador; None sólo con memoria
    inválida o de versión futura (se conserva, como en cmd_publish). Un
    sticky sin bloque de memoria es ausencia real: arranca vacío."""
    if sticky:
        load = review_domain.read_snapshot(sticky["body"])
        if isinstance(load, review_domain.Valid):
            if load.snapshot.schema == 3:
                return load.snapshot
            return review_domain.snapshot_a_v3(load.snapshot)
        if isinstance(load, review_domain.Legacy):
            return review_domain.snapshot_a_v3(load.snapshot)
        if isinstance(load, review_domain.Missing):
            return review_domain.Snapshot(
                schema=3,
                generation=1,
                revision=None,
                next_id=1,
                completion=review_domain.UNKNOWN,
                findings=[],
                command_cursor=0,
            )
        return None
    return review_domain.Snapshot(
        schema=3,
        generation=1,
        revision=None,
        next_id=1,
        completion=review_domain.UNKNOWN,
        findings=[],
        command_cursor=0,
    )


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
    # de revision_visible: un motivo vacío sigue siendo un fallo.
    if ERROR_KEY not in (artifact or {}):
        return None
    return review_domain.ReportFailed(
        request_id=artifact.get("request_id"),
        run_id=run_id,
        attempt=attempt,
        motivo=artifact[ERROR_KEY] or "el worker falló sin motivo",
        retryable=True,
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
    vivas = {r.id: r for r in estado.pending_requests if r.state != "finished"}
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
        fallida = solicitud.state == "failed_retryable" and solicitud.id != rid_avisado
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
    excluidos = (artifact.get("manifest") or {}).get("excluded") or []
    recortes = tuple(
        f"archivo excluido ({e.get('reason')}): {e.get('path')}"
        for e in excluidos
        if e.get("reason") in ("budget", "encoding")
    )
    if not recortes:
        return facts
    return replace(facts, omissions=facts.omissions + recortes)


def _ultimo_comentario(adaptador):
    return max(
        (c["id"] for c in adaptador.leer() if isinstance(c.get("id"), int)),
        default=0,
    )


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
    current = _estado_actual(sticky)
    if current is None:
        print(
            "ai-review: memoria inválida o de versión futura; se conserva sin tocar",
            file=sys.stderr,
        )
        return
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
        except Exception as exc:
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
        AVISOS_DE_RECHAZO[effect](cid)
        for cid, effect in rechazos
        if effect in AVISOS_DE_RECHAZO
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
    except Exception as exc:
        print(
            f"ai-review: no se pudo cosechar el worker ({exc}); "
            "se reintenta en el próximo evento",
            file=sys.stderr,
        )
        entregas, sin_corrida, rechazo = [], [], True
    # Se despacha otra vez; si el run solo tardó en aparecer en la lista, el
    # grupo del worker cancela al anterior y cuenta el más nuevo. Un PR
    # cerrado o en borrador no gasta otra corrida del modelo.
    if _pr_admisible(pr_data, repo):
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
                    solicitud.target.get("head_sha") or head,
                )
            elif artifact is not None and ERROR_KEY not in artifact:
                explicaciones.append(
                    (solicitud.finding_id, texto_de_explicacion(artifact))
                )
        estado = decision.snapshot
    estado = review_domain.marcar_hallazgos_nuevos(
        previo, estado, lambda: _ultimo_comentario(adaptador)
    )
    admision = _admision(target, pr_data, repo)
    if admision is not None:
        admitido = review_domain.reconcile(estado, admision, facts, policy)
        estado = admitido.snapshot
        # Una revisión nueva poda la anterior, que ya no se despacha.
        vivas = {r.id for r in estado.pending_requests}
        trabajo = tuple(w for w in trabajo if w.id in vivas)
        trabajo = (*trabajo, *admitido.work_after_commit)

    if trabajo:
        snapshot_ra, despachables, terminadas = review_domain.reautorizar(
            estado, trabajo, permisos
        )
        estado, trabajo = snapshot_ra or estado, tuple(despachables)
        for terminada in terminadas:
            print(f"ai-review: solicitud {terminada.id} terminada ({terminada.motivo})")

    if sticky is None and admision is None:
        avisos = ()

    if estado == current and not trabajo:
        print("ai-review: nada cambió; el sticky queda como está")
        if rechazo:
            sys.exit(1)
        return

    # Varias explicaciones pueden llegar en la misma cosecha: una por re-render.
    pasos = [estado_visible(estado, e) for e in explicaciones[:-1]]
    pasos.append(estado_visible(estado, (explicaciones or [None])[-1], avisos))
    if revision_entregada is not None:
        artifact, previos, cabeza = revision_entregada
        revision = revision_visible(artifact, estado, previos, cabeza)
        pasos = [revision, *pasos] if avisos or explicaciones else [revision]
    visible = encadenar(*pasos)
    resultado = publish_checkpoint(
        review_domain.Commit(snapshot=estado, work_after_commit=trabajo),
        sticky or None,
        adaptador,
        login=login,
        visible=visible,
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
    current = _estado_actual(sticky)
    if current is None:
        sys.exit(
            "ai-review: memoria inválida o de versión futura; "
            "no se ejecuta la solicitud"
        )
    solicitud = next(
        (
            r
            for r in current.pending_requests
            if r.id == int(args.request_id)
            and r.state in ("pending", "failed_retryable")
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
    secretos = [
        os.environ.get(n, "")
        for n in ("API_KEY", "FALLBACK_API_KEY", "GH_TOKEN", "GITHUB_TOKEN")
    ]
    texto = redact(resultado.get("result") or "", secretos)
    # El directorio de trabajo se sube completo como artifact: las salidas
    # crudas de las vueltas extra llevan el mismo borrado que la respuesta.
    for cruda in ("repair.json", "cierre.json"):
        ruta = work / cruda
        if ruta.exists():
            ruta.write_text(redact(ruta.read_text(), secretos))
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


def revision_visible(artifact, snapshot, previos, head):
    """Arma el comentario del resultado del worker con el mismo render que el
    revisor directo. Un fallo no marca el SHA como revisado: antepone el aviso
    a la revisión anterior."""

    def armar(bloque, resto):
        if ERROR_KEY in artifact:
            banner = caution_banner(artifact[ERROR_KEY], head, has_previous=bool(resto))
            return cuerpo_con_cabecera(
                bloque, insert_caution_banner(resto, banner).rstrip()
            )
        proveedor = artifact.get("review_provider")
        if proveedor not in PROVIDERS:
            proveedor = get_provider()[0]
        manifest = {"reviewed": [], "excluded": [], **(artifact.get("manifest") or {})}
        model_ok = artifact.get("model_ok", True) and forma_utilizable_del_artifact(
            artifact
        )
        libro = artifact.get("covered")
        if not model_ok and libro is not None:
            previo = libro_de(resto)[1] or {}
            libro = {ruta: blob for ruta, blob in libro.items() if previo.get(ruta) == blob}
        return compose(
            artifact,
            manifest,
            sha=head,
            provider=proveedor,
            findings={
                "merged": review_domain.hallazgos_legacy(snapshot),
                "new_ids": [f.id for f in snapshot.findings if f.id not in previos],
                "block": bloque,
                "model_ok": model_ok,
                "completion": snapshot.completion,
                "no_acreditados": review_domain.resoluciones_sin_acreditar(snapshot),
            },
            libro=libro,
            budget=review_domain.StorageBudget(),
        )

    return armar


TITULO_DE_REVISION = "### Revisión automática · "
EXPLICACION_PREFIX = "## Explicación de "
EXPLICACION_MAX = 8000
EXPLICACIONES_VISIBLES = 3
AVISO_COMANDO_PREFIX = aviso_md("Comando no entendido", "")
AVISOS_VISIBLES = 3
AVISO_EN_CURSO_PREFIX = aviso_md("Revisión en curso", "")
AVISO_SIN_REVISION_PREFIX = aviso_md("Sin revisión publicada", "")
LINEA_SIN_REVISION = (
    f"{AVISO_SIN_REVISION_PREFIX}este PR no tiene una revisión pendiente ni "
    "publicada; la pide `ai-review: revisar`, o un push cuando el PR está "
    "abierto y listo para revisión."
)


def linea_en_curso(snapshot):
    """La línea visible de la revisión pendiente, para que un sticky sin
    revisión publicada nunca quede vacío; None sin revisión pendiente."""
    pendiente = next(
        (
            r
            for r in reversed(snapshot.pending_requests)
            if r.kind == "review" and r.state == "pending"
        ),
        None,
    )
    if pendiente is None:
        return None
    head = pendiente.target.get("head_sha", "")
    commit = f"el commit `{head[:7]}`" if sha_hexa(head) else "el PR"
    return (
        f"{AVISO_EN_CURSO_PREFIX}el revisor está revisando {commit}; este "
        "comentario se actualiza cuando termine."
    )


def es_aviso_de_comando(linea):
    """Solo la línea exacta que escribe el programa para un comentario real se
    conserva entre re-renders; cualquier otra que imite el prefijo se descarta."""
    m = re.search(r"el comentario (\d{1,19}) ", linea)
    return bool(m) and any(
        linea == aviso(int(m.group(1))) for aviso in AVISOS_DE_RECHAZO.values()
    )


def aviso_de_comando_no_entendido(cid):
    return (
        f"{AVISO_COMANDO_PREFIX}el comentario {cid} menciona `ai-review:` pero no es un "
        "comando que entienda; cada comando va al inicio de una línea: "
        "`ai-review: descartar F3`, `ai-review: descartar F1, F2`, "
        "`ai-review: descartar todo`, `ai-review: explicar F3` o `ai-review: revisar`."
    )


def aviso_de_hallazgo_inexistente(cid):
    return (
        f"{aviso_md('Comando no aplicado', '')}el comentario {cid} nombra un hallazgo "
        "que no existe; no se aplicó nada."
    )


def aviso_de_comando_extra(cid):
    return (
        f"{aviso_md('Comando no aplicado', '')}el comentario {cid} trae más de un "
        "comando; solo se aplicó el primero. Escribe cada comando en su propio "
        "comentario."
    )


def aviso_de_todo_tardio(cid):
    return (
        f"{aviso_md('Comando no aplicado', '')}`ai-review: descartar todo` del "
        f"comentario {cid} es de antes de la última revisión con hallazgos nuevos y "
        "no se aplicó; si también quieres descartarlos, comenta de nuevo "
        "`ai-review: descartar todo`."
    )


AVISOS_DE_RECHAZO = {
    review_domain.RECHAZO_NO_ENTENDIDO: aviso_de_comando_no_entendido,
    review_domain.RECHAZO_HALLAZGO_INEXISTENTE: aviso_de_hallazgo_inexistente,
    review_domain.RECHAZO_COMANDO_EXTRA: aviso_de_comando_extra,
    review_domain.RECHAZO_TODO_TARDIO: aviso_de_todo_tardio,
}


def texto_de_explicacion(artifact):
    """Prosa de una explicación del worker, sin cobertura, bloque ni veredicto.
    Además de neutralizar_marcadores, no puede imitar las secciones que el
    re-render busca (encabezados bajan a ####, también con sangría) ni los
    avisos del programa (una cita `> **Comando no entendido:**` deja de serlo)."""
    texto = leer_cierre(artifact.get("result") or "").prosa
    texto = strip_model_verdict(strip_model_findings_block(texto)).strip()
    texto = re.sub(r"^[ \t]{0,3}#{1,6}[ \t]+", "#### ", texto, flags=re.M)
    texto = neutralizar_marcadores(prosa_del_modelo(texto))
    if len(texto) > EXPLICACION_MAX:
        texto = texto[:EXPLICACION_MAX] + "\n\n_(Explicación recortada.)_"
    return texto or "_El revisor no devolvió texto._"


def estado_visible(snapshot, explicacion=None, avisos=()):
    """Re-render del comentario cuando un comando o una explicación cambian la
    memoria sin una revisión nueva: veredicto y secciones salen del snapshot;
    los marcadores sha=/completion=, los avisos, el título y el detalle del
    revisor se conservan (nada de esto acredita un SHA). explicacion es
    (finding_id, texto). avisos son líneas de comandos que no se entendieron:
    se ven tras las secciones y se conservan los últimos en los re-renders. Un
    cuerpo sin la forma de una revisión conserva el resto tal cual."""
    merged = review_domain.hallazgos_legacy(snapshot)
    titulos = {f["id"]: f["title"] for f in merged}
    nueva = []
    if explicacion is not None:
        fid, texto = explicacion
        titulo = html.escape(titulos.get(fid, ""), quote=False)
        nueva = [
            f"{EXPLICACION_PREFIX}{fid}" + (f" · {titulo}" if titulo else ""),
            "",
            texto,
        ]

    def armar(bloque, resto):
        lineas = resto.split("\n") if resto else []
        titulo = next(
            (
                i
                for i, linea in enumerate(lineas)
                if linea.startswith(TITULO_DE_REVISION)
            ),
            None,
        )
        detalle = next(
            (i for i, linea in enumerate(lineas) if linea == DETALLE_DEL_REVISOR), None
        )
        veredicto = (
            next(
                (i for i in range(titulo, detalle) if VERDICT_RE.match(lineas[i])),
                None,
            )
            if titulo is not None and detalle is not None and titulo < detalle
            else None
        )
        if veredicto is None:
            cuerpo = cuerpo_con_cabecera(bloque, resto)
            en_curso = linea_en_curso(snapshot)
            return "\n".join(
                [
                    cuerpo,
                    *(["", en_curso] if en_curso else []),
                    *(["", *avisos] if avisos else []),
                    *(["", *nueva] if nueva else []),
                ]
            )[:GITHUB_COMMENT_MAX]
        marcas = (SHA_PREFIX, COMPLETION_PREFIX, COVERED_PREFIX)
        # Los avisos de un cuerpo viejo citan motivos y detalle de cobertura del
        # modelo publicados antes de neutralizarse.
        previo = [
            neutralizar_marcadores(linea)
            for linea in lineas[:titulo]
            if not linea.startswith(marcas)
        ]
        while previo and not previo[0]:
            previo.pop(0)
        while previo and not previo[-1]:
            previo.pop()
        region = lineas[veredicto:detalle]
        nuevos = []
        if "## Nuevos en este push" in region and "## Siguen abiertos" in region:
            tramo = region[
                region.index("## Nuevos en este push") : region.index(
                    "## Siguen abiertos"
                )
            ]
            nuevos = [
                m.group(1) for linea in tramo if (m := re.search(r" · (\S+)$", linea))
            ]
        explicaciones = []
        previos_avisos = []
        for linea in region:
            if linea.startswith(EXPLICACION_PREFIX):
                explicaciones.append([linea])
            elif explicaciones:
                explicaciones[-1].append(linea)
            elif es_aviso_de_comando(linea):
                previos_avisos.append(linea)
        todos_avisos = [a for a in previos_avisos if a not in avisos] + list(avisos)
        if nueva:
            explicaciones = [
                e
                for e in explicaciones
                if e[0].split(" · ")[0] != nueva[0].split(" · ")[0]
            ]
            explicaciones.append(nueva)
        conservadas = [linea for linea in lineas[:titulo] if linea.startswith(marcas)]
        # Con un aviso previo, la forma de revision_visible ante un fallo: el
        # aviso tras la cabecera, para que el próximo fallo lo reemplace.
        cabecera = [MARKER, *conservadas, bloque, *previo, *([""] if previo else [])]
        cuerpo = [
            *cabecera,
            lineas[titulo],
            *(
                neutralizar_marcadores(linea)
                for linea in lineas[titulo + 1 : veredicto]
            ),
            verdict_for(merged),
            "",
            *sections_for(
                merged, nuevos, review_domain.resoluciones_sin_acreditar(snapshot)
            ),
        ]
        for aviso in todos_avisos[-AVISOS_VISIBLES:]:
            cuerpo += ["", aviso]
        for e in explicaciones[-EXPLICACIONES_VISIBLES:]:
            while e and not e[-1]:
                e = e[:-1]
            cuerpo += ["", *e]
        # La prosa del detalle de un cuerpo publicado antes de neutralizarse
        # tampoco puede llevar marcadores vivos al cuerpo nuevo. El alcance real
        # es el último: la prosa vieja puede traer una línea idéntica.
        alcance = max(
            (i for i in range(detalle, len(lineas)) if lineas[i] == ALCANCE_INICIO),
            default=len(lineas),
        )
        # Dentro del Alcance las rutas salen del PR: solo su <details> de apertura
        # y su </details> de cierre quedan vivos.
        cierre = max(
            (i for i in range(alcance, len(lineas)) if lineas[i] == "</details>"),
            default=None,
        )
        if alcance < len(lineas) and cierre is not None:
            cola = [
                lineas[alcance],
                *(
                    neutralizar_marcadores(linea)
                    for linea in lineas[alcance + 1 : cierre]
                ),
                lineas[cierre],
                *(neutralizar_marcadores(linea) for linea in lineas[cierre + 1 :]),
            ]
        else:
            cola = [neutralizar_marcadores(linea) for linea in lineas[alcance:]]
        cuerpo += [
            "",
            lineas[detalle],
            *(neutralizar_marcadores(linea) for linea in lineas[detalle + 1 : alcance]),
            *cola,
        ]
        return "\n".join(cuerpo)[:GITHUB_COMMENT_MAX]

    return armar
