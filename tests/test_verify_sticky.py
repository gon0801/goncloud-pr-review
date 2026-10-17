"""El verificador de stickies acepta memoria coherente y frena el resto."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import review  # noqa: E402

SCRIPT = ROOT / "scripts" / "verify_sticky.py"
SHA_A, SHA_B = "a" * 40, "b" * 40


def legado():
    return (
        review.FINDINGS_PREFIX
        + json.dumps(
            {
                "findings": [
                    {
                        "id": "F1",
                        "file": "a.py",
                        "line": 1,
                        "severity": "High",
                        "title": "t",
                        "state": "open",
                    }
                ],
                "next": 2,
            }
        )
        + review.FINDINGS_SUFFIX
    )


def v2(completion):
    return (
        review.FINDINGS_PREFIX
        + json.dumps(
            {
                "schema": 2,
                "generation": 1,
                "next_id": 1,
                "command_cursor": 0,
                "completion": completion,
                "findings": [],
            }
        )
        + review.FINDINGS_SUFFIX
    )


def sticky(marker, bloque, sha=SHA_A):
    return (
        f"{review.MARKER}\n{review.SHA_PREFIX}{sha} -->\n"
        f"{review.COMPLETION_PREFIX}{sha}:{marker} -->\n{bloque}\ncola"
    )


class VerificadorDeStickies(unittest.TestCase):
    def correr(self, cuerpo, *extra):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as tmp:
            tmp.write(cuerpo)
            ruta = tmp.name
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--body-file", ruta, *extra],
            capture_output=True,
            text=True,
        )
        return proc.returncode, proc.stdout

    def test_legado_coherente_pasa(self):
        rc, salida = self.correr(sticky("partial", legado()))
        self.assertEqual(rc, 0, salida)

    def test_legado_con_complete_frena(self):
        rc, salida = self.correr(sticky("complete", legado()))
        self.assertEqual(rc, 1, salida)
        self.assertIn("no coincide", salida)

    def test_v2_completo_coherente_pasa(self):
        rc, salida = self.correr(sticky("complete", v2("complete_claim")))
        self.assertEqual(rc, 0, salida)

    def test_v2_parcial_con_marcador_complete_frena(self):
        rc, salida = self.correr(sticky("complete", v2("partial")))
        self.assertEqual(rc, 1, salida)

    def test_sin_bloque_frena(self):
        cuerpo = (
            f"{review.MARKER}\n{review.SHA_PREFIX}{SHA_A} -->\n"
            f"{review.COMPLETION_PREFIX}{SHA_A}:partial -->\nsolo prosa"
        )
        rc, _ = self.correr(cuerpo)
        self.assertEqual(rc, 1)

    def test_bloque_roto_frena(self):
        cuerpo = sticky("partial", review.FINDINGS_PREFIX + '{"findings": [')
        rc, salida = self.correr(cuerpo)
        self.assertEqual(rc, 1, salida)

    def test_sha_esperado(self):
        cuerpo = sticky("partial", legado(), sha=SHA_B)
        rc, _ = self.correr(cuerpo, "--expect-sha", SHA_B)
        self.assertEqual(rc, 0)
        rc, salida = self.correr(cuerpo, "--expect-sha", SHA_A)
        self.assertEqual(rc, 1, salida)

    def sticky_con_libro(self, marker, bloque, linea_libro, sha=SHA_A):
        return (
            f"{review.MARKER}\n{review.SHA_PREFIX}{sha} -->\n"
            f"{review.COMPLETION_PREFIX}{sha}:{marker} -->\n"
            f"{linea_libro}\n{bloque}\ncola"
        )

    def test_k_sticky_con_libro_valido_pasa(self):
        linea = (
            "<!-- ai-review:covered="
            + SHA_A
            + ":"
            + json.dumps({"a.py": SHA_B})
            + " -->"
        )
        rc, salida = self.correr(self.sticky_con_libro("partial", legado(), linea))
        self.assertEqual(rc, 0, salida)
        self.assertIn("libro", salida)

    def test_k_sticky_con_libro_corrupto_frena(self):
        linea = "<!-- ai-review:covered=" + SHA_A + ':{"a.py": } -->'
        rc, salida = self.correr(self.sticky_con_libro("partial", legado(), linea))
        self.assertEqual(rc, 1, salida)
        self.assertIn("libro", salida)

    def test_k_sticky_con_libro_de_otro_head_frena(self):
        linea = (
            "<!-- ai-review:covered="
            + SHA_B
            + ":"
            + json.dumps({"a.py": SHA_B})
            + " -->"
        )
        rc, salida = self.correr(self.sticky_con_libro("partial", legado(), linea))
        self.assertEqual(rc, 1, salida)
        self.assertIn("libro", salida)
