import hashlib
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAIZ = ROOT / "evaluation" / "reviewer"

CONSENSO_AUTORIZADO_SHA256 = (
    "bd8392246c689fab9b1246baefe8492bedce969d98d08f41012540351501f3d4"
)
CORPUS = json.loads((RAIZ / "corpus.json").read_text())["casos"]
HOJA = json.loads((RAIZ / "adjudicacion-ciega.json").read_text())
CORRESPONDENCIA = json.loads((RAIZ / "ciego-correspondencia.json").read_text())
OBS = json.loads((RAIZ / "observaciones.json").read_text())
CONSENSO = json.loads((RAIZ / "adjudicacion-final-ciega.json").read_text())
JUDGMENTS = json.loads((RAIZ / "judgments.json").read_text())

CLAVES_CONSENSO = {"blind_id", "veredicto", "via", "razon", "defecto", "duplicado_de"}
CLAVES_FILA_E0 = {
    "caso",
    "hallazgo",
    "veredicto",
    "duplicado_de",
    "defecto",
    "resuelto_real",
}
TOKENS_PROCEDENCIA = [
    "coderabbit",
    "deepseek",
    "opencode",
    "original-conservada",
    "retanda",
    "run ",
]


def sha256(ruta):
    return hashlib.sha256(Path(ruta).read_bytes()).hexdigest()


class ConsensoFinal(unittest.TestCase):
    def test_consenso_persistido_es_el_autorizado(self):
        self.assertEqual(
            sha256(RAIZ / "adjudicacion-final-ciega.json"), CONSENSO_AUTORIZADO_SHA256
        )
        self.assertEqual(len(CONSENSO["decisiones"]), 62)
        for f in CONSENSO["decisiones"]:
            self.assertLessEqual(set(f), CLAVES_CONSENSO)

    def test_biyeccion_hoja_correspondencia_consenso(self):
        ids_hoja = {h["blind_id"] for h in HOJA["hallazgos"]}
        ids_corr = [f["blind_id"] for f in CORRESPONDENCIA["filas"]]
        ids_cons = [f["blind_id"] for f in CONSENSO["decisiones"]]
        self.assertEqual(len(ids_hoja), 62)
        self.assertEqual(len(ids_corr), len(set(ids_corr)))
        self.assertEqual(len(ids_cons), len(set(ids_cons)))
        self.assertEqual(ids_hoja, set(ids_corr))
        self.assertEqual(ids_hoja, set(ids_cons))

    def test_conteos_literales_del_consenso(self):
        conteo = {"valid": 0, "false_positive": 0, "duplicate": 0, "unresolved": 0}
        via = {"acuerdo": 0, "desempate": 0}
        for f in CONSENSO["decisiones"]:
            conteo[f["veredicto"]] += 1
            via[f["via"]] += 1
        self.assertEqual(
            conteo, {"valid": 39, "false_positive": 22, "duplicate": 1, "unresolved": 0}
        )
        self.assertEqual(via, {"acuerdo": 40, "desempate": 22})

    def test_duplicate_valido_mismo_caso_sin_ciclos(self):
        por_id = {f["blind_id"]: f for f in CONSENSO["decisiones"]}
        caso_de = {h["blind_id"]: h["caso"] for h in HOJA["hallazgos"]}
        for f in CONSENSO["decisiones"]:
            if f["veredicto"] != "duplicate":
                self.assertIsNone(f.get("duplicado_de"))
                continue
            destino = por_id.get(f.get("duplicado_de"))
            self.assertIsNotNone(destino, f["blind_id"])
            self.assertEqual(destino["veredicto"], "valid")
            self.assertEqual(caso_de[f["blind_id"]], caso_de[destino["blind_id"]])
        for inicio in por_id:
            vistos, actual = set(), inicio
            while por_id[actual].get("duplicado_de"):
                if actual in vistos:
                    self.fail(f"ciclo de duplicate desde {inicio}")
                vistos.add(actual)
                actual = por_id[actual]["duplicado_de"]

    def test_decisiones_asociadas_sin_ambiguedad(self):
        indice = {(o["caso"], o["producto"]): o for o in OBS["observaciones"]}
        hallazgos = {
            (o["caso"], o["producto"], h["id"])
            for o in OBS["observaciones"]
            for h in o["hallazgos"]
        }
        vistos = set()
        for f in CORRESPONDENCIA["filas"]:
            clave = (f["caso"], f["producto"], f["hallazgo_id"])
            self.assertIn(clave, hallazgos, f["blind_id"])
            self.assertNotIn((f["caso"], f["hallazgo_id"]), vistos, f["blind_id"])
            vistos.add((f["caso"], f["hallazgo_id"]))
            obs = indice[(f["caso"], f["producto"])]
            self.assertIn("configuracion", obs)
            self.assertIn("intento", obs)
        for o in OBS["observaciones"]:
            patron = r"^pr\d+-(R|C)\d{2}$"
            for h in o["hallazgos"]:
                self.assertRegex(h["id"], patron)
            sufijo = "R" if o["producto"] == "revisor" else "C"
            self.assertTrue(all(f"-{sufijo}" in h["id"] for h in o["hallazgos"]))

    def test_judgments_integra_el_consenso_completo(self):
        corr = {f["blind_id"]: f for f in CORRESPONDENCIA["filas"]}
        cons = {f["blind_id"]: f for f in CONSENSO["decisiones"]}
        self.assertEqual(set(JUDGMENTS["defectos"]), set())
        filas = JUDGMENTS["adjudicaciones"]
        self.assertEqual(len(filas), 62)
        for fila in filas:
            self.assertLessEqual(set(fila), CLAVES_FILA_E0)
            coincidentes = [
                b
                for b, f in corr.items()
                if f["caso"] == fila["caso"] and f["hallazgo_id"] == fila["hallazgo"]
            ]
            self.assertEqual(len(coincidentes), 1, fila)
            self.assertEqual(fila["veredicto"], cons[coincidentes[0]]["veredicto"])
        self.assertEqual(len({(f["caso"], f["hallazgo"]) for f in filas}), 62)
        conteo = {"valid": 0, "false_positive": 0, "duplicate": 0, "unresolved": 0}
        for fila in filas:
            conteo[fila["veredicto"]] += 1
        self.assertEqual(
            conteo, {"valid": 39, "false_positive": 22, "duplicate": 1, "unresolved": 0}
        )
        dup = [f for f in filas if f["veredicto"] == "duplicate"]
        self.assertEqual(len(dup), 1)
        destino = corr[
            cons[
                [
                    b
                    for b, f in corr.items()
                    if (f["caso"], f["hallazgo_id"])
                    == (dup[0]["caso"], dup[0]["hallazgo"])
                ][0]
            ]["duplicado_de"]
        ]
        self.assertEqual(dup[0]["duplicado_de"], destino["hallazgo_id"])
        self.assertEqual(dup[0]["caso"], destino["caso"])

    def test_sin_campos_de_procedencia_en_consenso_ni_hoja(self):
        for f in CONSENSO["decisiones"]:
            for clave in CLAVES_CONSENSO - {"razon"}:
                valor = json.dumps(f.get(clave), ensure_ascii=False).lower()
                for token in TOKENS_PROCEDENCIA:
                    self.assertNotIn(token, valor, f"{f['blind_id']}/{clave}")
        texto_meta = json.dumps(CONSENSO["meta"], ensure_ascii=False).lower()
        for token in TOKENS_PROCEDENCIA:
            self.assertNotIn(token, texto_meta)
        for h in HOJA["hallazgos"]:
            self.assertEqual(
                set(h),
                {
                    "blind_id",
                    "caso",
                    "head",
                    "severidad",
                    "titulo",
                    "ruta",
                    "linea",
                    "detalle",
                    "contexto",
                },
            )

    def test_meta_del_consenso_fija_la_hoja(self):
        self.assertEqual(
            CONSENSO["meta"]["hoja_sha256"], sha256(RAIZ / "adjudicacion-ciega.json")
        )


if __name__ == "__main__":
    unittest.main()
