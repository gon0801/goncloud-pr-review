import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "compare_reviews.py"

BASE = "b" * 40
HEAD = "a" * 40

CORPUS_A = {
    "casos": [
        {
            "caso": "c1",
            "repo": "o/r",
            "base": BASE,
            "head": HEAD,
            "producto": "revisor local",
            "configuracion": "v1",
            "intento": 1,
        }
    ]
}

OBS_A = {
    "observaciones": [
        {
            "caso": "c1",
            "repo": "o/r",
            "base": BASE,
            "head": HEAD,
            "producto": "revisor local",
            "configuracion": "v1",
            "intento": 1,
            "resultado": "success",
            "cobertura": "complete",
            "duracion_s": 120.5,
            "turnos": 9,
            "costo_usd": 0.048,
            "hallazgos": [
                {"id": "F1", "titulo": "t1", "ruta": "a.py", "resuelto": False},
                {"id": "F2", "titulo": "t2", "ruta": "b.py", "resuelto": True},
                {"id": "F3", "titulo": "t3", "ruta": "c.py", "resuelto": False},
                {"id": "F4", "titulo": "t4", "ruta": "a.py", "resuelto": False},
                {"id": "F5", "titulo": "t5", "ruta": "d.py", "resuelto": False},
            ],
        }
    ]
}

JUDG_A = {
    "adjudicaciones": [
        {"caso": "c1", "hallazgo": "F1", "veredicto": "valid", "defecto": "D1"},
        {
            "caso": "c1",
            "hallazgo": "F2",
            "veredicto": "valid",
            "defecto": "D2",
            "resuelto_real": False,
        },
        {"caso": "c1", "hallazgo": "F3", "veredicto": "false_positive"},
        {
            "caso": "c1",
            "hallazgo": "F4",
            "veredicto": "duplicate",
            "duplicado_de": "F1",
        },
        {"caso": "c1", "hallazgo": "F5", "veredicto": "unresolved"},
    ],
    "defectos": {"c1": ["D1", "D2"]},
}


def correr(corpus, obs, judg):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "corpus.json").write_text(json.dumps(corpus))
        (tmp / "observaciones.json").write_text(json.dumps(obs))
        (tmp / "judgments.json").write_text(json.dumps(judg))
        salida = tmp / "informe.json"
        r = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--corpus",
                str(tmp / "corpus.json"),
                "--observations",
                str(tmp / "observaciones.json"),
                "--judgments",
                str(tmp / "judgments.json"),
                "--output",
                str(salida),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        informe = json.loads(salida.read_text()) if salida.exists() else None
        return r, informe


class Comparador(unittest.TestCase):
    def test_fixture_principal_afirma_conteos_literales(self):
        r, informe = correr(CORPUS_A, OBS_A, JUDG_A)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(informe["hallazgos"]["validos"], 2)
        self.assertEqual(informe["hallazgos"]["falsos_positivos"], 1)
        self.assertEqual(informe["hallazgos"]["duplicados"], 1)
        self.assertEqual(informe["hallazgos"]["sin_resolver"], 1)
        self.assertEqual(informe["precision"]["valor"], 0.5)
        self.assertEqual(informe["precision"]["validos"], 2)
        self.assertEqual(informe["precision"]["denominador"], 4)
        self.assertEqual(informe["precision"]["excluidos_sin_resolver"], 1)
        self.assertEqual(informe["falsos_resueltos"], 1)
        self.assertEqual(informe["defectos_conocidos"]["recuperacion"], 1.0)
        self.assertEqual(informe["defectos_conocidos"]["omitidos"], [])
        self.assertEqual(informe["cobertura_declarada"], {"c1": "complete"})
        self.assertEqual(informe["tiempo"]["duracion_total_s"], 120.5)
        self.assertEqual(informe["tiempo"]["turnos_total"], 9)
        self.assertEqual(informe["costo"], {"usd_conocido": 0.048, "desconocidos": 0})

    def test_sin_conjunto_de_defectos_informa_desconocido(self):
        corpus = {
            "casos": [
                {
                    "caso": "c2",
                    "repo": "o/r",
                    "base": BASE,
                    "head": HEAD,
                    "producto": "revisor local",
                    "configuracion": "v1",
                    "intento": 1,
                }
            ]
        }
        obs = {
            "observaciones": [
                {
                    "caso": "c2",
                    "repo": "o/r",
                    "base": BASE,
                    "head": HEAD,
                    "producto": "revisor local",
                    "configuracion": "v1",
                    "intento": 1,
                    "resultado": "success",
                    "cobertura": "partial",
                    "duracion_s": 60.0,
                    "turnos": 4,
                    "costo_usd": None,
                    "hallazgos": [
                        {"id": "G1", "titulo": "g", "ruta": "x.py", "resuelto": False}
                    ],
                }
            ]
        }
        judg = {
            "adjudicaciones": [{"caso": "c2", "hallazgo": "G1", "veredicto": "valid"}]
        }
        r, informe = correr(corpus, obs, judg)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(informe["defectos_conocidos"]["conjunto_presente"], False)
        self.assertEqual(informe["defectos_conocidos"]["recuperacion"], "desconocido")
        self.assertEqual(informe["defectos_conocidos"]["omitidos"], "desconocido")
        self.assertEqual(informe["costo"]["usd_conocido"], None)
        self.assertEqual(informe["costo"]["desconocidos"], 1)

    def test_rechaza_par_de_distinto_sha(self):
        obs = json.loads(json.dumps(OBS_A))
        obs["observaciones"][0]["head"] = "9" * 40
        r, _ = correr(CORPUS_A, obs, JUDG_A)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("SHA", r.stderr)

    def test_rechaza_filas_duplicadas(self):
        judg = json.loads(json.dumps(JUDG_A))
        judg["adjudicaciones"].append(
            {"caso": "c1", "hallazgo": "F1", "veredicto": "false_positive"}
        )
        r, _ = correr(CORPUS_A, OBS_A, judg)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("duplicad", r.stderr)

    def test_rechaza_veredicto_invalido(self):
        judg = json.loads(json.dumps(JUDG_A))
        judg["adjudicaciones"][0]["veredicto"] = "tal vez"
        r, _ = correr(CORPUS_A, OBS_A, judg)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("veredicto", r.stderr)


if __name__ == "__main__":
    unittest.main()
