"""Vuelta de cierre: una revisión que agota el tope de turnos retoma su misma
sesión una vez para escribir la respuesta final con lo que ya leyó. Si esa
vuelta no puede correr o no entrega texto, el resultado queda como hoy."""

import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import review  # noqa: E402

SESION = "0f8e5c1a-3b2d-4c6e-9a7f-1d2e3f4a5b6c"
SIN_TURNOS = {
    "type": "result",
    "subtype": "error_max_turns",
    "is_error": True,
    "num_turns": 61,
    "session_id": SESION,
    "total_cost_usd": 0.4,
    "usage": {"input_tokens": 900000, "output_tokens": 20000},
}
TITULO = "x cambia de 1 a 2 sin migrar a quien lo lee"
CIERRE = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "num_turns": 2,
    "session_id": SESION,
    # Una sesión retomada reporta su costo acumulado: 0.40 de la revisión más
    # 0.02 de esta vuelta. Turnos y usage sí son solo los de la vuelta.
    "total_cost_usd": 0.42,
    "usage": {"input_tokens": 5000, "output_tokens": 300},
    "result": (
        "**Veredicto:** 1 High.\n\n"
        f"#### 🟠 High · `a.py:1` · {TITULO}\n\n"
        '<!-- ai-review:findings={"findings":[{"id":"F-new","file":"a.py",'
        f'"line":1,"severity":"High","title":"{TITULO}","state":"open"}}]}} -->\n'
        '<!-- ai-review:unreviewed=["b.py"] -->\n'
        "COVERAGE: partial | no alcancé a leer b.py"
    ),
}
CUOTA = {
    "result": "API Error: 402 Insufficient account funds",
    "is_error": True,
    "api_error_status": 402,
}


class ClaudeFalso:
    """La CLI de la prueba: guarda la sesión salvo con --no-session-persistence
    y solo retoma con --resume una sesión que guardó. Cada respuesta es un par
    (segundos que tarda, salida JSON); pasar el límite es un timeout."""

    def __init__(self, respuestas, reloj, guarda=True):
        self.respuestas = list(respuestas)
        self.reloj = reloj
        self.guarda = guarda
        self.sesiones = set()
        self.corridas = []

    def __call__(self, cmd, env, timeout):
        self.corridas.append((list(cmd), dict(env), timeout))
        segundos, respuesta = self.respuestas.pop(0)
        if segundos > timeout:
            self.reloj[0] += timeout
            raise subprocess.TimeoutExpired(cmd, timeout, output="", stderr="")
        self.reloj[0] += segundos
        if "--resume" in cmd:
            sesion = cmd[cmd.index("--resume") + 1]
            if sesion not in self.sesiones:
                aviso = f"No conversation found with session ID: {sesion}\n"
                return subprocess.CompletedProcess(cmd, 1, "", aviso)
        elif (
            self.guarda
            and "--no-session-persistence" not in cmd
            and respuesta.get("session_id")
        ):
            self.sesiones.add(respuesta["session_id"])
        codigo = 1 if respuesta.get("is_error") else 0
        return subprocess.CompletedProcess(cmd, codigo, json.dumps(respuesta), "")


def git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, text=True, capture_output=True
    ).stdout.strip()


class VueltaDeCierre(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name, "repo")
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "t@t")
        git(self.repo, "config", "user.name", "t")
        (self.repo / "a.py").write_text("x = 1\n")
        (self.repo / "b.py").write_text("y = 1\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "base")
        self.base = git(self.repo, "rev-parse", "HEAD")
        (self.repo / "a.py").write_text("x = 2\n")
        (self.repo / "b.py").write_text("y = 2\n")
        git(self.repo, "commit", "-qam", "head")
        self.head = git(self.repo, "rev-parse", "HEAD")
        self.work = Path(tmp.name, "work")
        self.work.mkdir()
        manifest = {
            "base": self.base,
            "head": self.head,
            "reviewed": ["a.py", "b.py"],
            "excluded": [],
            "diff_bytes": 20,
        }
        (self.work / "manifest.json").write_text(json.dumps(manifest))

    def correr(self, respuestas, guarda=True, fallback_key=""):
        """cmd_run con reloj virtual: (salida, result.json, ClaudeFalso,
        proxies arrancados)."""
        reloj = [0.0]
        claude = ClaudeFalso(respuestas, reloj, guarda)
        proxies = []

        def arrancar_proxy(work, provider, key, session):
            proxies.append(mock.Mock(**{"wait.return_value": 0}))
            return proxies[-1], "http://127.0.0.1:4000", "sk-token"

        env = {
            "API_KEY": "sk-go-key-123",
            "FALLBACK_API_KEY": fallback_key,
            "PROVIDER": "opencode-go",
            "MAX_TURNS": "60",
            "ATTEMPT_TIMEOUT": "1290",
            "ATTEMPTS": "1",
            "REVIEW_BUDGET_SECONDS": "1320",
            "REPO": "o/r",
            "PR_NUMBER": "7",
        }
        salida = None
        with (
            mock.patch.dict(os.environ, env),
            mock.patch.object(
                review.time, "monotonic", side_effect=lambda: reloj[0]
            ),
            mock.patch.object(review.time, "sleep"),
            mock.patch.object(review, "start_proxy", side_effect=arrancar_proxy),
            mock.patch.object(review, "run_in_group", side_effect=claude),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            try:
                review.cmd_run(argparse.Namespace(work=str(self.work)))
            except SystemExit as exc:
                salida = exc.code
        result = json.loads((self.work / "result.json").read_text())
        return salida, result, claude, proxies

    def publicar(self, result):
        (self.work / "result.json").write_text(json.dumps(result))
        real_sh = review.sh

        def run_command(*args, **kwargs):
            if args[0] == "git":
                return real_sh(*args, cwd=self.repo, **kwargs)
            return mock.Mock(returncode=0)

        with (
            mock.patch.dict(
                os.environ,
                {
                    "REPO": "o/r",
                    "PR_NUMBER": "7",
                    "HEAD_SHA": self.head,
                    "PROVIDER": "opencode-go",
                    "GITHUB_STEP_SUMMARY": "",
                },
            ),
            mock.patch.object(review, "fetch_all_comments", return_value=[]),
            mock.patch.object(review, "set_output"),
            mock.patch.object(review, "sh", side_effect=run_command),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            review.cmd_publish(argparse.Namespace(work=str(self.work)))
        return json.loads((self.work / "comment.json").read_text())["body"]

    def cerrar_para_el_coordinador(self):
        """close-result del worker sobre el result.json de la corrida: el
        paquete que el coordinador acepta sin mirar el subtype."""
        (self.work / "request-package.json").write_text(
            json.dumps({"request_id": 1, "kind": "review", "pr_head_sha": self.head})
        )
        entorno = {
            "GITHUB_OUTPUT": str(self.work / "out"),
            "GITHUB_STEP_SUMMARY": str(self.work / "summary"),
            "FINDING_IDENTITY": "current",
        }
        with (
            mock.patch.dict(os.environ, entorno),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            review.cmd_close_result(argparse.Namespace(work=str(self.work)))
        return json.loads((self.work / "result.json").read_text())

    def test_el_comentario_lleva_los_hallazgos_de_la_vuelta_de_cierre(self):
        salida, result, claude, proxies = self.correr(
            [(900, SIN_TURNOS), (40, CIERRE)]
        )
        self.assertIsNone(salida)
        self.assertEqual(len(claude.corridas), 2, "una sola vuelta de cierre")
        (principal, _, _), (cierre, _, limite) = claude.corridas
        i = cierre.index("--resume")
        self.assertEqual(cierre[i + 1], SESION)
        cierre = cierre[:i] + cierre[i + 2 :]
        mismas_restricciones = list(principal)
        for flag, valor in (("-p", None), ("--max-turns", "3")):
            j = principal.index(flag) + 1
            mismas_restricciones[j] = valor or cierre[j]
        self.assertEqual(cierre, mismas_restricciones)
        self.assertEqual(limite, 240, "límite propio de la vuelta")
        self.assertEqual(len(proxies), 2)
        self.assertTrue(all(p.terminate.called for p in proxies))
        self.assertAlmostEqual(result["total_cost_usd"], 0.42)

        cuerpo = self.publicar(result)
        self.assertIn(TITULO, cuerpo)
        self.assertIn(f"completion={self.head}:partial -->", cuerpo)
        self.assertIn("el revisor se quedó sin turnos antes de terminar", cuerpo)
        self.assertIn("- Turnos: 63/60", cuerpo)
        self.assertNotIn("_El revisor no devolvió texto._", cuerpo)

    def test_una_vuelta_que_declara_complete_sigue_publicandose_parcial(self):
        completa = dict(
            CIERRE,
            result=CIERRE["result"].rsplit("\n", 2)[0] + "\nCOVERAGE: complete",
        )
        _, result, _, _ = self.correr([(900, SIN_TURNOS), (40, completa)])
        cuerpo = self.publicar(result)
        self.assertIn(TITULO, cuerpo)
        self.assertIn(f"completion={self.head}:partial -->", cuerpo)
        self.assertIn("el revisor se quedó sin turnos antes de terminar", cuerpo)

        paquete = self.cerrar_para_el_coordinador()
        self.assertEqual(paquete["cobertura"], review.review_domain.PARTIAL)
        self.assertEqual([o["title"] for o in paquete["observaciones"]], [TITULO])
        self.assertEqual(paquete["num_turns"], 63)

    def test_con_respaldo_la_vuelta_va_al_mismo_proveedor_de_la_revision(self):
        salida, result, claude, _ = self.correr(
            [(5, CUOTA), (600, SIN_TURNOS), (40, CIERRE)],
            fallback_key="sk-deepseek-key",
        )
        self.assertIsNone(salida)
        self.assertEqual(len(claude.corridas), 3)
        respaldo, cierre = claude.corridas[1:]
        pago = review.PROVIDERS["deepseek"]
        for argv, env, _ in (respaldo, cierre):
            self.assertEqual(argv[argv.index("--model") + 1], pago["model"])
            self.assertEqual(env["ANTHROPIC_BASE_URL"], pago["upstream"])
            self.assertEqual(env["ANTHROPIC_API_KEY"], "sk-deepseek-key")
        self.assertIn("--resume", cierre[0])
        self.assertEqual(result["review_provider"], "deepseek")
        self.assertEqual(result["result"], CIERRE["result"])

    def test_si_la_vuelta_no_puede_entregar_texto_queda_el_resultado_de_hoy(self):
        sin_sesion = {k: v for k, v in SIN_TURNOS.items() if k != "session_id"}
        permanente = {
            "result": "invalid model",
            "is_error": True,
            "api_error_status": 400,
        }
        # (segundos de la corrida principal, su salida, la vuelta, guarda sesión)
        casos = {
            "sin id de sesión": (900, sin_sesion, [], True),
            "la sesión no se guardó": (900, SIN_TURNOS, [(5, CIERRE)], False),
            "la vuelta excede su límite": (900, SIN_TURNOS, [(300, CIERRE)], True),
            "error permanente": (900, SIN_TURNOS, [(5, permanente)], True),
            "quedan 80 s": (1210, SIN_TURNOS, [], True),
        }
        for caso, (segundos, principal, vuelta, guarda) in casos.items():
            with self.subTest(caso=caso):
                salida, result, claude, _ = self.correr(
                    [(segundos, principal), *vuelta], guarda=guarda
                )
                self.assertIsNone(salida)
                self.assertEqual(result, principal)
                self.assertEqual(len(claude.corridas), 1 + len(vuelta))
                cuerpo = self.publicar(result)
                self.assertIn("_El revisor no devolvió texto._", cuerpo)
                self.assertIn("- Turnos: 61/60", cuerpo)

    def test_el_consumo_de_una_sesion_retomada_no_se_cuenta_dos_veces(self):
        # Salidas de Claude Code 2.1.325 contra un endpoint local: tres llamadas
        # en la revisión y una en el cierre, cada una de 100 tokens de entrada,
        # 20 de salida y 0.001 de costo. El cierre trae el costo de la sesión
        # completa, pero solo sus propios turnos y su propio usage. Su
        # modelUsage, que sí es de la sesión, dice 400 y 80.
        revision = {
            "num_turns": 4,
            "total_cost_usd": 0.003,
            "usage": {"input_tokens": 300, "output_tokens": 60},
        }
        cierre = {
            "num_turns": 1,
            "total_cost_usd": 0.004,
            "usage": {"input_tokens": 100, "output_tokens": 20},
        }
        review.sumar_consumo(revision, cierre, sesion_retomada=True)
        self.assertEqual(revision["num_turns"], 5)
        self.assertAlmostEqual(revision["total_cost_usd"], 0.004)
        self.assertEqual(revision["usage"], {"input_tokens": 400, "output_tokens": 80})

    def test_el_artifact_no_lleva_la_llave_en_la_salida_cruda_de_las_vueltas(self):
        llave = "sk-go-key-123"
        con_llave = dict(CIERRE, result=f"la llave es {llave}\n" + CIERRE["result"])
        self.correr([(900, SIN_TURNOS), (40, con_llave)])
        (self.work / "repair.json").write_text(
            json.dumps({"result": f"otra vez {llave}"})
        )
        with mock.patch.dict(os.environ, {"API_KEY": llave}):
            self.cerrar_para_el_coordinador()
        for cruda in ("cierre.json", "repair.json"):
            with self.subTest(archivo=cruda):
                texto = (self.work / cruda).read_text()
                self.assertNotIn(llave, texto)
                self.assertIn("[REDACTED]", json.loads(texto)["result"])

    def test_una_vuelta_que_tambien_agota_turnos_suma_su_consumo(self):
        otra_vez = dict(SIN_TURNOS, num_turns=3, total_cost_usd=0.43)
        salida, result, claude, _ = self.correr(
            [(900, SIN_TURNOS), (60, otra_vez)]
        )
        self.assertIsNone(salida)
        self.assertEqual(len(claude.corridas), 2)
        self.assertNotIn("result", result)
        self.assertEqual(result["num_turns"], 64)
        self.assertAlmostEqual(result["total_cost_usd"], 0.43)
        self.assertEqual(
            result["usage"], {"input_tokens": 1800000, "output_tokens": 40000}
        )
        cuerpo = self.publicar(result)
        self.assertIn("_El revisor no devolvió texto._", cuerpo)
        self.assertIn("- Turnos: 64/60", cuerpo)


if __name__ == "__main__":
    unittest.main()
