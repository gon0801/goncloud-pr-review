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


def correr(corpus, obs, judg, pairing=None):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "corpus.json").write_text(json.dumps(corpus))
        (tmp / "observaciones.json").write_text(json.dumps(obs))
        (tmp / "judgments.json").write_text(json.dumps(judg))
        salida = tmp / "informe.json"
        comando = [
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
        ]
        if pairing is not None:
            (tmp / "pairing.json").write_text(json.dumps(pairing))
            comando += ["--pairing", str(tmp / "pairing.json")]
        r = subprocess.run(
            comando,
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
        self.assertEqual(
            informe["cobertura_declarada"],
            [
                {
                    "caso": "c1",
                    "producto": "revisor local",
                    "configuracion": "v1",
                    "intento": 1,
                    "cobertura": "complete",
                }
            ],
        )
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


def caso_simple(c):
    return {
        "caso": c,
        "repo": "o/r",
        "base": BASE,
        "head": HEAD,
        "producto": "revisor local",
        "configuracion": "v1",
        "intento": 1,
    }


def obs_simple(c, hallazgo):
    return {
        "caso": c,
        "repo": "o/r",
        "base": BASE,
        "head": HEAD,
        "producto": "revisor local",
        "configuracion": "v1",
        "intento": 1,
        "resultado": "success",
        "cobertura": "complete",
        "duracion_s": 10.0,
        "turnos": 2,
        "costo_usd": None,
        "hallazgos": [
            {"id": hallazgo, "titulo": "t", "ruta": "x.py", "resuelto": False}
        ],
    }


class DefectosPorCaso(unittest.TestCase):
    def test_ids_repetidos_entre_casos_no_se_fusionan(self):
        corpus = {"casos": [caso_simple("c1"), caso_simple("c2")]}
        obs = {"observaciones": [obs_simple("c1", "F1"), obs_simple("c2", "G1")]}
        judg = {
            "adjudicaciones": [
                {
                    "caso": "c1",
                    "hallazgo": "F1",
                    "veredicto": "valid",
                    "defecto": "D1",
                },
                {"caso": "c2", "hallazgo": "G1", "veredicto": "false_positive"},
            ],
            "defectos": {"c1": ["D1"], "c2": ["D1"]},
        }
        r, informe = correr(corpus, obs, judg)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(informe["defectos_conocidos"]["conocidos"], 2)
        self.assertEqual(informe["defectos_conocidos"]["detectados"], 1)
        self.assertEqual(informe["defectos_conocidos"]["omitidos"], ["c2/D1"])
        self.assertEqual(informe["defectos_conocidos"]["recuperacion"], 0.5)

    def test_defecto_acreditado_fuera_de_su_caso_se_rechaza(self):
        corpus = {"casos": [caso_simple("c1"), caso_simple("c2")]}
        obs = {"observaciones": [obs_simple("c1", "F1"), obs_simple("c2", "G1")]}
        judg = {
            "adjudicaciones": [
                {"caso": "c1", "hallazgo": "F1", "veredicto": "false_positive"},
                {
                    "caso": "c2",
                    "hallazgo": "G1",
                    "veredicto": "valid",
                    "defecto": "D9",
                },
            ],
            "defectos": {"c1": ["D9"], "c2": ["D2"]},
        }
        r, _ = correr(corpus, obs, judg)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("D9", r.stderr)
        self.assertIn("c2", r.stderr)


class ClaveMultiProducto(unittest.TestCase):
    def test_acepta_mismo_caso_con_clave_distinta_y_ambas_cuentan(self):
        corpus = {"casos": [caso_simple("c1")]}
        o1 = obs_simple("c1", "F1")
        o2 = obs_simple("c1", "G1")
        o2["intento"] = 2
        o3 = obs_simple("c1", "H1")
        o3["producto"] = "coderabbit"
        o3["configuracion"] = "comentarios existentes"
        o3["duracion_s"] = None
        o3["turnos"] = None
        judg = {
            "adjudicaciones": [
                {"caso": "c1", "hallazgo": "F1", "veredicto": "valid"},
                {"caso": "c1", "hallazgo": "G1", "veredicto": "false_positive"},
                {"caso": "c1", "hallazgo": "H1", "veredicto": "valid"},
            ],
            "defectos": {},
        }
        r, informe = correr(corpus, {"observaciones": [o1, o2, o3]}, judg)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(informe["observaciones"], 3)
        self.assertEqual(informe["hallazgos"]["total"], 3)
        self.assertEqual(informe["precision"]["validos"], 2)
        self.assertEqual(len(informe["cobertura_declarada"]), 3)
        self.assertEqual(informe["tiempo"]["duracion_total_s"], 20.0)
        self.assertEqual(informe["tiempo"]["duracion_desconocidas"], 1)
        self.assertEqual(informe["tiempo"]["turnos_total"], 4)
        self.assertEqual(informe["tiempo"]["turnos_desconocidos"], 1)

    def test_rechaza_misma_clave_completa_duplicada(self):
        corpus = {"casos": [caso_simple("c1")]}
        o1 = obs_simple("c1", "F1")
        o2 = obs_simple("c1", "G1")
        r, _ = correr(
            corpus, {"observaciones": [o1, o2]}, {"adjudicaciones": [], "defectos": {}}
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("duplicad", r.stderr)

    def test_rechaza_hallazgo_repetido_entre_observaciones_del_mismo_caso(self):
        corpus = {"casos": [caso_simple("c1")]}
        o1 = obs_simple("c1", "F1")
        o2 = obs_simple("c1", "F1")
        o2["intento"] = 2
        judg = {
            "adjudicaciones": [{"caso": "c1", "hallazgo": "F1", "veredicto": "valid"}],
            "defectos": {},
        }
        r, _ = correr(corpus, {"observaciones": [o1, o2]}, judg)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("F1", r.stderr)

    def test_coberturas_con_barra_no_colisionan_y_el_orden_es_determinista(self):
        corpus = {"casos": [caso_simple("c1")]}
        o1 = obs_simple("c1", "F1")
        o1["producto"] = "a/b"
        o1["configuracion"] = "c"
        o2 = obs_simple("c1", "G1")
        o2["producto"] = "a"
        o2["configuracion"] = "b/c"
        judg = {
            "adjudicaciones": [
                {"caso": "c1", "hallazgo": "F1", "veredicto": "valid"},
                {"caso": "c1", "hallazgo": "G1", "veredicto": "valid"},
            ],
            "defectos": {},
        }
        r, informe = correr(corpus, {"observaciones": [o1, o2]}, judg)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(informe["observaciones"], 2)
        self.assertEqual(
            informe["cobertura_declarada"],
            [
                {
                    "caso": "c1",
                    "producto": "a",
                    "configuracion": "b/c",
                    "intento": 1,
                    "cobertura": "complete",
                },
                {
                    "caso": "c1",
                    "producto": "a/b",
                    "configuracion": "c",
                    "intento": 1,
                    "cobertura": "complete",
                },
            ],
        )


def caso_v2():
    return {
        "caso": "c1",
        "repo": "o/r",
        "base": BASE,
        "head": HEAD,
        "tarea": "entre-modulos",
        "sha_anterior": None,
    }


def obs_v2(producto, ids, **cambios):
    obs = {
        **caso_v2(),
        "producto": producto,
        "configuracion": "v1",
        "repeticion": 1,
        "intento": 1,
        "resultado": "success",
        "cobertura": "complete",
        "duracion_s": 10.0,
        "turnos": 2,
        "costo_usd": None,
        "hallazgos": [
            {"id": i, "titulo": "t", "ruta": "x.py", "resuelto": False} for i in ids
        ],
    }
    obs.update(cambios)
    return obs


def fila_v2(obs, hallazgo, veredicto, **extra):
    fila = {
        "caso": obs["caso"],
        "repo": obs["repo"],
        "base": obs["base"],
        "head": obs["head"],
        "tarea": obs["tarea"],
        "sha_anterior": obs["sha_anterior"],
        "producto": obs["producto"],
        "configuracion": obs["configuracion"],
        "repeticion": obs["repeticion"],
        "intento": obs["intento"],
        "hallazgo": hallazgo,
        "veredicto": veredicto,
    }
    fila.update(extra)
    return fila


def lado(producto, intento=1):
    return {"producto": producto, "configuracion": "v1", "intento": intento}


def par_v2(control, variante, **cambios):
    par = {
        **caso_v2(),
        "repeticion": 1,
        "control": control,
        "variante": variante,
    }
    par.update(cambios)
    return par


def pairing_v2(*pares):
    return {"experimentos": [{"experimento": "e1", "pares": list(pares)}]}


def inventario_proyectado_v2():
    ruta = ROOT / "evaluation" / "reviewer" / "corpus.json"
    casos = json.loads(ruta.read_text())["casos"]
    inventario = {}
    for caso in casos:
        inventario[caso["caso"]] = {
            "repo": caso["repo"],
            "base": caso["base"],
            "head": caso["head"],
            "tarea": caso["categoria"],
            "sha_anterior": None,
        }
        pushes = caso.get("pushes") or []
        for i in range(1, len(pushes)):
            inventario[f"{caso['caso']}-push-{i}"] = {
                "repo": caso["repo"],
                "base": caso["base"],
                "head": pushes[i],
                "tarea": caso["categoria"],
                "sha_anterior": pushes[i - 1],
            }
    return inventario


class ComparisonV2(unittest.TestCase):
    def test_products_keep_separate_precision(self):
        alfa = obs_v2("alfa", ["F1", "F2"])
        beta = obs_v2("beta", ["G1", "G2"])
        judg = {
            "adjudicaciones": [
                fila_v2(alfa, "F1", "valid"),
                fila_v2(alfa, "F2", "false_positive"),
                fila_v2(beta, "G1", "valid"),
                fila_v2(beta, "G2", "valid"),
            ]
        }
        r, informe = correr(
            {"casos": [caso_v2()]},
            {"observaciones": [alfa, beta]},
            judg,
            pairing_v2(par_v2(lado("alfa"), lado("beta"))),
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(
            [g["producto"] for g in informe["productos"]], ["alfa", "beta"]
        )
        grupos = {g["producto"]: g for g in informe["productos"]}
        self.assertEqual(grupos["alfa"]["precision"]["valor"], 0.5)
        self.assertEqual(grupos["alfa"]["precision"]["denominador"], 2)
        self.assertEqual(grupos["beta"]["precision"]["valor"], 1.0)
        self.assertEqual(grupos["beta"]["precision"]["denominador"], 2)
        self.assertNotIn("precision", informe)
        self.assertNotIn("precision_global", informe)

    def test_full_key_adjudication_and_rejects_ambiguous_legacy(self):
        alfa = obs_v2("alfa", ["F1"])
        beta = obs_v2("beta", ["F1"])
        corpus = {"casos": [caso_v2()]}
        observaciones = {"observaciones": [alfa, beta]}
        pairing = pairing_v2(par_v2(lado("alfa"), lado("beta")))
        judg = {
            "adjudicaciones": [
                fila_v2(alfa, "F1", "valid"),
                fila_v2(beta, "F1", "false_positive"),
            ]
        }
        r, informe = correr(corpus, observaciones, judg, pairing)
        self.assertEqual(r.returncode, 0, r.stderr)
        grupos = {g["producto"]: g for g in informe["productos"]}
        self.assertEqual(grupos["alfa"]["hallazgos"]["validos"], 1)
        self.assertEqual(grupos["beta"]["hallazgos"]["falsos_positivos"], 1)
        judg_legado = {
            "adjudicaciones": [{"caso": "c1", "hallazgo": "F1", "veredicto": "valid"}]
        }
        r, _ = correr(corpus, observaciones, judg_legado, pairing)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("ambigua", r.stderr)

    def test_pair_with_differing_keys_rejected_and_observations_unmatched(self):
        observaciones = {
            "observaciones": [
                obs_v2("alfa", []),
                obs_v2("beta", []),
                obs_v2("alfa", [], repeticion=2),
                obs_v2("beta", [], repeticion=2),
                obs_v2("gama", []),
            ]
        }
        corpus = {"casos": [caso_v2()]}
        judg = {"adjudicaciones": []}
        pairing = pairing_v2(
            par_v2(lado("alfa"), lado("beta"), repo="o/otro"),
            par_v2(lado("alfa"), lado("beta"), head="9" * 40, repeticion=2),
        )
        r, informe = correr(corpus, observaciones, judg, pairing)
        self.assertEqual(r.returncode, 0, r.stderr)
        bloque = informe["pares"][0]
        self.assertEqual(bloque["pares_evaluados"], 0)
        self.assertEqual(len(bloque["pares_rechazados"]), 2)
        for rechazo in bloque["pares_rechazados"]:
            self.assertTrue(rechazo["motivo"])
        self.assertEqual(bloque["cohorte"]["comparables"], 0)
        self.assertEqual(len(informe["sin_pareja"]), 5)
        for sin in informe["sin_pareja"]:
            self.assertTrue(sin["motivo"])
        productos_sin_pareja = {s["producto"] for s in informe["sin_pareja"]}
        self.assertEqual(productos_sin_pareja, {"alfa", "beta", "gama"})
        for campo, cambio in (
            ("repo", {"repo": "o/otro"}),
            ("base", {"base": "8" * 40}),
            ("head", {"head": "9" * 40}),
            ("tarea", {"tarea": "otra"}),
            ("sha_anterior", {"sha_anterior": "c" * 40}),
        ):
            with self.subTest(campo=campo):
                r, informe = correr(
                    corpus,
                    observaciones,
                    judg,
                    pairing_v2(par_v2(lado("alfa"), lado("beta"), **cambio)),
                )
                self.assertEqual(r.returncode, 0, r.stderr)
                bloque = informe["pares"][0]
                self.assertEqual(bloque["pares_evaluados"], 0)
                self.assertEqual(len(bloque["pares_rechazados"]), 1)
                self.assertTrue(bloque["pares_rechazados"][0]["motivo"])

        with self.subTest(lado="contrapartes idénticas"):
            r, informe = correr(
                corpus,
                observaciones,
                judg,
                pairing_v2(par_v2(lado("alfa"), lado("alfa"))),
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            bloque = informe["pares"][0]
            self.assertEqual(bloque["pares_evaluados"], 0)
            self.assertEqual(len(bloque["pares_rechazados"]), 1)
            self.assertIn(
                "contrapartes idénticas",
                bloque["pares_rechazados"][0]["motivo"],
            )
            rechazada = [
                s
                for s in informe["sin_pareja"]
                if s["producto"] == "alfa"
                and s["repeticion"] == 1
                and s["intento"] == 1
            ]
            self.assertEqual(len(rechazada), 1)
            self.assertIn("contrapartes idénticas", rechazada[0]["motivo"])

    def test_failed_attempt_then_success_reports_request_duration_and_unknown_cost(
        self,
    ):
        fallido = obs_v2("alfa", [], resultado="error", duracion_s=10.0)
        exitoso = obs_v2("alfa", ["X1"], intento=2, duracion_s=20.0)
        beta = obs_v2("beta", ["Y1"])
        judg = {
            "adjudicaciones": [
                fila_v2(exitoso, "X1", "valid"),
                fila_v2(beta, "Y1", "valid"),
            ]
        }
        r, informe = correr(
            {"casos": [caso_v2()]},
            {"observaciones": [fallido, exitoso, beta]},
            judg,
            pairing_v2(par_v2(lado("alfa", intento=2), lado("beta"))),
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        alfa = {g["producto"]: g for g in informe["productos"]}["alfa"]
        self.assertEqual(alfa["intentos"]["total"], 2)
        self.assertEqual(alfa["intentos"]["exitosos"], 1)
        self.assertEqual(alfa["intentos"]["fallidos"], 1)
        self.assertEqual(alfa["intentos"]["tasa_fallos"], 0.5)
        self.assertEqual(alfa["solicitudes"]["total"], 1)
        solicitud = alfa["solicitudes"]["por_solicitud"][0]
        self.assertEqual(solicitud["caso"], "c1")
        self.assertEqual(solicitud["repeticion"], 1)
        self.assertEqual(solicitud["intentos"], 2)
        self.assertEqual(solicitud["fallidos"], 1)
        self.assertEqual(solicitud["duracion_s"], 30.0)
        self.assertEqual(
            sorted(d["duracion_s"] for d in solicitud["intentos_detalle"]),
            [10.0, 20.0],
        )
        self.assertEqual(alfa["costo"]["usd_conocido"], None)
        self.assertEqual(alfa["costo"]["desconocidos"], 2)

    def test_unadjudicated_duplicates_and_missing_defect_reference(self):
        alfa = obs_v2("alfa", ["H1", "H2", "H3"])
        beta = obs_v2("beta", ["K1"])
        judg = {
            "adjudicaciones": [
                fila_v2(alfa, "H1", "valid"),
                fila_v2(alfa, "H2", "duplicate", duplicado_de="H1"),
                fila_v2(beta, "K1", "valid"),
            ]
        }
        r, informe = correr(
            {"casos": [caso_v2()]},
            {"observaciones": [alfa, beta]},
            judg,
            pairing_v2(par_v2(lado("alfa"), lado("beta"))),
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        grupos = {g["producto"]: g for g in informe["productos"]}
        self.assertEqual(grupos["alfa"]["precision"]["valor"], 0.5)
        self.assertEqual(grupos["alfa"]["precision"]["validos"], 1)
        self.assertEqual(grupos["alfa"]["precision"]["denominador"], 2)
        self.assertEqual(grupos["alfa"]["hallazgos"]["sin_adjudicar"], 1)
        self.assertEqual(grupos["alfa"]["hallazgos"]["duplicados"], 1)
        self.assertEqual(grupos["alfa"]["defectos"]["conjunto_presente"], False)
        self.assertEqual(grupos["alfa"]["defectos"]["recuperacion"], "desconocido")
        self.assertEqual(grupos["beta"]["precision"]["valor"], 1.0)
        self.assertEqual(grupos["beta"]["precision"]["denominador"], 1)
        judg_autoduplicado = {
            "adjudicaciones": [
                fila_v2(alfa, "H2", "duplicate", duplicado_de="H2"),
            ]
        }
        r, _ = correr(
            {"casos": [caso_v2()]},
            {"observaciones": [alfa, beta]},
            judg_autoduplicado,
            pairing_v2(par_v2(lado("alfa"), lado("beta"))),
        )
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("duplicate", r.stderr)

    def test_sin_ejecucion_no_infla_tasa_de_fallos(self):
        comentarios = obs_v2(
            "gama",
            ["Z1"],
            resultado="comentarios existentes",
            duracion_s=None,
            turnos=None,
            costo_usd=None,
        )
        fallido = obs_v2("alfa", [], resultado="error", duracion_s=10.0)
        exitoso = obs_v2("alfa", ["X1"], intento=2, duracion_s=20.0)
        judg = {
            "adjudicaciones": [
                fila_v2(comentarios, "Z1", "valid"),
                fila_v2(exitoso, "X1", "valid"),
            ]
        }
        r, informe = correr(
            {"casos": [caso_v2()]},
            {"observaciones": [comentarios, fallido, exitoso]},
            judg,
            pairing_v2(par_v2(lado("alfa", intento=2), lado("gama"))),
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        grupos = {g["producto"]: g for g in informe["productos"]}
        gama = grupos["gama"]
        self.assertEqual(
            gama["intentos"],
            {"total": 0, "exitosos": 0, "fallidos": 0, "tasa_fallos": None},
        )
        self.assertEqual(gama["solicitudes"], {"total": 0, "por_solicitud": []})
        self.assertEqual(gama["sin_ejecucion"], 1)
        self.assertEqual(gama["observaciones"], 1)
        self.assertEqual(gama["precision"]["valor"], 1.0)
        self.assertEqual(gama["precision"]["denominador"], 1)
        alfa = grupos["alfa"]
        self.assertEqual(
            alfa["intentos"],
            {"total": 2, "exitosos": 1, "fallidos": 1, "tasa_fallos": 0.5},
        )
        self.assertEqual(alfa["solicitudes"]["por_solicitud"][0]["duracion_s"], 30.0)

    def test_duplicate_entre_observaciones_del_mismo_caso(self):
        configuracion_cr = (
            "comentarios existentes en el SHA exacto; sin revisión disparada"
        )
        coderabbit = obs_v2(
            "coderabbit",
            ["C02"],
            configuracion=configuracion_cr,
            resultado="comentarios existentes",
            cobertura="no declarada (comentarios existentes)",
            duracion_s=None,
            turnos=None,
            costo_usd=None,
        )
        revisor = obs_v2("revisor", ["R02"])
        judg = {
            "adjudicaciones": [
                fila_v2(revisor, "R02", "valid"),
                {
                    "caso": "c1",
                    "hallazgo": "C02",
                    "veredicto": "duplicate",
                    "duplicado_de": "R02",
                },
            ]
        }
        pairing = pairing_v2(
            par_v2(
                lado("revisor"),
                {
                    "producto": "coderabbit",
                    "configuracion": configuracion_cr,
                    "intento": 1,
                },
            )
        )
        r, informe = correr(
            {"casos": [caso_v2()]},
            {"observaciones": [coderabbit, revisor]},
            judg,
            pairing,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(informe["pares"][0]["pares_evaluados"], 1)
        grupos = {g["producto"]: g for g in informe["productos"]}
        self.assertEqual(grupos["coderabbit"]["hallazgos"]["duplicados"], 1)
        self.assertEqual(grupos["coderabbit"]["precision"]["denominador"], 1)
        self.assertEqual(grupos["coderabbit"]["precision"]["valor"], 0.0)
        self.assertEqual(grupos["revisor"]["precision"]["valor"], 1.0)
        self.assertEqual(grupos["revisor"]["precision"]["denominador"], 1)

        with self.subTest(duplicado_de="resuelve a dos observaciones"):
            reintento = obs_v2("revisor", ["R02"], intento=2)
            r, _ = correr(
                {"casos": [caso_v2()]},
                {"observaciones": [coderabbit, revisor, reintento]},
                judg,
                pairing,
            )
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("ambiguo", r.stderr)

    def test_pairing_congelado_cubre_pushes_con_casos_propios(self):
        inventario = inventario_proyectado_v2()
        self.assertEqual(len(inventario), 26)
        particion = json.loads(
            (ROOT / "evaluation" / "reviewer" / "v2" / "partition.json").read_text()
        )["particion"]
        pairing = json.loads(
            (ROOT / "evaluation" / "reviewer" / "v2" / "pairing.json").read_text()
        )
        por_par = {}
        for experimento in pairing["experimentos"]:
            for par in experimento["pares"]:
                clave = f"{experimento['experimento']}/{par['caso']}"
                if experimento["experimento"] == "dependencia-control-push-0":
                    with self.subTest(dependencia=clave):
                        self.assertNotIn(
                            par["caso"],
                            inventario,
                            "la dependencia no es un caso proyectado",
                        )
                        self.assertNotIn(
                            par["caso"],
                            particion,
                            "la dependencia no genera observaciones: no va en la partición",
                        )
                else:
                    with self.subTest(par=clave):
                        self.assertIn(par["caso"], inventario)
                        esperado = inventario[par["caso"]]
                        for campo in (
                            "repo",
                            "base",
                            "head",
                            "tarea",
                            "sha_anterior",
                        ):
                            self.assertEqual(
                                par[campo],
                                esperado[campo],
                                f"{clave}: {campo} declarado no coincide con la proyección",
                            )
                        self.assertIn(par["caso"], particion)
                clave_par = (experimento["experimento"], par["caso"])
                por_par.setdefault(clave_par, []).append(par)
        for (experimento, caso), pares in sorted(por_par.items()):
            repeticiones = [par["repeticion"] for par in pares]
            with self.subTest(
                unicidad=f"{experimento}/{caso}", repeticiones=repeticiones
            ):
                self.assertEqual(
                    len(repeticiones),
                    len(set(repeticiones)),
                    "cada (experimento, caso, repetición) aparece una sola vez",
                )
        medidos = {"producto-en-sha-exacto", "push-consecutivo"}
        for (experimento, caso), pares in sorted(por_par.items()):
            if experimento not in medidos:
                continue
            por_repeticion = {par["repeticion"]: par for par in pares}
            with self.subTest(alternado=f"{experimento}/{caso}"):
                self.assertEqual(
                    sorted(por_repeticion),
                    [1, 2, 3],
                    "los pares medidos congelan repeticiones 1-3",
                )
                self.assertEqual(por_repeticion[1]["orden"], por_repeticion[3]["orden"])
                self.assertNotEqual(
                    por_repeticion[1]["orden"], por_repeticion[2]["orden"]
                )
        with self.subTest(particion="cobertura y herencia de grupo"):
            self.assertEqual(set(particion), set(inventario))
            for caso in particion:
                origen = caso.split("-push-")[0]
                self.assertEqual(particion[caso], particion[origen], caso)
        conteos = {e["experimento"]: len(e["pares"]) for e in pairing["experimentos"]}
        self.assertEqual(
            conteos,
            {
                "producto-en-sha-exacto": 15,
                "push-consecutivo": 18,
                "dependencia-control-push-0": 4,
            },
        )
        corpus = {
            c["caso"]: c
            for c in json.loads(
                (ROOT / "evaluation" / "reviewer" / "corpus.json").read_text()
            )["casos"]
        }
        dependencias = next(
            e
            for e in pairing["experimentos"]
            if e["experimento"] == "dependencia-control-push-0"
        )
        for par in dependencias["pares"]:
            with self.subTest(dependencia=par["caso"]):
                caso = par["caso"].split("-dep-")[0]
                pushes = corpus[caso]["pushes"]
                self.assertTrue(pushes, "la dependencia exige un PR con pushes")
                self.assertEqual(par["head"], pushes[0])
                self.assertIsNone(par["sha_anterior"])
                self.assertEqual(par["variante"]["producto"], "ninguno")
                self.assertIn("repeticiones 1-3", par["compartida"])

    def test_push_con_caso_propio_se_evalua(self):
        caso_pr2 = {**caso_v2(), "caso": "pr2"}
        caso_push = {
            **caso_v2(),
            "caso": "pr2-push-1",
            "head": "d" * 40,
            "sha_anterior": "c" * 40,
        }
        config_retanda = (
            "deepseek-v4.1-flash vía deepseek directo (retanda E1aP; "
            "ATTEMPTS=2, presupuesto 1320 s)"
        )
        config_existente = (
            "comentarios existentes en el SHA exacto; sin revisión disparada"
        )
        revisor = obs_v2(
            "revisor", ["pr2-R01"], caso="pr2", configuracion=config_retanda
        )
        coderabbit = obs_v2(
            "coderabbit",
            ["pr2-C01"],
            caso="pr2",
            configuracion=config_existente,
            resultado="comentarios existentes",
            cobertura="no declarada (comentarios existentes)",
            duracion_s=None,
            turnos=None,
            costo_usd=None,
        )
        completa = obs_v2(
            "revisor",
            ["pr2-push-R01"],
            caso="pr2-push-1",
            head="d" * 40,
            sha_anterior="c" * 40,
            configuracion="revision completa (protocolo D0)",
        )
        delta = obs_v2(
            "revisor",
            ["pr2-push-D01"],
            caso="pr2-push-1",
            head="d" * 40,
            sha_anterior="c" * 40,
            configuracion="delta-d0 (protocolo D0)",
        )
        judg = {
            "adjudicaciones": [
                fila_v2(revisor, "pr2-R01", "valid"),
                fila_v2(coderabbit, "pr2-C01", "valid"),
                fila_v2(completa, "pr2-push-R01", "valid"),
                fila_v2(delta, "pr2-push-D01", "false_positive"),
            ]
        }
        pairing = {
            "experimentos": [
                {
                    "experimento": "producto-en-sha-exacto",
                    "pares": [
                        par_v2(
                            {
                                "producto": "revisor",
                                "configuracion": config_retanda,
                                "intento": 1,
                            },
                            {
                                "producto": "coderabbit",
                                "configuracion": config_existente,
                                "intento": 1,
                            },
                            caso="pr2",
                        )
                    ],
                },
                {
                    "experimento": "push-consecutivo",
                    "pares": [
                        par_v2(
                            {
                                "producto": "revisor",
                                "configuracion": "revision completa (protocolo D0)",
                                "intento": 1,
                            },
                            {
                                "producto": "revisor",
                                "configuracion": "delta-d0 (protocolo D0)",
                                "intento": 1,
                            },
                            caso="pr2-push-1",
                            head="d" * 40,
                            sha_anterior="c" * 40,
                        )
                    ],
                },
            ]
        }
        r, informe = correr(
            {"casos": [caso_pr2, caso_push]},
            {"observaciones": [revisor, coderabbit, completa, delta]},
            judg,
            pairing,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(informe["pares"]), 2)
        bloques = {b["experimento"]: b for b in informe["pares"]}
        self.assertEqual(bloques["producto-en-sha-exacto"]["pares_evaluados"], 1)
        self.assertEqual(bloques["push-consecutivo"]["pares_evaluados"], 1)
        for bloque in informe["pares"]:
            for rechazo in bloque["pares_rechazados"]:
                self.assertNotIn("campos que difieren del caso", rechazo["motivo"])


if __name__ == "__main__":
    unittest.main()
