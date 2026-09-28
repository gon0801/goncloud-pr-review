import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAIZ = ROOT / "evaluation" / "reviewer"
BUILDER = ROOT / "scripts" / "build_adjudicacion_ciega.py"

CORPUS = json.loads((RAIZ / "corpus.json").read_text())["casos"]
OBS = json.loads((RAIZ / "observaciones.json").read_text())
HOJA = json.loads((RAIZ / "adjudicacion-ciega.json").read_text())
CORRESPONDENCIA = json.loads((RAIZ / "ciego-correspondencia.json").read_text())

CAMPOS_HOJA = {
    "blind_id",
    "caso",
    "head",
    "severidad",
    "titulo",
    "ruta",
    "linea",
    "detalle",
    "contexto",
}
TOKENS_PROCEDENCIA = [
    "coderabbit",
    "original-conservada",
    "retanda",
    "run ",
    "run-",
]


class InsumosCiegos(unittest.TestCase):
    def test_biyeccion_hoja_correspondencia(self):
        ids_hoja = [h["blind_id"] for h in HOJA["hallazgos"]]
        ids_corr = [f["blind_id"] for f in CORRESPONDENCIA["filas"]]
        self.assertEqual(ids_hoja, ids_corr)
        self.assertEqual(len(ids_hoja), len(set(ids_hoja)))
        for f in CORRESPONDENCIA["filas"]:
            self.assertEqual(
                set(f),
                {"blind_id", "caso", "producto", "hallazgo_id", "procedencia"},
            )
            self.assertIn(f["producto"], {"revisor", "coderabbit"})
            self.assertTrue(f["procedencia"])

    def test_blind_ids_unicos_y_formato(self):
        ids = [h["blind_id"] for h in HOJA["hallazgos"]]
        self.assertTrue(all(re.fullmatch(r"H-\d{3}", i) for i in ids))
        self.assertEqual(len(ids), len(set(ids)))

    def test_ningun_hallazgo_perdido_ni_duplicado(self):
        esperados = sorted(
            (o["caso"], h["id"]) for o in OBS["observaciones"] for h in o["hallazgos"]
        )
        mapeados = sorted(
            (f["caso"], f["hallazgo_id"]) for f in CORRESPONDENCIA["filas"]
        )
        self.assertEqual(esperados, mapeados)

    def test_observaciones_con_base_y_head_del_corpus(self):
        corpus = {c["caso"]: c for c in CORPUS}
        for o in OBS["observaciones"]:
            c = corpus[o["caso"]]
            self.assertEqual((o["base"], o["head"]), (c["base"], c["head"]))
            if o["producto"] == "coderabbit":
                self.assertEqual(o["sha_captura"], c["head"])

    def test_solo_las_20_salidas_utiles(self):
        corpus = {c["caso"]: c for c in CORPUS}
        indice = json.loads((RAIZ / "salidas" / "indice.json").read_text())
        salidas = indice["salidas"]
        self.assertEqual(len(salidas), 20)
        self.assertEqual({s["caso"] for s in salidas}, set(corpus))
        conservadas = {
            s["caso"] for s in salidas if s["procedencia"] == "original-conservada"
        }
        self.assertEqual(conservadas, {"pr6", "pr18", "pr25", "pr26", "pr27"})
        for s in salidas:
            self.assertRegex(s["procedencia"], r"^(run \d+|original-conservada)$")
            self.assertEqual(
                (s["base"], s["head"]),
                (corpus[s["caso"]]["base"], corpus[s["caso"]]["head"]),
            )
            for clave in ("result", "manifest"):
                self.assertTrue((ROOT / s["archivos"][clave]).exists(), s["caso"])
            result = json.loads((ROOT / s["archivos"]["result"]).read_text())
            self.assertEqual(result["subtype"], "success")
            self.assertFalse(result["is_error"])
            self.assertIn("ai-review:findings=", result["result"])
            self.assertRegex(result["result"], r"COVERAGE:\s*complete")

    def test_coderabbit_solo_sha_exacto(self):
        corpus = {c["caso"]: c for c in CORPUS}
        comparables = set()
        for caso, c in corpus.items():
            captura = json.loads(
                (
                    RAIZ / "salidas" / "coderabbit" / f"{caso}-{c['head'][:8]}.json"
                ).read_text()
            )
            self.assertEqual(captura["sha"], c["head"])
            if captura["revisiones"] or captura["comentarios"]:
                comparables.add(caso)
        obs_cr = {
            o["caso"] for o in OBS["observaciones"] if o["producto"] == "coderabbit"
        }
        self.assertEqual(obs_cr, comparables)
        pendientes = {p["caso"]: p for p in OBS["pendientes_coderabbit"]}
        self.assertEqual(set(pendientes), set(corpus) - comparables)
        for p in pendientes.values():
            self.assertEqual(
                (p["base"], p["head"]),
                (corpus[p["caso"]]["base"], corpus[p["caso"]]["head"]),
            )
            self.assertIn("head exacto", p["motivo"])

    def test_hoja_sin_campos_de_procedencia_ni_producto(self):
        for h in HOJA["hallazgos"]:
            self.assertEqual(set(h), CAMPOS_HOJA)
            for token in TOKENS_PROCEDENCIA:
                self.assertNotIn(token, h["contexto"], h["blind_id"])
        texto_meta = json.dumps(HOJA["meta"], ensure_ascii=False).lower()
        for token in TOKENS_PROCEDENCIA:
            self.assertNotIn(token, texto_meta)

    def test_correspondencia_nunca_se_imprime_y_es_deterministica(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_raiz = Path(tmp) / "reviewer"
            tmp_raiz.mkdir()
            shutil.copy2(RAIZ / "corpus.json", tmp_raiz / "corpus.json")
            shutil.copytree(RAIZ / "salidas", tmp_raiz / "salidas")
            r = subprocess.run(
                [sys.executable, str(BUILDER), "--raiz", str(tmp_raiz)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("H-", r.stdout)
            self.assertIn("sha256=", r.stdout)
            for nombre in (
                "adjudicacion-ciega.json",
                "ciego-correspondencia.json",
                "observaciones.json",
            ):
                self.assertEqual(
                    (tmp_raiz / nombre).read_bytes(),
                    (RAIZ / nombre).read_bytes(),
                    f"{nombre} no es determinístico",
                )


if __name__ == "__main__":
    unittest.main()
