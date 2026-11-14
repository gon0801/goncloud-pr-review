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


class ProxyQueIgnoraSigterm:
    def __init__(self):
        self.vivo = True

    def terminate(self):
        pass

    def kill(self):
        self.vivo = False

    def wait(self, timeout=None):
        if self.vivo:
            raise subprocess.TimeoutExpired("litellm", timeout)
        return -9


class ParadaDelProxy(unittest.TestCase):
    def correr(self, agente):
        proxies = []

        def arrancar(work, provider, key, session):
            proxies.append(ProxyQueIgnoraSigterm())
            return proxies[-1], "http://127.0.0.1:4000", "sk-token"

        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "manifest.json").write_text(json.dumps(MANIFEST))
            env = {
                "API_KEY": "sk-go-key-123",
                "PROVIDER": "opencode-go",
                "REPO": "o/r",
                "PR_NUMBER": "7",
            }
            salida = None
            with (
                mock.patch.dict(os.environ, env),
                mock.patch.object(review, "start_proxy", side_effect=arrancar),
                mock.patch.object(review, "run_agent", side_effect=agente),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                try:
                    review.cmd_run(argparse.Namespace(work=str(work)))
                except SystemExit as exc:
                    salida = exc.code
            result = json.loads((work / "result.json").read_text())
        return salida, result, proxies

    def test_un_proxy_lento_en_salir_no_tumba_el_paso(self):
        def revision_entregada(cmd, child_env, result_path, *args, **kwargs):
            result_path.write_text(json.dumps(OK_REPLY))

        def falla_suave(cmd, child_env, result_path, *args, **kwargs):
            review.soft_fail(result_path, "error permanente del proveedor")

        casos = {
            "revisión entregada": (revision_entregada, None, OK_REPLY),
            "falla suave": (
                falla_suave,
                0,
                {review.ERROR_KEY: "error permanente del proveedor"},
            ),
        }
        for caso, (agente, salida_esperada, resultado) in casos.items():
            with self.subTest(caso=caso):
                salida, result, proxies = self.correr(agente)
                self.assertEqual(salida, salida_esperada)
                self.assertEqual(result, resultado)
                self.assertEqual([p.vivo for p in proxies], [False])


if __name__ == "__main__":
    unittest.main()
