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


CLAVES_FILA_V2 = {
    "blind_id",
    "caso",
    "head",
    "diagnostico",
    "ubicacion",
    "evidencia",
    "severidad",
}
CLAVES_CORRESPONDENCIA_V2 = {
    "blind_id",
    "caso",
    "repo",
    "base",
    "head",
    "tarea",
    "sha_anterior",
    "producto",
    "configuracion",
    "repeticion",
    "intento",
    "hallazgo_id",
    "severidad_original",
    "particion",
}
TOKENS_FUGA_V2 = [
    "tool-a",
    "tool-b",
    "🤖",
    "<details>",
    "####",
    "addressed",
    "herramienta",
    "🩺",
    "⚡",
    "🟡",
    "⚪",
    "quick win",
    "minor",
    "medium",
]
PAIRING = {
    "meta": {"que_es": "pares fijados antes de observar resultados"},
    "experimentos": [],
    "adjudicacion": {
        "normalizacion": "1",
        "jueces": [{"id": "j1", "tipo": "ia", "modelo": "modelo-de-prueba"}],
        "desempates": {"version": "1", "regla": "unresolved si persiste"},
    },
}


def hallazgo(identificador, titulo, ruta, linea, severidad, detalle, resuelto=False):
    return {
        "id": identificador,
        "titulo": titulo,
        "ruta": ruta,
        "linea": linea,
        "severidad": severidad,
        "resuelto": resuelto,
        "detalle": detalle,
    }


def observacion(
    caso, head, hallazgos, producto="tool-a", configuracion="ca", sha_anterior=None
):
    return {
        "caso": caso,
        "repo": "gon0801/prueba",
        "base": "0" * 40,
        "head": head,
        "tarea": "revisar el PR",
        "sha_anterior": sha_anterior,
        "producto": producto,
        "configuracion": configuracion,
        "repeticion": 1,
        "intento": 1,
        "resultado": "success",
        "cobertura": "complete",
        "duracion_s": 12.5,
        "turnos": 3,
        "costo_usd": 0.01,
        "hallazgos": hallazgos,
    }


def raiz_v2_minima(tmp, observaciones, particion, pairing):
    raiz_v2 = Path(tmp) / "v2"
    raiz_v2.mkdir()
    (raiz_v2 / "observaciones.json").write_text(
        json.dumps({"observaciones": observaciones}, ensure_ascii=False)
    )
    (raiz_v2 / "partition.json").write_text(
        json.dumps(
            {"meta": {"que_es": "asignación de PRs completos"}, "particion": particion}
        )
    )
    (raiz_v2 / "pairing.json").write_text(json.dumps(pairing, ensure_ascii=False))
    return raiz_v2


def correr_v2(raiz_v2):
    return subprocess.run(
        [sys.executable, str(BUILDER), "--versionado", "--raiz-v2", str(raiz_v2)],
        capture_output=True,
        text=True,
    )


class BlindJudgmentsV2(unittest.TestCase):
    def test_removes_product_cues(self):
        obs = [
            observacion(
                "c1",
                "a" * 40,
                [
                    hallazgo(
                        "c1-a01",
                        "Alucina el índice",
                        "x.py",
                        7,
                        "Major",
                        "Cuidado con 🤖 y <details>marca de herramienta</details> en "
                        "el texto ✅ Addressed in commit a1b2c3d",
                    ),
                    hallazgo(
                        "c1-a02",
                        "Alinea el orden de los campos",
                        "x.py",
                        9,
                        "Low",
                        "El inventario de campos no coincide con el aplicado por "
                        "Tool-B",
                    ),
                    hallazgo(
                        "c1-a03",
                        "Orden declarado divergente",
                        "plan.md",
                        8,
                        "Medium",
                        "- 🟡 Medium · `Plans.md:8` · el orden declarado no coincide",
                    ),
                    hallazgo(
                        "c1-a04",
                        "Recorte silencioso de defectos conocidos",
                        "compare_reviews.py",
                        109,
                        "Medium",
                        "- 🟡 **Medium** · `scripts/compare_reviews.py:109` · el "
                        "conjunto de defectos conocidos se rellena a mano",
                    ),
                ],
                producto="tool-a",
                configuracion="ca",
            ),
            observacion(
                "c2",
                "b" * 40,
                [
                    hallazgo(
                        "c2-b01",
                        "Fuera de rango",
                        "y.py",
                        3,
                        "Nitpick",
                        "Revisar el límite del bucle que tool-a marcó",
                    ),
                    hallazgo(
                        "c2-b02",
                        "Estabilidad y disponibilidad",
                        "z.py",
                        5,
                        "Minor",
                        "_🩺 Stability & Availability_ | _🟡 Minor_ | _⚡ Quick win_",
                    ),
                    hallazgo(
                        "c2-b03",
                        "Autoridad del ajuste",
                        "autopilot.json",
                        1,
                        "Low",
                        "dejarlo escrito con esa razón. #### ⚪ Low · "
                        "`.saikit/autopilot.json:1` · La autoridad del ajuste no "
                        "es la ficha",
                    ),
                ],
                producto="tool-b",
                configuracion="cb",
            ),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            raiz_v2 = raiz_v2_minima(
                tmp, obs, {"c1": "ajuste", "c2": "reservada"}, PAIRING
            )
            r = correr_v2(raiz_v2)
            self.assertEqual(r.returncode, 0, r.stderr)
            hoja = json.loads((raiz_v2 / "adjudicacion-ciega.json").read_text())
            correspondencia = json.loads(
                (raiz_v2 / "ciego-correspondencia.json").read_text()
            )
        for fila in hoja["hallazgos"]:
            self.assertEqual(set(fila), CLAVES_FILA_V2)
        self.assertEqual(
            sorted(f["severidad"] for f in hoja["hallazgos"]),
            ["baja", "baja", "baja", "baja", "media", "media", "media"],
        )
        with self.subTest(marca="severidad en negrita"):
            fila_negrita = next(
                f
                for f in hoja["hallazgos"]
                if f["diagnostico"] == "Recorte silencioso de defectos conocidos"
            )
            self.assertEqual(
                fila_negrita["evidencia"],
                "`scripts/compare_reviews.py:109` · el conjunto de defectos "
                "conocidos se rellena a mano",
            )
        with self.subTest(marca="severidad tras marcador de encabezado"):
            fila_encabezado = next(
                f
                for f in hoja["hallazgos"]
                if f["diagnostico"] == "Autoridad del ajuste"
            )
            self.assertEqual(
                fila_encabezado["evidencia"],
                "dejarlo escrito con esa razón. `.saikit/autopilot.json:1` · La "
                "autoridad del ajuste no es la ficha",
            )
        texto = json.dumps(hoja["hallazgos"], ensure_ascii=False).lower()
        for token in TOKENS_FUGA_V2:
            self.assertNotIn(token, texto, token)
        self.assertIsNone(re.search(r"\bca\b", texto))
        fila_plan = next(
            f
            for f in hoja["hallazgos"]
            if f["diagnostico"] == "Orden declarado divergente"
        )
        self.assertEqual(
            fila_plan["evidencia"],
            "`Plans.md:8` · el orden declarado no coincide",
        )
        fila_badges = next(
            f
            for f in hoja["hallazgos"]
            if f["diagnostico"] == "Estabilidad y disponibilidad"
        )
        self.assertEqual(fila_badges["evidencia"], fila_badges["diagnostico"])
        for fila in correspondencia["filas"]:
            self.assertEqual(set(fila), CLAVES_CORRESPONDENCIA_V2)
        self.assertEqual(
            sorted(f["severidad_original"] for f in correspondencia["filas"]),
            ["Low", "Low", "Major", "Medium", "Medium", "Minor", "Nitpick"],
        )
        self.assertEqual(
            {f["producto"] for f in correspondencia["filas"]}, {"tool-a", "tool-b"}
        )
        self.assertIs(hoja["meta"]["adjudicacion_ia"], True)
        self.assertEqual(
            hoja["meta"]["jueces"],
            [{"id": "j1", "tipo": "ia", "modelo": "modelo-de-prueba"}],
        )
        self.assertEqual(hoja["meta"]["version"], 2)
        self.assertEqual(hoja["meta"]["normalizacion"], "1")

    def test_all_pushes_share_partition(self):
        obs = [
            observacion(
                "c1",
                "a" * 40,
                [hallazgo("c1-a01", "Primera", "a.py", 1, "Major", "detalle 1")],
            ),
            observacion(
                "c1",
                "b" * 40,
                [hallazgo("c1-a02", "Segunda", "a.py", 2, "Major", "detalle 2")],
                sha_anterior="a" * 40,
            ),
            observacion(
                "c1",
                "c" * 40,
                [hallazgo("c1-a03", "Tercera", "a.py", 3, "Major", "detalle 3")],
                sha_anterior="b" * 40,
            ),
            observacion(
                "c2",
                "d" * 40,
                [hallazgo("c2-a01", "Última", "b.py", 1, "Major", "detalle 4")],
            ),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            raiz_v2 = raiz_v2_minima(
                tmp, obs, {"c1": "ajuste", "c2": "reservada"}, PAIRING
            )
            r = correr_v2(raiz_v2)
            self.assertEqual(r.returncode, 0, r.stderr)
            correspondencia = json.loads(
                (raiz_v2 / "ciego-correspondencia.json").read_text()
            )
            filas_c1 = [f for f in correspondencia["filas"] if f["caso"] == "c1"]
            filas_c2 = [f for f in correspondencia["filas"] if f["caso"] == "c2"]
            self.assertEqual(len(filas_c1), 3)
            self.assertEqual(len(filas_c2), 1)
            self.assertEqual({f["particion"] for f in filas_c1}, {"ajuste"})
            self.assertEqual({f["particion"] for f in filas_c2}, {"reservada"})
        with tempfile.TemporaryDirectory() as tmp:
            raiz_v2 = raiz_v2_minima(tmp, obs, {"c1": "ajuste"}, PAIRING)
            r = correr_v2(raiz_v2)
            self.assertNotEqual(r.returncode, 0)
            self.assertTrue(r.stderr.startswith("build_adjudicacion_ciega: "), r.stderr)

    def test_historical_outputs_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_raiz = Path(tmp) / "reviewer"
            tmp_raiz.mkdir()
            shutil.copy2(RAIZ / "corpus.json", tmp_raiz / "corpus.json")
            shutil.copytree(RAIZ / "salidas", tmp_raiz / "salidas")
            r_historico = subprocess.run(
                [sys.executable, str(BUILDER), "--raiz", str(tmp_raiz)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(r_historico.returncode, 0, r_historico.stderr)
            obs = [
                observacion(
                    "c1",
                    "a" * 40,
                    [hallazgo("c1-a01", "Nulo", "a.py", 1, "Minor", "detalle")],
                )
            ]
            raiz_v2 = raiz_v2_minima(tmp_raiz, obs, {"c1": "ajuste"}, PAIRING)
            r_v2 = correr_v2(raiz_v2)
            self.assertEqual(r_v2.returncode, 0, r_v2.stderr)
            for nombre in (
                "adjudicacion-ciega.json",
                "ciego-correspondencia.json",
                "observaciones.json",
            ):
                self.assertEqual(
                    (tmp_raiz / nombre).read_bytes(),
                    (RAIZ / nombre).read_bytes(),
                    nombre,
                )
            self.assertTrue((raiz_v2 / "adjudicacion-ciega.json").exists())
            self.assertTrue((raiz_v2 / "ciego-correspondencia.json").exists())

    def test_accepts_inventory_of_21_cases(self):
        casos = [f"c{n:02d}" for n in range(1, 22)]
        observaciones = [
            observacion(
                caso,
                f"{n:040d}",
                [hallazgo(f"{caso}-01", "Repetido", "a.py", 1, "Minor", "detalle")],
            )
            for n, caso in enumerate(casos, start=1)
        ]
        particion = {
            caso: ("ajuste" if n % 2 else "reservada")
            for n, caso in enumerate(casos, start=1)
        }
        with tempfile.TemporaryDirectory() as tmp_a:
            raiz_a = raiz_v2_minima(tmp_a, observaciones, particion, PAIRING)
            r_a = correr_v2(raiz_a)
            self.assertEqual(r_a.returncode, 0, r_a.stderr)
            hoja_bytes_a = (raiz_a / "adjudicacion-ciega.json").read_bytes()
            corr_bytes_a = (raiz_a / "ciego-correspondencia.json").read_bytes()
        with tempfile.TemporaryDirectory() as tmp_b:
            raiz_b = raiz_v2_minima(tmp_b, observaciones, particion, PAIRING)
            r_b = correr_v2(raiz_b)
            self.assertEqual(r_b.returncode, 0, r_b.stderr)
            hoja_bytes_b = (raiz_b / "adjudicacion-ciega.json").read_bytes()
            corr_bytes_b = (raiz_b / "ciego-correspondencia.json").read_bytes()
        self.assertEqual(hoja_bytes_a, hoja_bytes_b)
        self.assertEqual(corr_bytes_a, corr_bytes_b)
        self.assertEqual(len(json.loads(hoja_bytes_a)["hallazgos"]), 21)

    def test_redacta_identificadores_del_inventario_por_longitud(self):
        obs = [
            observacion(
                "c1",
                "a" * 40,
                [
                    hallazgo(
                        "c1-a01",
                        "Límite del bucle",
                        "a.py",
                        1,
                        "Minor",
                        "tool marca el límite del bucle",
                    )
                ],
                producto="tool",
                configuracion="v1",
            ),
            observacion(
                "c2",
                "b" * 40,
                [
                    hallazgo(
                        "c2-a01",
                        "Cobertura del arreglo",
                        "b.py",
                        2,
                        "Major",
                        "tool-plus resuelve lo que tool abre",
                    )
                ],
                producto="tool-plus",
                configuracion="v1",
            ),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            raiz_v2 = raiz_v2_minima(
                tmp, obs, {"c1": "ajuste", "c2": "reservada"}, PAIRING
            )
            r = correr_v2(raiz_v2)
            self.assertEqual(r.returncode, 0, r.stderr)
            hoja = json.loads((raiz_v2 / "adjudicacion-ciega.json").read_text())
        texto = json.dumps(hoja["hallazgos"], ensure_ascii=False).lower()
        self.assertNotIn("[redactado]-plus", texto)
        self.assertNotIn("tool", texto)


if __name__ == "__main__":
    unittest.main()
