import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import review  # noqa: E402

CORPUS = ROOT / "evaluation" / "reviewer" / "corpus.json"
JUDGMENTS = ROOT / "evaluation" / "reviewer" / "judgments.json"

HEX40 = re.compile(r"^[0-9a-f]{40}$")


class CorpusCongelado(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = json.loads(CORPUS.read_text())
        cls.casos = cls.corpus["casos"]

    def test_veinte_casos_con_ids_unicos(self):
        self.assertEqual(len(self.casos), 20)
        self.assertEqual(len({c["caso"] for c in self.casos}), 20)

    def test_particion_13_ajuste_y_7_reservada(self):
        conteo = {"ajuste": 0, "reservada": 0}
        for c in self.casos:
            self.assertIn(c["particion"], conteo)
            conteo[c["particion"]] += 1
        self.assertEqual(conteo, {"ajuste": 13, "reservada": 7})

    def test_al_menos_diez_pares_de_pushes(self):
        pares = [c for c in self.casos if c["pushes"] >= 2]
        self.assertGreaterEqual(len(pares), 10)

    def test_categorias_observables_presentes(self):
        self.assertTrue(any(c["categoria"] == "limpio" for c in self.casos))
        self.assertTrue(any(c["categoria"] == "entre_modulos" for c in self.casos))
        self.assertTrue(any(c["corte_anterior"] for c in self.casos))
        pendientes = "\n".join(self.corpus["meta"]["pendientes"])
        self.assertIn("renombre", pendientes)
        self.assertIn("reversión", pendientes)

    def test_shas_validos_y_alcanzables_en_el_repo(self):
        for c in self.casos:
            self.assertRegex(c["base"], HEX40)
            self.assertRegex(c["head"], HEX40)
            for sha in (c["base"], c["head"]):
                subprocess.run(["git", "cat-file", "-e", sha], check=True)

    def test_meta_declara_incompleto_y_versiones_fijadas(self):
        meta = self.corpus["meta"]
        self.assertFalse(meta["completo"])
        self.assertEqual(meta["versiones"]["claude_code"], review.CLAUDE_CODE_VERSION)
        self.assertEqual(meta["versiones"]["litellm"], review.LITELLM_VERSION)
        self.assertEqual(
            meta["clave_observacion"], "(caso, producto, configuracion, intento)"
        )


class JudgmentsCongelado(unittest.TestCase):
    def test_es_un_solo_objeto_json_con_adjudicaciones(self):
        datos = json.loads(JUDGMENTS.read_text())
        self.assertIsInstance(datos, dict)
        self.assertIsInstance(datos["adjudicaciones"], list)


if __name__ == "__main__":
    unittest.main()
