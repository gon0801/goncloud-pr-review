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

MANIFEST = {
    "base": "b" * 40,
    "head": "a" * 40,
    "reviewed": ["src/app.py"],
    "excluded": [],
    "diff_bytes": 10,
}
OK_REPLY = {
    "result": 'ok\n<!-- ai-review:findings={"findings":[]} -->\nCOVERAGE: complete',
    "subtype": "success",
}
CUOTA = {
    "result": "API Error: 402 Insufficient account funds",
    "is_error": True,
    "api_error_status": 402,
}
TRANSITORIO = {"result": "overloaded", "is_error": True, "api_error_status": 529}
SUSCRIPCION = review.PROVIDERS["opencode-go"]["model"]
PAGO = review.PROVIDERS["deepseek"]["model"]


class SegundaCorrida(unittest.TestCase):
    """El respaldo de pago o el reintento solo arranca si el presupuesto cubre
    el tiempo límite completo que pide el tope de turnos."""

    def correr(self, turnos, respuestas, fallback_key="sk-deepseek-key"):
        """Corre `cmd_run` con reloj virtual. `respuestas` son pares (segundos
        que tarda la corrida, respuesta del modelo). Devuelve el código de
        salida (None si no hubo soft_fail), result.json y cada corrida del
        modelo como (modelo, límite en segundos)."""
        reloj = [0.0]
        corridas = []

        def avanzar(segundos):
            reloj[0] += segundos

        def lanzar(cmd, env, timeout):
            segundos, respuesta = respuestas[len(corridas)]
            corridas.append((env["ANTHROPIC_MODEL"], timeout))
            if segundos > timeout:
                avanzar(timeout)
                raise subprocess.TimeoutExpired(cmd, timeout, output="", stderr="")
            avanzar(segundos)
            return subprocess.CompletedProcess(
                cmd, 1 if respuesta.get("is_error") else 0, json.dumps(respuesta), ""
            )

        def arrancar_proxy(work, provider, key, session):
            proxy = mock.Mock()
            proxy.wait.return_value = 0
            return proxy, "http://127.0.0.1:4000", "sk-token"

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "manifest.json").write_text(json.dumps(MANIFEST))
            env = {
                "API_KEY": "sk-go-key-123",
                "FALLBACK_API_KEY": fallback_key,
                "PROVIDER": "opencode-go",
                "MAX_TURNS": str(turnos),
                "ATTEMPT_TIMEOUT": "",
                "ATTEMPTS": "2",
                "RETRY_DELAY": "30",
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
                mock.patch.object(review.time, "sleep", side_effect=avanzar),
                mock.patch.object(review, "start_proxy", side_effect=arrancar_proxy),
                mock.patch.object(review, "run_in_group", side_effect=lanzar),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                try:
                    review.cmd_run(argparse.Namespace(work=str(work)))
                except SystemExit as exc:
                    salida = exc.code
            result = json.loads((work / "result.json").read_text())
        return salida, result, corridas

    def test_sin_tiempo_para_la_revision_completa_no_se_paga_el_respaldo(self):
        # 80 turnos piden 1200 s; la cuota se agota a los 900 s y quedan 390.
        salida, result, corridas = self.correr(80, [(900, CUOTA), (300, OK_REPLY)])
        self.assertEqual(corridas, [(SUSCRIPCION, 1200)], "ninguna corrida de pago")
        self.assertEqual(salida, 0)
        self.assertEqual(
            result,
            {
                review.ERROR_KEY: "opencode-go agotó su cuota; no queda tiempo para "
                "una revisión completa con deepseek (quedan 390 s de los 1200 s "
                "que necesita)"
            },
        )

    def test_el_respaldo_de_pago_arranca_con_su_limite_completo(self):
        # 1320 - 30 de margen - 1200 de límite: la cuota puede llegar hasta el s 90.
        for llega in (5, 90):
            with self.subTest(cuota_a_los=llega):
                salida, result, corridas = self.correr(
                    80, [(llega, CUOTA), (300, OK_REPLY)]
                )
                self.assertEqual(corridas, [(SUSCRIPCION, 1200), (PAGO, 1200)])
                self.assertIsNone(salida)
                self.assertEqual(result, dict(OK_REPLY, review_provider="deepseek"))
        salida, _, corridas = self.correr(80, [(91, CUOTA), (300, OK_REPLY)])
        self.assertEqual(corridas, [(SUSCRIPCION, 1200)], "a los 91 s ya no cabe")
        self.assertEqual(salida, 0)

    def test_el_reintento_no_arranca_sin_tiempo_y_conserva_la_causa(self):
        # Falla a los 840 s; tras 30 s de espera quedan 420 de los 1200 s.
        for fallback_key, motivo in (
            (
                "sk-deepseek-key",
                (
                    "opencode-go falló con un error transitorio; no queda tiempo "
                    "para un reintento completo (quedan 420 s de los 1200 s que "
                    "necesita); la suscripción es la única vía y no se cambia a "
                    "la API de pago"
                ),
            ),
            (
                "",
                (
                    "la revisión falló con un error transitorio (proveedor no "
                    "disponible por ahora); no queda tiempo para un reintento "
                    "completo (quedan 420 s de los 1200 s que necesita)"
                ),
            ),
        ):
            with self.subTest(respaldo=bool(fallback_key)):
                salida, result, corridas = self.correr(
                    80, [(840, TRANSITORIO), (700, OK_REPLY)], fallback_key
                )
                self.assertEqual(corridas, [(SUSCRIPCION, 1200)])
                self.assertEqual(salida, 0)
                self.assertEqual(result, {review.ERROR_KEY: motivo})

    def test_el_reintento_arranca_con_su_limite_completo(self):
        salida, result, corridas = self.correr(80, [(60, TRANSITORIO), (300, OK_REPLY)])
        self.assertEqual(corridas, [(SUSCRIPCION, 1200), (SUSCRIPCION, 1200)])
        self.assertIsNone(salida)
        self.assertEqual(result, OK_REPLY)


if __name__ == "__main__":
    unittest.main()
