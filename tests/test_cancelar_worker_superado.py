"""Un push nuevo cancela el worker que revisaba el head anterior del mismo PR.

Producción, tres días: 33 de 150 runs del worker en un repo y 24 de 150 en
otro siguieron corriendo el modelo tras un run más nuevo del mismo PR, y cada
resultado terminó en un run rojo del coordinador (solicitud ya podada).
"""

import argparse
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import review  # noqa: E402
import review_domain as domain  # noqa: E402

WORKER = (ROOT / "templates" / "ai-review-worker.yml").read_text()
A, B, BASE, MAIN = "a" * 40, "e" * 40, "b" * 40, "f" * 40
BOT = "github-actions[bot]"
ESTADOS = ("always", "cancelled", "success", "failure")


def _case(*args):
    *pares, defecto = args
    for predicado, valor in zip(pares[::2], pares[1::2]):
        if predicado:
            return valor
    return defecto


def _evaluar(expr, contexto):
    """Subconjunto de expresiones de Actions que usan estas plantillas."""
    expr = expr.strip()
    if expr.startswith("${{") and expr.endswith("}}"):
        expr = expr[3:-2]
    python = re.sub(r"!(?!=)", " not ", expr)
    python = python.replace("&&", " and ").replace("||", " or ")
    return eval(python, {"__builtins__": {}}, {"case": _case, **contexto})


def _entradas_declaradas():
    bloque = WORKER.split("    inputs:\n", 1)[1].split("\n\n", 1)[0]
    return set(re.findall(r"^      (\w+):$", bloque, re.M))


def _grupo_y_cancelacion(entradas):
    m = re.search(
        r"^concurrency:\n  group: (.+)\n  cancel-in-progress: (.+)$", WORKER, re.M
    )
    contexto = {
        "github": SimpleNamespace(repository="o/r"),
        "inputs": SimpleNamespace(
            **{**dict.fromkeys(_entradas_declaradas(), ""), **entradas}
        ),
    }
    grupo = re.sub(
        r"\$\{\{(.+?)\}\}",
        lambda e: str(_evaluar(e.group(1), contexto)),
        m.group(1),
    )
    cancela = m.group(2).strip()
    if cancela in ("true", "false"):
        return grupo, cancela == "true"
    return grupo, bool(_evaluar(cancela, contexto))


def _avisar_corre(conclusion):
    """El `if` del job avisar con la conclusión del run; sin función de estado,
    Actions antepone success()."""
    expr = re.search(r"^  avisar:\n(?:    .*\n)*?    if: (.+)$", WORKER, re.M)
    expr = expr.group(1) if expr else "success()"
    if not any(f"{nombre}()" in expr for nombre in ESTADOS):
        expr = f"success() && ({expr})"
    funciones = {
        "always": lambda: True,
        "cancelled": lambda: conclusion == "cancelled",
        "success": lambda: conclusion == "success",
        "failure": lambda: conclusion == "failure",
    }
    return bool(_evaluar(expr, funciones))


class GitHubFalso:
    def __init__(self, comentarios=()):
        self.comentarios = [dict(c) for c in comentarios]
        self.escrituras = 0

    def leer(self):
        return [dict(c) for c in self.comentarios]

    def parchar(self, cid, body):
        self.escrituras += 1
        for c in self.comentarios:
            if c["id"] == cid:
                c["body"] = body

    def crear(self, body):
        self.escrituras += 1
        self.comentarios.append({"id": 100 + self.escrituras, "body": body})

    def comentar(self, cid, texto):
        self.comentarios.append(
            {
                "id": cid,
                "body": texto,
                "user": "jefe",
                "created_at": "2026-11-01T10:00:00Z",
                "updated_at": "2026-11-01T10:00:00Z",
            }
        )

    def sticky(self):
        return review.sticky_from_comments(self.leer(), BOT)["body"]


def reconciliar(github, evento, head, artifact=None):
    """El cmd_reconcile real; devuelve (código de salida, entradas despachadas)."""
    despachos = []

    def sh_falso(*args, **kw):
        if args[:3] == ("gh", "workflow", "run"):
            pares = [args[i + 1] for i, a in enumerate(args) if a == "-f"]
            despachos.append(dict(p.split("=", 1) for p in pares))
            return mock.Mock(stdout="")
        if args[:3] == ("gh", "run", "download"):
            destino = Path(args[args.index("--dir") + 1])
            destino.mkdir(parents=True)
            (destino / "result.json").write_text(json.dumps(artifact))
            return mock.Mock(stdout="")
        ruta = args[2]
        if ruta.endswith("/pulls/12"):
            return mock.Mock(
                stdout=json.dumps({"head": {"sha": head}, "base": {"sha": BASE}})
            )
        if "/commits/" in ruta:
            return mock.Mock(stdout=json.dumps({"sha": MAIN}))
        # El worker 900 avisó con el artifact de la solicitud que nombra.
        rid = (artifact or {}).get("request_id")
        nombre = f"ai-review-result-request-{rid}-run-900-attempt-1"
        if "/actions/workflows/ai-review-worker.yml/runs" in ruta:
            run = {
                "id": 900,
                "display_title": f"PR 12 solicitud {rid}",
                "path": ".github/workflows/ai-review-worker.yml",
                "head_branch": "main",
                "head_sha": MAIN,
                "run_attempt": 1,
                "status": "completed",
            }
            runs = [run] if artifact is not None else []
            return mock.Mock(stdout=json.dumps({"workflow_runs": runs}))
        if ruta.endswith("/actions/runs/900/artifacts?per_page=100"):
            return mock.Mock(stdout=json.dumps({"artifacts": [{"name": nombre}]}))
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
            "WORKER_RUN_ID": "900" if artifact is not None else "",
            "WORKER_ATTEMPT": "1" if artifact is not None else "",
        }
        codigo = 0
        with (
            mock.patch.dict(os.environ, entorno, clear=False),
            mock.patch.object(review, "ComentariosGh", return_value=github),
            mock.patch.object(review, "sh", side_effect=sh_falso),
            mock.patch.object(review, "collaborator_permission", return_value="write"),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            try:
                review.cmd_reconcile(argparse.Namespace(work=tmp))
            except SystemExit as salida:
                codigo = salida.code or 0
    return codigo, despachos


def resultado_del_worker(request_id, head):
    return {
        "request_id": request_id,
        "run_id": 900,
        "attempt": 1,
        "pr_head_sha": head,
        "policy_digest": review.digest_de_politica(review.politica_de_revision()),
        "observaciones": [],
        "cobertura": "complete",
    }


def sticky_con_hallazgo():
    digest = review.digest_de_politica(review.politica_de_revision())
    estado = domain.Snapshot(
        schema=3,
        generation=2,
        revision=domain.Revision(base_sha=BASE, head_sha=A, policy_digest=digest),
        next_id=2,
        completion=domain.COMPLETE_CLAIM,
        findings=[
            domain.Finding(
                id="F1",
                title="Fuga",
                severity="High",
                status=domain.StatusOpen(),
                primary_anchor=domain.AnchorLegacy(path="a.py", line=1),
            )
        ],
        command_cursor=0,
    )
    bloque = domain.encode_snapshot(estado).block
    return {"id": 7, "body": review.cuerpo_con_cabecera(bloque, ""), "user": BOT}


class PushNuevoCancelaAlWorkerAnterior(unittest.TestCase):
    def test_push_nuevo_cancela_al_worker_anterior_sin_tumbar_al_coordinador(self):
        github = GitHubFalso()
        _, primero = reconciliar(github, "pull_request_target", A)
        _, segundo = reconciliar(github, "pull_request_target", B)
        despachos = primero + segundo
        self.assertEqual(
            [(d["request_id"], d.get("kind")) for d in despachos],
            [("1", "review"), ("2", "review")],
        )
        for despacho in despachos:
            self.assertLessEqual(
                set(despacho), _entradas_declaradas(), "GitHub rechaza (422)"
            )
        grupo_a, _ = _grupo_y_cancelacion(primero[0])
        grupo_b, cancela = _grupo_y_cancelacion(segundo[0])
        self.assertEqual(grupo_a, grupo_b)
        self.assertTrue(cancela, "el run de B cancela al de A en curso")
        self.assertFalse(_avisar_corre("cancelled"), "no hay artifact que bajar")
        self.assertTrue(_avisar_corre("success"))
        self.assertTrue(_avisar_corre("failure"), "el fallo se sigue avisando")

        # El worker de A terminó y avisó justo antes de que B lo cancelara.
        escrituras, sticky = github.escrituras, github.sticky()
        codigo, tardio = reconciliar(
            github, "workflow_dispatch", B, resultado_del_worker(1, A)
        )
        self.assertEqual(codigo, 0)
        self.assertEqual(tardio, [])
        self.assertEqual((github.escrituras, github.sticky()), (escrituras, sticky))

    def test_resultado_de_una_solicitud_que_esta_memoria_nunca_emitio_falla_alto(self):
        # Un artifact de otro PR, uno corrupto o una memoria perdida no son un
        # worker cancelado: el run del coordinador queda rojo para que se vea.
        github = GitHubFalso()
        reconciliar(github, "pull_request_target", A)
        escrituras, sticky = github.escrituras, github.sticky()
        for request_id in (2, "1", None):
            with self.subTest(request_id=request_id):
                codigo, despachos = reconciliar(
                    github, "workflow_dispatch", A, resultado_del_worker(request_id, A)
                )
                self.assertNotEqual(codigo, 0)
                self.assertEqual(despachos, [])
                self.assertEqual(
                    (github.escrituras, github.sticky()), (escrituras, sticky)
                )
        sin_memoria = GitHubFalso()
        codigo, despachos = reconciliar(
            sin_memoria, "workflow_dispatch", A, resultado_del_worker(1, A)
        )
        self.assertNotEqual(codigo, 0)
        self.assertEqual((despachos, sin_memoria.escrituras), ([], 0))

    def test_explicaciones_otros_prs_y_coordinador_anterior_no_se_cancelan(self):
        # Una explicación cancelada quedaría pendiente para siempre: la
        # recuperación solo re-despacha revisiones y la poda no toca explicaciones.
        github = GitHubFalso([sticky_con_hallazgo()])
        github.comentar(8, "ai-review: explicar F1")
        _, primera = reconciliar(github, "issue_comment", A)
        github.comentar(9, "ai-review: explicar F1")
        _, segunda = reconciliar(github, "issue_comment", A)
        _, revision = reconciliar(github, "pull_request_target", B)
        despachos = primera + segunda + revision
        self.assertEqual(
            [d.get("kind") for d in despachos], ["explain", "explain", "review"]
        )
        grupos = {_grupo_y_cancelacion(d)[0] for d in despachos}
        self.assertEqual(len(grupos), 3, grupos)
        # Orbit #419/#420: dos PRs nuevos piden la solicitud 1 a la vez; con un
        # grupo sin PR, GitHub cancelaba el worker en espera.
        self.assertNotEqual(
            _grupo_y_cancelacion({"request_id": "1", "pr_number": "419"}),
            _grupo_y_cancelacion({"request_id": "1", "pr_number": "420"}),
        )
        # Un coordinador fijado antes de este cambio no manda kind: sus
        # solicitudes conservan un grupo cada una.
        self.assertNotEqual(
            _grupo_y_cancelacion({"request_id": "1", "pr_number": "12"}),
            _grupo_y_cancelacion({"request_id": "2", "pr_number": "12"}),
        )


if __name__ == "__main__":
    unittest.main()
