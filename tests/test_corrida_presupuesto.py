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
    """El respaldo de pago o el reintento solo arranca si al presupuesto le
    quedan, tras el margen de 30 s, 600 s o el límite del intento si es menor.
    Arranca con lo que quede, aunque no cubra el límite que pide el tope."""

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

    def test_con_tope_alto_y_la_cuota_al_inicio_el_respaldo_arranca(self):
        # 120 y 150 turnos piden 1800 y 2250 s, más que todo el presupuesto.
        for turnos in (120, 150):
            with self.subTest(turnos=turnos):
                salida, result, corridas = self.correr(
                    turnos, [(5, CUOTA), (300, OK_REPLY)]
                )
                self.assertEqual(corridas, [(SUSCRIPCION, 1290), (PAGO, 1285)])
                self.assertIsNone(salida)
                self.assertEqual(result, dict(OK_REPLY, review_provider="deepseek"))

    def test_con_343_s_el_respaldo_no_arranca_y_no_se_paga(self):
        salida, result, corridas = self.correr(150, [(947, CUOTA), (300, OK_REPLY)])
        self.assertEqual(corridas, [(SUSCRIPCION, 1290)], "ninguna corrida de pago")
        self.assertEqual(salida, 0)
        self.assertEqual(
            result,
            {
                review.ERROR_KEY: "opencode-go agotó su cuota; no queda tiempo para "
                "probar deepseek (quedan 343 s, se necesitan al menos 600 s)"
            },
        )

    def test_el_respaldo_arranca_con_600_s_justos(self):
        # 1320 - 690 - 30 de margen = 600.
        salida, result, corridas = self.correr(150, [(690, CUOTA), (300, OK_REPLY)])
        self.assertEqual(corridas, [(SUSCRIPCION, 1290), (PAGO, 600)])
        self.assertIsNone(salida)
        self.assertEqual(result, dict(OK_REPLY, review_provider="deepseek"))
        salida, _, corridas = self.correr(150, [(691, CUOTA), (300, OK_REPLY)])
        self.assertEqual(corridas, [(SUSCRIPCION, 1290)], "con 599 s ya no arranca")
        self.assertEqual(salida, 0)

    def test_un_intento_de_menos_de_600_s_se_reintenta_con_su_propio_limite(self):
        # 30 turnos piden 450 s. La cuota llega a los 400 s y el respaldo de pago
        # falla con un error transitorio; tras 30 s de espera quedan 460 s.
        salida, result, corridas = self.correr(
            30, [(400, CUOTA), (400, TRANSITORIO), (300, OK_REPLY)]
        )
        self.assertEqual(corridas, [(SUSCRIPCION, 450), (PAGO, 450), (PAGO, 450)])
        self.assertIsNone(salida)
        self.assertEqual(result, dict(OK_REPLY, review_provider="deepseek"))
        salida, result, corridas = self.correr(
            30, [(400, CUOTA), (411, TRANSITORIO), (300, OK_REPLY)]
        )
        self.assertEqual(corridas, [(SUSCRIPCION, 450), (PAGO, 450)])
        self.assertEqual(salida, 0)
        self.assertEqual(
            result,
            {
                review.ERROR_KEY: "opencode-go agotó su cuota; deepseek falló con un "
                "error transitorio; no queda tiempo para reintentar (quedan 449 s, "
                "se necesitan al menos 450 s)"
            },
        )

    def test_el_reintento_sigue_la_misma_regla_y_conserva_la_causa(self):
        # Falla a los 660 s; tras 30 s de espera quedan 630, 600 tras el margen.
        salida, result, corridas = self.correr(
            150, [(660, TRANSITORIO), (300, OK_REPLY)]
        )
        self.assertEqual(corridas, [(SUSCRIPCION, 1290), (SUSCRIPCION, 600)])
        self.assertIsNone(salida)
        self.assertEqual(result, OK_REPLY)
        nota = (
            "no queda tiempo para reintentar (quedan 599 s, se necesitan al menos "
            "600 s)"
        )
        for fallback_key, motivo in (
            (
                "sk-deepseek-key",
                f"opencode-go falló con un error transitorio; {nota}; la suscripción "
                "es la única vía y no se cambia a la API de pago",
            ),
            (
                "",
                "la revisión falló con un error transitorio (proveedor no disponible "
                f"por ahora); {nota}",
            ),
        ):
            with self.subTest(respaldo=bool(fallback_key)):
                salida, result, corridas = self.correr(
                    150, [(661, TRANSITORIO), (300, OK_REPLY)], fallback_key
                )
                self.assertEqual(corridas, [(SUSCRIPCION, 1290)])
                self.assertEqual(salida, 0)
                self.assertEqual(result, {review.ERROR_KEY: motivo})


if __name__ == "__main__":
    unittest.main()
