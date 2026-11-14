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

    def worker(self, rid, artifact=None, run_id=900, estado="completed"):
        self.corridas.append(
            {
                "id": run_id,
                "display_title": f"PR 12 solicitud {rid}",
                "path": WORKER_PATH,
                "head_branch": "main",
                "head_sha": MAIN,
                "run_attempt": 1,
                "status": estado,
                "conclusion": "success" if estado == "completed" else None,
                "event": "workflow_dispatch",
            }
        )
        if artifact is not None:
            self.artefactos[run_id] = artifact

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
        "result": f"Revisión.\n<!-- ai-review:findings={bloque} -->\nCOVERAGE: complete",
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
            pares = [args[i + 1] for i, a in enumerate(args) if a == "-f"]
            despachos.append(dict(p.split("=", 1) for p in pares))
            return mock.Mock(stdout="")
        ruta = args[2]
        if ruta.endswith("/pulls/12"):
            return mock.Mock(stdout=json.dumps(github.pr()))
        if "/commits/" in ruta:
            return mock.Mock(stdout=json.dumps({"sha": MAIN}))
        for corrida in github.corridas:
            if ruta.endswith(f"/actions/runs/{corrida['id']}/attempts/1"):
                return mock.Mock(stdout=json.dumps(corrida))
        raise AssertionError(f"llamada inesperada: {args}")

    with tempfile.TemporaryDirectory() as tmp:
        evento_json = Path(tmp, "event.json")
        evento_json.write_text(json.dumps({"action": "created"}))
        if aviso and aviso[0] in github.artefactos:
            Path(tmp, "result.json").write_text(json.dumps(github.artefactos[aviso[0]]))
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
        github = GitHubFalso()
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
        codigo, _, _ = correr(github, "workflow_dispatch", permiso=sin_respuesta, aviso=(900, 1))
        self.assertEqual(codigo, 0)
        self.assertEqual(estados(github), {"F1": "StatusOpen", "F2": "StatusOpen"})

        github.comentar(30, "ai-review: descartar F1")
        codigo, _, _ = correr(github, "issue_comment")
        self.assertEqual(codigo, 0)
        self.assertEqual(
            estados(github), {"F1": "StatusDismissed", "F2": "StatusOpen"}
        )
        self.assertEqual(github.estado().command_cursor, 30)
        self.assertIn("descartar todo", github.visible())
        self.assertIn("comentario 20", github.visible())


if __name__ == "__main__":
    unittest.main()
