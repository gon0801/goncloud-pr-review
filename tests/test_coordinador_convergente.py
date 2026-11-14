"""Cada run del coordinador converge el PR completo, sin importar el evento.

GitHub guarda un run en curso y uno pendiente por grupo; el tercero reemplaza
al pendiente. En producción eso dejó solicitudes `pending` para siempre con un
sticky invisible, y 67 stickies sin texto en PRs que solo recibieron un "LGTM".
"""

import argparse
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import review  # noqa: E402
import review_domain as domain  # noqa: E402

A, B, BASE, MAIN = "a" * 40, "e" * 40, "b" * 40, "f" * 40
BOT = "github-actions[bot]"
DIGEST = review.digest_de_politica(review.politica_de_revision())
WORKER_PATH = ".github/workflows/ai-review-worker.yml"


class GitHubFalso:
    """Comentarios, el PR vivo y los runs del worker de un PR, como los ve la
    API; registra lo que el coordinador escribe y despacha."""

    def __init__(self, comentarios=(), head=A, draft=False, abierto=True):
        self.comentarios = [dict(c) for c in comentarios]
        self.head, self.draft, self.abierto = head, draft, abierto
        self.repo_del_head = "o/r"
        self.escrituras = 0
        self.creados = 0
        self.corridas = []
        self.artefactos = {}
        self.despacho_falla = False

    def leer(self):
        return [dict(c) for c in self.comentarios]

    def parchar(self, cid, body):
        self.escrituras += 1
        for c in self.comentarios:
            if c["id"] == cid:
                c["body"] = body

    def crear(self, body):
        self.escrituras += 1
        self.creados += 1
        cid = max([c["id"] for c in self.comentarios] + [100]) + 1
        self.comentarios.append({"id": cid, "body": body, "user": BOT})

    def comentar(self, cid, texto, user="jefe"):
        self.comentarios.append(
            {
                "id": cid,
                "body": texto,
                "user": user,
                "created_at": "2026-11-01T10:00:00Z",
                "updated_at": "2026-11-01T10:00:00Z",
            }
        )

    def sticky(self):
        s = review.sticky_from_comments(self.leer(), BOT)
        return s["body"] if s else None

    def estado(self):
        return domain.read_snapshot(self.sticky()).snapshot

    def visible(self):
        return re.sub(r"<!--.*?-->", "", self.sticky() or "", flags=re.S).strip()

    def worker(
        self,
        rid,
        artifact=None,
        run_id=900,
        estado="completed",
        pr=12,
        rama="main",
        intento=1,
    ):
        self.corridas.append(
            {
                "id": run_id,
                "display_title": f"PR {pr} solicitud {rid}",
                "path": WORKER_PATH,
                "head_branch": rama,
                "head_sha": MAIN,
                "run_attempt": intento,
                "status": estado,
                "conclusion": "failure" if estado == "completed" else None,
                "event": "workflow_dispatch",
            }
        )
        if artifact is not None:
            self.artefactos[run_id] = (
                f"ai-review-result-request-{rid}-run-{run_id}"
                f"-attempt-{artifact['attempt']}",
                artifact,
            )

    def despachado(self, rid):
        """Un despacho crea un run del worker, que queda en curso."""
        self.worker(rid, run_id=500 + len(self.corridas), estado="in_progress")

    def pr(self):
        return {
            "head": {"sha": self.head, "repo": {"full_name": self.repo_del_head}},
            "base": {"sha": BASE},
            "draft": self.draft,
            "state": "open" if self.abierto else "closed",
        }


def hallazgo(n, titulo=None):
    return domain.Finding(
        id=f"F{n}",
        title=titulo or f"Fuga {n}",
        severity="High",
        status=domain.StatusOpen(),
        primary_anchor=domain.AnchorLegacy(path="a.py", line=n),
    )


def revisado(head=A, hallazgos=(), **campos):
    return domain.Snapshot(
        schema=3,
        generation=2,
        revision=domain.Revision(base_sha=BASE, head_sha=head, policy_digest=DIGEST),
        next_id=len(hallazgos) + 1,
        completion=domain.COMPLETE_CLAIM,
        findings=list(hallazgos),
        command_cursor=0,
        **campos,
    )


def resultado(rid, head, titulos=(), run_id=900):
    """El artifact que sube el worker para la solicitud rid."""
    bloque = json.dumps(
        {
            "findings": [
                {"title": t, "severity": "High", "file": "a.py", "line": n}
                for n, t in enumerate(titulos, 1)
            ]
        }
    )
    return {
        "request_id": rid,
        "kind": "review",
        "run_id": run_id,
        "attempt": 1,
        "pr_head_sha": head,
        "base_sha": BASE,
        "policy_digest": DIGEST,
        "finding_identity": "current",
        "result": (
            f"Revisión.\n<!-- ai-review:findings={bloque} -->\nCOVERAGE: complete"
        ),
        "observaciones": [],
        "cobertura": domain.COMPLETE_CLAIM,
        "model_ok": True,
        "review_provider": "opencode-go",
        "manifest": {"reviewed": ["a.py"], "excluded": []},
    }


def estados(github):
    return {f.id: type(f.status).__name__ for f in github.estado().findings}


def sticky_con(estado, visible=""):
    bloque = domain.encode_snapshot(estado).block
    return {"id": 7, "body": review.cuerpo_con_cabecera(bloque, visible), "user": BOT}


def correr(github, evento, head=None, permiso="write", aviso=None):
    """El cmd_reconcile real; devuelve (salida, entradas despachadas, stderr)."""
    despachos = []
    head = head or github.head

    def sh_falso(*args, **kw):
        if args[:3] == ("gh", "workflow", "run"):
            if github.despacho_falla:
                raise subprocess.CalledProcessError(1, args)
            pares = [args[i + 1] for i, a in enumerate(args) if a == "-f"]
            despachos.append(dict(p.split("=", 1) for p in pares))
            github.despachado(int(despachos[-1]["request_id"]))
            return mock.Mock(stdout="")
        if args[:3] == ("gh", "run", "download"):
            nombre, artifact = github.artefactos[int(args[3])]
            opciones = dict(zip(args[4::2], args[5::2]))
            assert opciones["--name"] == nombre, opciones
            Path(opciones["--dir"]).mkdir(parents=True, exist_ok=True)
            Path(opciones["--dir"], "result.json").write_text(json.dumps(artifact))
            return mock.Mock(stdout="")
        ruta = args[2]
        if "/actions/workflows/ai-review-worker.yml/runs" in ruta:
            corridas = sorted(github.corridas, key=lambda c: -c["id"])
            return mock.Mock(stdout=json.dumps({"workflow_runs": corridas}))
        if re.search(r"/actions/runs/\d+/artifacts", ruta):
            run_id = int(re.search(r"/actions/runs/(\d+)/", ruta).group(1))
            listado = [
                {"id": 1, "name": github.artefactos[run_id][0], "expired": False}
            ] if run_id in github.artefactos else []
            return mock.Mock(stdout=json.dumps({"artifacts": listado}))
        if ruta.endswith("/pulls/12"):
            return mock.Mock(stdout=json.dumps(github.pr()))
        if "/commits/" in ruta:
            return mock.Mock(stdout=json.dumps({"sha": MAIN}))
        raise AssertionError(f"llamada inesperada: {args}")

    with tempfile.TemporaryDirectory() as tmp:
        evento_json = Path(tmp, "event.json")
        evento_json.write_text(json.dumps({"action": "created"}))
        entorno = {
            "REPO": "o/r",
            "PR_NUMBER": "12",
            "WORKER_REF": "main",
            "BOT_LOGIN": BOT,
            "GITHUB_RUN_ID": "55",
            "GITHUB_EVENT_NAME": evento,
            "GITHUB_EVENT_PATH": str(evento_json),
            "HEAD_SHA": head if evento == "pull_request_target" else "",
            "BASE_SHA": BASE if evento == "pull_request_target" else "",
            "WORKER_RUN_ID": str(aviso[0]) if aviso else "",
            "WORKER_ATTEMPT": str(aviso[1]) if aviso else "",
        }
        codigo, err = 0, io.StringIO()
        permisos = permiso if callable(permiso) else (lambda repo, login: permiso)
        with (
            mock.patch.dict(os.environ, entorno, clear=False),
            mock.patch.object(review, "ComentariosGh", return_value=github),
            mock.patch.object(review, "sh", side_effect=sh_falso),
            mock.patch.object(review, "collaborator_permission", side_effect=permisos),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(err),
        ):
            try:
                review.cmd_reconcile(argparse.Namespace(work=tmp))
            except SystemExit as salida:
                codigo = salida.code or 0
    return codigo, despachos, err.getvalue()


class StickyNuncaVacio(unittest.TestCase):
    def test_comentario_sin_comando_en_pr_sin_sticky_no_crea_comentario(self):
        github = GitHubFalso(draft=True)
        github.comentar(20, "LGTM", user="cualquiera")
        codigo, despachos, _ = correr(github, "issue_comment", permiso="read")
        self.assertEqual((codigo, despachos), (0, []))
        self.assertEqual(github.creados, 0, "un sticky sin texto visible no se crea")

    def test_el_primer_push_deja_una_linea_visible_mientras_corre_el_worker(self):
        github = GitHubFalso()
        codigo, despachos, _ = correr(github, "pull_request_target")
        self.assertEqual(codigo, 0)
        self.assertEqual([d["request_id"] for d in despachos], ["1"])
        self.assertIn("Revisión en curso", github.visible())
        self.assertIn(A[:7], github.visible())

    def test_un_sticky_que_pierde_su_revision_pendiente_sigue_con_texto(self):
        # Un push a un PR en borrador mientras corre el worker: el resultado
        # llega vencido y la admisión no pide otra revisión.
        github = GitHubFalso()
        correr(github, "pull_request_target")
        github.head, github.draft = B, True
        github.worker(1, resultado(1, A))
        codigo, despachos, _ = correr(github, "workflow_dispatch", aviso=(900, 1))
        self.assertEqual((codigo, despachos), (0, []))
        self.assertEqual(github.estado().pending_requests, [])
        self.assertIn("Sin revisión publicada", github.visible())
        self.assertNotIn("Revisión en curso", github.visible())

        github.draft = False
        correr(github, "workflow_dispatch", aviso=(900, 1))
        github.comentar(20, "ai-review: revisar")
        correr(github, "issue_comment")
        self.assertNotIn("Sin revisión publicada", github.visible())
        self.assertEqual(github.visible().count("Revisión en curso"), 1)

    def test_la_linea_en_curso_no_se_repite_ni_queda_tras_el_resultado(self):
        github = GitHubFalso()
        correr(github, "pull_request_target")
        correr(github, "pull_request_target", head=B)
        self.assertEqual(github.visible().count("Revisión en curso"), 1)
        self.assertIn(B[:7], github.visible())
        self.assertNotIn(A[:7], github.visible())


def solo_jefe(repo, login):
    return "write" if login == "jefe" else "read"


def sin_respuesta(repo, login):
    return None


class ComandosEnCualquierEvento(unittest.TestCase):
    def test_el_comando_de_un_run_perdido_se_aplica_en_el_push_siguiente(self):
        github = GitHubFalso([sticky_con(revisado(A, [hallazgo(1), hallazgo(2)]))])
        github.comentar(20, "ai-review: descartar F1")
        github.comentar(21, "ai-review: descartar F2", user="visitante")
        codigo, _, _ = correr(github, "pull_request_target", head=B, permiso=solo_jefe)
        self.assertEqual(codigo, 0)
        self.assertEqual(
            estados(github), {"F1": "StatusDismissed", "F2": "StatusOpen"}
        )
        self.assertEqual(github.estado().command_cursor, 21)

    def test_el_mismo_comando_se_aplica_una_vez_aunque_el_run_se_repita(self):
        github = GitHubFalso([sticky_con(revisado(A, [hallazgo(1), hallazgo(2)]))])
        github.comentar(20, "ai-review: descartar F1")
        correr(github, "issue_comment")
        escrituras, recibos = github.escrituras, github.estado().receipts
        correr(github, "issue_comment")
        self.assertEqual(github.escrituras, escrituras, "nada cambió: no escribe")
        correr(github, "pull_request_target", head=B)
        self.assertEqual(github.estado().receipts, recibos)
        self.assertEqual(
            estados(github), {"F1": "StatusDismissed", "F2": "StatusOpen"}
        )

    def test_descartar_todo_retenido_no_alcanza_hallazgos_nuevos(self):
        github = GitHubFalso([sticky_con(revisado(A, [hallazgo(1)]))])
        github.comentar(20, "ai-review: descartar todo")
        correr(github, "issue_comment", permiso=sin_respuesta)
        correr(github, "pull_request_target", head=B, permiso=sin_respuesta)
        rid = github.estado().pending_requests[0].id
        github.head = B
        github.worker(rid, resultado(rid, B, ["Fuga 1", "Inyección SQL nueva"]))
        codigo, _, _ = correr(
            github, "workflow_dispatch", permiso=sin_respuesta, aviso=(900, 1)
        )
        self.assertEqual(codigo, 0)
        self.assertEqual(estados(github), {"F1": "StatusOpen", "F2": "StatusOpen"})

        github.comentar(30, "ai-review: descartar F1")
        codigo, _, _ = correr(github, "issue_comment")
        self.assertEqual(codigo, 0)
        self.assertEqual(
            estados(github), {"F1": "StatusDismissed", "F2": "StatusOpen"}
        )
        self.assertEqual(github.estado().command_cursor, 30)
        aviso = review.aviso_de_todo_tardio(20)
        self.assertIn(aviso, github.sticky())
        github.comentar(31, "ai-review: descartar F2")
        correr(github, "issue_comment")
        self.assertIn(aviso, github.sticky(), "el aviso sobrevive al re-render")


def revisiones(despachos):
    return [(d["kind"], d["head_sha"]) for d in despachos]


class AdmisionDelHeadVivo(unittest.TestCase):
    def test_un_push_perdido_lo_admite_el_siguiente_comentario(self):
        github = GitHubFalso([sticky_con(revisado(A, [hallazgo(1)]))], head=B)
        github.comentar(20, "ai-review: descartar F1")
        codigo, despachos, _ = correr(github, "issue_comment")
        self.assertEqual(codigo, 0)
        self.assertEqual(revisiones(despachos), [("review", B)])
        _, otra_vez, _ = correr(github, "issue_comment")
        self.assertEqual(otra_vez, [], "la solicitud de B ya está pendiente")

    def test_repetir_un_push_o_reabrir_un_head_revisado_no_despacha_otra_vez(self):
        github = GitHubFalso()
        _, primero, _ = correr(github, "pull_request_target")
        _, repetido, _ = correr(github, "pull_request_target")
        self.assertEqual(revisiones(primero + repetido), [("review", A)])
        ya_revisado = GitHubFalso([sticky_con(revisado(A, [hallazgo(1)]))])
        _, reabierto, _ = correr(ya_revisado, "pull_request_target")
        self.assertEqual(reabierto, [])
        self.assertEqual(ya_revisado.escrituras, 0)

    def test_un_despacho_que_fallo_se_repite_en_el_siguiente_evento(self):
        github = GitHubFalso()
        github.despacho_falla = True
        with self.assertRaises(subprocess.CalledProcessError):
            correr(github, "pull_request_target")
        self.assertEqual(
            [(r.id, r.state) for r in github.estado().pending_requests],
            [(1, "pending")],
        )
        github.despacho_falla = False
        _, repetido, _ = correr(github, "issue_comment")
        self.assertEqual([d["request_id"] for d in repetido], ["1"])
        _, otra_vez, _ = correr(github, "issue_comment")
        self.assertEqual(otra_vez, [], "el run de la solicitud 1 ya está en curso")
        self.assertEqual(github.estado().request_count, 1)

    def test_repetir_el_push_reintenta_una_revision_que_fallo(self):
        github = GitHubFalso()
        correr(github, "pull_request_target")
        github.worker(1)
        correr(github, "workflow_dispatch", aviso=(900, 1))
        _, comentario, _ = correr(github, "issue_comment")
        self.assertEqual(
            comentario, [], "solo un push reintenta: el aviso del worker haría un ciclo"
        )
        _, repetido, _ = correr(github, "pull_request_target")
        self.assertEqual(revisiones(repetido), [("review", A)])
        self.assertEqual(
            [(r.id, r.state) for r in github.estado().pending_requests],
            [(2, "pending")],
        )
        self.assertIn("Revisión en curso", github.visible())

    def test_un_comentario_no_admite_borradores_cerrados_ni_forks(self):
        casos = {
            "borrador": GitHubFalso(draft=True),
            "cerrado": GitHubFalso(abierto=False),
            "fork": GitHubFalso(),
        }
        casos["fork"].repo_del_head = "otro/r"
        for nombre, github in casos.items():
            with self.subTest(pr=nombre):
                github.comentar(20, "ai-review: descartar F1")
                codigo, despachos, _ = correr(github, "issue_comment")
                self.assertEqual((codigo, despachos, github.creados), (0, [], 0))

    def test_un_pr_cerrado_o_en_borrador_no_repite_un_despacho_perdido(self):
        for nombre, cambio in (("cerrado", {"abierto": False}), ("borrador", {"draft": True})):
            with self.subTest(pr=nombre):
                github = GitHubFalso()
                github.despacho_falla = True
                with self.assertRaises(subprocess.CalledProcessError):
                    correr(github, "pull_request_target")
                github.despacho_falla = False
                for campo, valor in cambio.items():
                    setattr(github, campo, valor)
                github.comentar(20, "ai-review: descartar todo")
                codigo, despachos, _ = correr(github, "issue_comment")
                self.assertEqual((codigo, despachos), (0, []))

    def test_revisar_reemplaza_la_revision_pendiente(self):
        github = GitHubFalso()
        correr(github, "pull_request_target")
        github.comentar(20, "ai-review: revisar")
        _, despachos, _ = correr(github, "issue_comment")
        pendientes = github.estado().pending_requests
        self.assertEqual([(r.id, r.origin) for r in pendientes], [(2, "comando")])
        self.assertEqual([d["request_id"] for d in despachos], ["2"])

    def test_un_resultado_vencido_no_se_acredita_y_se_admite_el_head_vivo(self):
        github = GitHubFalso()
        correr(github, "pull_request_target")
        github.head = B
        github.worker(1, resultado(1, A, ["Fuga vieja"]))
        codigo, despachos, _ = correr(github, "workflow_dispatch", aviso=(900, 1))
        self.assertEqual(codigo, 0)
        self.assertIsNone(github.estado().revision)
        self.assertEqual(revisiones(despachos), [("review", B)])

    def test_una_explicacion_vencida_no_vuelve_a_despachar_la_revision(self):
        github = GitHubFalso([sticky_con(revisado(A, [hallazgo(1)]))])
        github.comentar(20, "ai-review: explicar F1")
        correr(github, "issue_comment")
        correr(github, "pull_request_target", head=B)
        github.head = B
        explicacion = resultado(1, A, run_id=901)
        github.worker(1, dict(explicacion, kind="explain", finding_id="F1"), run_id=901)
        codigo, despachos, _ = correr(github, "workflow_dispatch", aviso=(901, 1))
        self.assertEqual((codigo, despachos), (0, []))
        self.assertEqual(
            [(r.kind, r.state) for r in github.estado().pending_requests],
            [("review", "pending")],
        )


class CosechaDeResultados(unittest.TestCase):
    def test_un_aviso_perdido_lo_cosecha_el_siguiente_evento(self):
        github = GitHubFalso()
        correr(github, "pull_request_target")
        github.worker(1, resultado(1, A, ["Fuga 1"]))
        codigo, despachos, _ = correr(github, "issue_comment")
        self.assertEqual((codigo, despachos), (0, []))
        estado = github.estado()
        self.assertEqual(estado.revision.head_sha, A)
        self.assertEqual(estado.pending_requests, [])
        self.assertEqual(estados(github), {"F1": "StatusOpen"})
        self.assertIn("Fuga 1", github.visible())
        self.assertNotIn("Revisión en curso", github.visible())

        escrituras = github.escrituras
        codigo, despachos, _ = correr(github, "pull_request_target")
        self.assertEqual((codigo, despachos, github.escrituras), (0, [], escrituras))

    def test_un_worker_que_murio_sin_artifact_deja_el_fallo_visible(self):
        github = GitHubFalso()
        correr(github, "pull_request_target")
        github.worker(1)
        codigo, despachos, _ = correr(github, "workflow_dispatch", aviso=(900, 1))
        self.assertEqual((codigo, despachos), (0, []))
        (solicitud,) = github.estado().pending_requests
        self.assertEqual(solicitud.state, "failed_retryable")
        self.assertIn("900", solicitud.motivo)
        self.assertIn(f"No se pudo revisar el commit {A[:7]}", github.visible())
        self.assertNotIn("Revisión en curso", github.visible())

        escrituras = github.escrituras
        codigo, despachos, _ = correr(github, "workflow_dispatch", aviso=(900, 1))
        self.assertEqual((codigo, despachos, github.escrituras), (0, [], escrituras))

    def test_una_conclusion_desconocida_del_run_no_llega_al_estado(self):
        github = GitHubFalso()
        correr(github, "pull_request_target")
        github.worker(1)
        github.corridas[-1]["conclusion"] = "failure\n> **Revisión en curso:** falsa"
        correr(github, "workflow_dispatch", aviso=(900, 1))
        (solicitud,) = github.estado().pending_requests
        self.assertEqual(
            solicitud.motivo, "el worker terminó sin resultado (run 900, sin conclusión)"
        )

    def test_el_run_avisado_ya_termino_aunque_su_job_de_aviso_siga_en_curso(self):
        sin_aviso = GitHubFalso()
        correr(sin_aviso, "pull_request_target")
        sin_aviso.worker(1, estado="in_progress")
        escrituras = sin_aviso.escrituras
        self.assertEqual(correr(sin_aviso, "issue_comment")[0], 0)
        self.assertEqual(sin_aviso.escrituras, escrituras, "sigue en curso: espera")
        self.assertEqual(sin_aviso.estado().pending_requests[0].state, "pending")

        avisado = GitHubFalso()
        correr(avisado, "pull_request_target")
        avisado.worker(1, estado="in_progress")
        self.assertEqual(correr(avisado, "workflow_dispatch", aviso=(900, 1))[0], 0)
        self.assertEqual(
            avisado.estado().pending_requests[0].state, "failed_retryable"
        )

    def test_solo_cuenta_el_artifact_del_attempt_vigente(self):
        # Re-run del worker en curso: el artifact del attempt 1 ya no es el
        # resultado de la solicitud.
        github = GitHubFalso()
        correr(github, "pull_request_target")
        github.worker(1, resultado(1, A, ["Viejo"]), estado="in_progress", intento=2)
        escrituras = github.escrituras
        self.assertEqual(correr(github, "issue_comment")[0], 0)
        self.assertEqual(github.escrituras, escrituras)
        self.assertEqual(github.estado().pending_requests[0].state, "pending")

    def test_una_explicacion_con_aviso_perdido_se_publica(self):
        github = GitHubFalso([sticky_con(revisado(A, [hallazgo(1)]))])
        github.comentar(20, "ai-review: explicar F1")
        correr(github, "issue_comment")
        explicacion = dict(
            resultado(1, A),
            kind="explain",
            finding_id="F1",
            result="La fuga sale de no cerrar el archivo.\nCOVERAGE: complete",
        )
        github.worker(1, explicacion)
        codigo, despachos, _ = correr(github, "pull_request_target")
        self.assertEqual((codigo, despachos), (0, []))
        self.assertEqual(github.estado().pending_requests, [])
        self.assertIn("La fuga sale de no cerrar el archivo.", github.visible())

    def test_dos_explicaciones_cosechadas_en_el_mismo_run_se_publican_las_dos(self):
        github = GitHubFalso([sticky_con(revisado(A, [hallazgo(1), hallazgo(2)]))])
        github.comentar(20, "ai-review: explicar F1")
        github.comentar(21, "ai-review: explicar F2")
        correr(github, "issue_comment")
        for rid, texto in ((1, "No se cierra el archivo."), (2, "Falta el timeout.")):
            explicacion = dict(
                resultado(rid, A, run_id=900 + rid),
                kind="explain",
                finding_id=f"F{rid}",
                result=f"{texto}\nCOVERAGE: complete",
            )
            github.worker(rid, explicacion, run_id=900 + rid)
        codigo, despachos, _ = correr(github, "pull_request_target")
        self.assertEqual((codigo, despachos), (0, []))
        self.assertEqual(github.estado().pending_requests, [])
        self.assertIn("No se cierra el archivo.", github.visible())
        self.assertIn("Falta el timeout.", github.visible())

    def test_corridas_ajenas_no_se_cosechan(self):
        github = GitHubFalso()
        correr(github, "pull_request_target")
        github.worker(1, resultado(1, A, ["De otro PR"]), run_id=901, pr=13)
        github.worker(
            1, resultado(1, A, ["De otra rama"], run_id=902), run_id=902, rama="ataque"
        )
        escrituras = github.escrituras
        codigo, despachos, err = correr(github, "issue_comment")
        self.assertEqual((codigo, despachos), (1, []), "el run de otra rama se ve")
        self.assertIn("ref del run incorrecta", err)
        self.assertEqual(github.escrituras, escrituras)
        self.assertEqual(github.estado().pending_requests[0].state, "pending")

    def test_un_artifact_que_no_autentica_no_se_publica_y_el_run_queda_rojo(self):
        github = GitHubFalso()
        correr(github, "pull_request_target")
        github.worker(1, resultado(1, B, ["Head equivocado"]))
        escrituras = github.escrituras
        codigo, despachos, err = correr(github, "issue_comment")
        self.assertEqual((codigo, despachos), (1, []))
        self.assertIn("rechazado", err)
        self.assertEqual(github.escrituras, escrituras)
        self.assertEqual(github.estado().pending_requests[0].state, "pending")


class Idempotencia(unittest.TestCase):
    def _escenario(self):
        """Un push perdido a B, el resultado de su revisión sin aviso y un
        descarte cuyo run también se perdió."""
        github = GitHubFalso([sticky_con(revisado(A, [hallazgo(1)]))])
        correr(github, "pull_request_target", head=B)
        github.head = B
        github.comentar(20, "ai-review: descartar F1")
        github.worker(1, resultado(1, B, ["Fuga 1", "Fuga nueva"]))
        return github

    def test_el_orden_de_los_eventos_no_cambia_el_estado_final(self):
        comentario, aviso = ("issue_comment", None), ("workflow_dispatch", (900, 1))
        ordenes = {
            "comentario y aviso": [comentario, aviso],
            "aviso y comentario": [aviso, comentario],
            "aviso repetido": [aviso] * 3,
            "push repetido": [("pull_request_target", None)] * 2,
        }
        finales = {}
        for nombre, eventos in ordenes.items():
            github = self._escenario()
            despachos = []
            for evento, avisado in eventos:
                codigo, despachado, _ = correr(github, evento, aviso=avisado)
                self.assertEqual(codigo, 0, nombre)
                despachos += despachado
            self.assertEqual(despachos, [], f"{nombre}: nada que re-despachar")
            finales[nombre] = (github.estado(), github.visible())
        estado, visible = finales.pop("comentario y aviso")
        self.assertEqual(
            {f.id: type(f.status).__name__ for f in estado.findings},
            {"F1": "StatusDismissed", "F2": "StatusOpen"},
        )
        self.assertEqual(estado.revision.head_sha, B)
        for nombre, final in finales.items():
            self.assertEqual(final, (estado, visible), nombre)


if __name__ == "__main__":
    unittest.main()
