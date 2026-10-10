import argparse
import hashlib
import json
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import review  # noqa: E402
import review_domain as domain  # noqa: E402

PRE, SUF = review.FINDINGS_PREFIX, review.FINDINGS_SUFFIX


def legado(findings, next=1, seen=None):
    data = {"findings": findings, "next": next}
    if seen:
        data["seen"] = seen
    return PRE + json.dumps(data, separators=(",", ":"), ensure_ascii=False) + SUF


def entrada(i, **kw):
    d = {
        "id": i,
        "file": "src/app.py",
        "line": 10,
        "severity": "High",
        "title": f"Hallazgo {i}",
        "state": "open",
    }
    d.update(kw)
    return d


def payload_v2(**over):
    p = {
        "schema": 2,
        "generation": 1,
        "revision": None,
        "next_id": 4,
        "completion": "unknown",
        "command_cursor": 0,
        "pending_requests": [],
        "findings": [
            {
                "id": "F1",
                "title": "Bug",
                "severity": "High",
                "status": {"kind": "open"},
                "primary_anchor": {"kind": "legacy", "path": "a.py", "line": 1},
                "related_anchors": [],
                "cause_hint": None,
                "evidence": [],
            }
        ],
    }
    p.update(over)
    return p


def snapshot_v2():
    return domain.Snapshot(
        schema=2,
        generation=3,
        revision=domain.Revision(
            base_sha="c" * 40, head_sha="d" * 40, policy_digest="e" * 64
        ),
        next_id=4,
        completion=domain.PARTIAL,
        findings=[
            domain.Finding(
                id="F1",
                title="Carrera al escribir",
                severity="High",
                status=domain.StatusOpen(),
                primary_anchor=domain.AnchorLocated(
                    path="src/app.py",
                    blob_sha="a" * 40,
                    range=(10, 20),
                    excerpt_digest="d1",
                    symbol_hint="escribir",
                ),
                related_anchors=[domain.AnchorLegacy(path="src/otro.py", line=5)],
                cause_hint="posible carrera",
                evidence=[
                    domain.EvidenceSource(
                        anchor=domain.AnchorLegacy(path="src/app.py", line=10)
                    ),
                    domain.EvidenceUnverified(text="se reproduce con X"),
                ],
            ),
            domain.Finding(
                id="F2",
                title="Ya arreglado",
                severity="Medium",
                status=domain.StatusResolved(at_sha="b" * 40),
                primary_anchor=domain.AnchorLegacy(path="src/fixed.py", line=3),
                related_anchors=[],
                cause_hint=None,
                evidence=[
                    domain.EvidenceCheck(
                        check_id="ci-1",
                        head_sha="d" * 40,
                        producer="quality",
                        conclusion="pass",
                        url="https://example/run/1",
                    )
                ],
            ),
            domain.Finding(
                id="F3",
                title="Descartado por el operador",
                severity="Low",
                status=domain.StatusDismissed(command_id=98),
                primary_anchor=domain.AnchorLegacy(path="b.py", line=2),
                related_anchors=[],
                cause_hint=None,
                evidence=[],
            ),
        ],
        command_cursor=98,
        pending_requests=[
            domain.PendingRequest(id="req-1", kind="explain", finding_id="F1")
        ],
    )


class LectorCompatible(unittest.TestCase):
    def test_legacy_preserves_ids_dismissals_and_cursor(self):
        load = domain.read_snapshot(
            legado(
                [
                    entrada("F1"),
                    entrada("F2", state="resolved", file="src/fixed.py", line=3),
                    entrada("F3", state="dismissed", file="src/otro.py", line=5),
                ],
                next=7,
                seen=42,
            )
        )
        self.assertIsInstance(load, domain.Legacy)
        s = load.snapshot
        self.assertEqual([f.id for f in s.findings], ["F1", "F2", "F3"])
        self.assertEqual(s.findings[2].status, domain.StatusDismissed(command_id=None))
        self.assertEqual(s.findings[1].status, domain.StatusResolved(at_sha=None))
        self.assertEqual(s.command_cursor, 42)
        self.assertEqual(s.next_id, 7)
        self.assertEqual(
            s.findings[0].primary_anchor,
            domain.AnchorLegacy(path="src/app.py", line=10),
        )
        for f in s.findings:
            self.assertEqual(f.evidence, [], "la migración no inventa evidencia")
            self.assertIsNone(f.cause_hint, "la migración no inventa causa")
            self.assertIsInstance(f.primary_anchor, domain.AnchorLegacy)
        self.assertEqual(s.completion, domain.UNKNOWN)
        self.assertEqual(s.pending_requests, [])
        self.assertEqual([f["id"] for f in load.raw["findings"]], ["F1", "F2", "F3"])

    def test_missing_differs_from_invalid(self):
        vacio = domain.read_snapshot("**Veredicto:** ok")
        self.assertIsInstance(vacio, domain.Missing)
        self.assertNotIsInstance(vacio, domain.Invalid)
        roto = domain.read_snapshot(PRE + "{oops" + SUF)
        self.assertIsInstance(roto, domain.Invalid)
        self.assertNotIsInstance(roto, domain.Missing)
        self.assertTrue(str(roto.reason))
        self.assertIsNone(review.parse_findings_block("**Veredicto:** ok"))
        self.assertIsNone(review.parse_findings_block(PRE + "{oops" + SUF))

    def test_future_version_is_not_legacy(self):
        SCHEMA_FUTURO = 4
        load = domain.read_snapshot(
            PRE
            + json.dumps({"schema": SCHEMA_FUTURO, "findings": [], "next_id": 1})
            + SUF
        )
        self.assertIsInstance(load, domain.Future)
        self.assertEqual(load.version, SCHEMA_FUTURO)
        self.assertNotIsInstance(load, domain.Legacy)
        self.assertNotIsInstance(load, domain.Invalid)


class FronteraDeLectura(unittest.TestCase):
    def lee_v2(self, payload):
        return domain.read_snapshot(PRE + json.dumps(payload) + SUF)

    def test_v2_valido_carga_como_valid(self):
        load = self.lee_v2(payload_v2())
        self.assertIsInstance(load, domain.Valid)
        self.assertEqual(load.snapshot.schema, 2)
        self.assertEqual(load.snapshot.findings[0].id, "F1")

    def test_v2_rechaza_ids_duplicados(self):
        p = payload_v2()
        p["findings"].append(dict(p["findings"][0]))
        load = self.lee_v2(p)
        self.assertIsInstance(load, domain.Invalid)
        self.assertIn("duplic", load.reason)

    def test_v2_rechaza_tipos_invalidos(self):
        for sobre in (
            {"next_id": "4"},
            {"completion": "todo-bien"},
            {
                "findings": [
                    {
                        "id": "F1",
                        "title": "Bug",
                        "severity": "bogus",
                        "status": {"kind": "open"},
                        "primary_anchor": {"kind": "legacy", "path": "a.py", "line": 1},
                        "related_anchors": [],
                        "cause_hint": None,
                        "evidence": [],
                    }
                ]
            },
        ):
            with self.subTest(sobre=sorted(sobre)):
                load = self.lee_v2(payload_v2(**sobre))
                self.assertIsInstance(load, domain.Invalid)

    def test_v2_rechaza_referencias_inconsistentes(self):
        p = payload_v2()
        p["findings"][0]["primary_anchor"] = {
            "kind": "located",
            "path": "a.py",
            "blob_sha": "no-hex",
            "range": [1, 2],
            "excerpt_digest": "d",
        }
        load = self.lee_v2(p)
        self.assertIsInstance(load, domain.Invalid)
        p2 = payload_v2()
        p2["findings"][0]["evidence"] = [{"kind": "chisme", "texto": "x"}]
        load = self.lee_v2(p2)
        self.assertIsInstance(load, domain.Invalid)

    def test_v2_round_trip_sin_perdida(self):
        original = snapshot_v2()
        bloque = domain.encode_snapshot(original)
        self.assertIsInstance(bloque, str)
        load = domain.read_snapshot(bloque)
        self.assertIsInstance(load, domain.Valid)
        self.assertEqual(load.snapshot, original)

    def test_v2_capacidad_excedida(self):
        original = snapshot_v2()
        original.findings[0].title = "x" * 9000
        resultado = domain.encode_snapshot(original)
        self.assertIsInstance(resultado, domain.CapacityExceeded)
        self.assertEqual(resultado.limit, 8000)
        self.assertGreater(resultado.needed, 8000)


class EscrituraCompatible(unittest.TestCase):
    def test_legado_escribe_formato_legado_por_defecto(self):
        load = domain.read_snapshot(
            legado(
                [entrada("F1"), entrada("F3", state="dismissed", file="b.py", line=2)],
                next=7,
                seen=42,
            )
        )
        bloque = domain.encode_snapshot(load.snapshot)
        self.assertIsInstance(bloque, str)
        self.assertNotIn('"schema"', bloque)
        de_vuelta = domain.read_snapshot(bloque)
        self.assertIsInstance(de_vuelta, domain.Legacy)
        self.assertEqual([f.id for f in de_vuelta.snapshot.findings], ["F1", "F3"])
        self.assertEqual(
            de_vuelta.snapshot.findings[1].status,
            domain.StatusDismissed(command_id=None),
        )
        self.assertEqual(de_vuelta.snapshot.command_cursor, 42)
        self.assertEqual(de_vuelta.snapshot.next_id, 7)
        self.assertEqual(bloque, review.serialize_findings(load.raw))

    def test_cierre_mismo_comentario_antes_y_despues_de_la_extraccion(self):
        comentario = (
            "<!-- ai-review:sticky -->\n"
            "<!-- ai-review:sha=abc123 -->\n"
            "## Revisión\n\n**Veredicto:** 1 Medium, 1 Low.\n\n"
            "- 🟡 Medium · `src/app.py:10` · Hallazgo F1\n"
            "- ⚪ Low · `src/other.py:5` · Hallazgo F3 (descartado antes)\n\n"
            + PRE
            + json.dumps(
                {
                    "findings": [
                        entrada("F1"),
                        entrada("F2", state="resolved", file="src/fixed.py", line=3),
                        entrada("F3", state="dismissed", file="src/other.py", line=5),
                    ],
                    "next": 4,
                    "seen": 42,
                },
                separators=(",", ":"),
                ensure_ascii=False,
            )
            + SUF
            + "\n\nCOVERAGE: complete\n"
        )
        estado = review.parse_findings_block(comentario)
        self.assertEqual([f["id"] for f in estado["findings"]], ["F1", "F2", "F3"])
        self.assertEqual(
            [f["state"] for f in estado["findings"]], ["open", "resolved", "dismissed"]
        )
        self.assertEqual(estado["next"], 4)
        self.assertEqual(estado["seen"], 42)
        self.assertEqual(review.split_coverage(comentario)[1], "complete")
        bloque = review.serialize_findings(estado)
        de_vuelta = review.parse_findings_block(bloque)
        self.assertEqual([f["id"] for f in de_vuelta["findings"]], ["F1", "F2", "F3"])
        self.assertEqual(
            [f["state"] for f in de_vuelta["findings"]],
            ["open", "resolved", "dismissed"],
        )
        self.assertEqual(review.serialize_findings(de_vuelta), bloque)
        load = domain.read_snapshot(comentario)
        self.assertIsInstance(load, domain.Legacy)
        self.assertEqual([f.id for f in load.snapshot.findings], ["F1", "F2", "F3"])
        self.assertEqual(load.snapshot.next_id, 4)
        self.assertEqual(load.snapshot.command_cursor, 42)


if __name__ == "__main__":
    unittest.main()


def snapshot_v2_chico():
    return domain.Snapshot(
        schema=2,
        generation=2,
        revision=None,
        next_id=3,
        completion=domain.UNKNOWN,
        findings=[
            domain.Finding(
                id="F1",
                title="Descartado por el operador",
                severity="Low",
                status=domain.StatusDismissed(command_id=7),
                primary_anchor=domain.AnchorLegacy(path="a.py", line=1),
            ),
            domain.Finding(
                id="F2",
                title="Abierto vigente",
                severity="High",
                status=domain.StatusOpen(),
                primary_anchor=domain.AnchorLegacy(path="b.py", line=2),
            ),
        ],
        command_cursor=7,
        pending_requests=[],
    )


class RutaDePublicacion(unittest.TestCase):
    def memoria_v2_en_sticky(self):
        snapshot = snapshot_v2_chico()
        cuerpo = domain.encode_snapshot(snapshot)
        self.assertIsInstance(cuerpo, str)
        return snapshot, cuerpo

    def test_b1_sticky_v2_se_actualiza_sin_perdida_en_publicacion(self):
        """T04: identidad current actualiza la memoria v2 conservando todo."""
        snapshot, sticky_body = self.memoria_v2_en_sticky()
        sticky = {"id": 9, "body": sticky_body}
        result = {"result": legado([entrada("F-new", file="c.py")])}
        manifest = {"mode": "full", "reason": "no-prev", "reviewed": ["c.py"]}
        out = review.build_findings(
            result, manifest, sticky, repo="x/y", pr=1, login="bot", comments=[]
        )
        self.assertFalse(out.get("keep"), "el escritor compatible actualiza")
        self.assertNotEqual(out["block"], sticky_body, "la revisión avanza")
        de_vuelta = domain.read_snapshot(out["block"])
        self.assertIsInstance(de_vuelta, domain.Valid)
        self.assertEqual(de_vuelta.snapshot.schema, 2, "no se degrada a legacy")
        ids = [f.id for f in de_vuelta.snapshot.findings]
        self.assertIn("F1", ids)
        self.assertIn("F2", ids)
        self.assertIn("F3", ids, "lo nuevo del modelo se integra con id asignado")
        self.assertIn("Hallazgo F-new", [f.title for f in de_vuelta.snapshot.findings])
        self.assertEqual(
            next(f.status for f in de_vuelta.snapshot.findings if f.id == "F1"),
            domain.StatusDismissed(command_id=7),
            "un descarte confirmado no reaparece",
        )
        self.assertEqual(de_vuelta.snapshot.command_cursor, 7)

    def test_sticky_de_version_futura_se_conserva(self):
        sticky_body = (
            PRE + json.dumps({"schema": 9, "findings": [], "next_id": 1}) + SUF
        )
        sticky = {"id": 9, "body": sticky_body}
        result = {"result": legado([entrada("F1")])}
        manifest = {"mode": "full", "reason": "no-prev", "reviewed": ["a.py"]}
        out = review.build_findings(
            result, manifest, sticky, repo="x/y", pr=1, login="bot", comments=[]
        )
        self.assertEqual(out["block"], sticky_body)
        self.assertTrue(out.get("keep"))

    def test_b2_null_en_claves_v2_da_invalid_y_no_trona(self):
        for clave in ("pending_requests", "related_anchors", "evidence"):
            with self.subTest(clave=clave):
                p = payload_v2()
                if clave == "pending_requests":
                    p[clave] = None
                else:
                    p["findings"][0][clave] = None
                load = domain.read_snapshot(PRE + json.dumps(p) + SUF)
                self.assertIsInstance(load, domain.Invalid)

    def test_b2_el_texto_del_modelo_no_trona_la_publicacion(self):
        p = payload_v2()
        p["findings"][0]["evidence"] = None
        self.assertIsNone(review.parse_model_findings(PRE + json.dumps(p) + SUF))


class FronteraDeReferencias(unittest.TestCase):
    def lee(self, payload):
        return domain.read_snapshot(PRE + json.dumps(payload) + SUF)

    def hallazgo_f5(self):
        return {
            "id": "F5",
            "title": "Viejo",
            "severity": "Low",
            "status": {"kind": "open"},
            "primary_anchor": {"kind": "legacy", "path": "a.py", "line": 1},
            "related_anchors": [],
            "cause_hint": None,
            "evidence": [],
        }

    def test_b3_next_id_debe_superar_al_maximo_existente(self):
        load = self.lee(payload_v2(next_id=1, findings=[self.hallazgo_f5()]))
        self.assertIsInstance(load, domain.Invalid)
        self.assertIn("next_id", load.reason)

    def test_b3_solicitud_de_hallazgo_inexistente(self):
        load = self.lee(
            payload_v2(
                pending_requests=[
                    {"id": "req-1", "kind": "explain", "finding_id": "F9"}
                ]
            )
        )
        self.assertIsInstance(load, domain.Invalid)
        self.assertIn("F9", load.reason)

    def test_b3_command_id_no_puede_superar_el_cursor(self):
        p = payload_v2(
            command_cursor=0,
            findings=[
                {
                    "id": "F1",
                    "title": "x",
                    "severity": "Low",
                    "status": {"kind": "dismissed", "command_id": 99},
                    "primary_anchor": {"kind": "legacy", "path": "a.py", "line": 1},
                    "related_anchors": [],
                    "cause_hint": None,
                    "evidence": [],
                }
            ],
        )
        load = self.lee(p)
        self.assertIsInstance(load, domain.Invalid)
        self.assertIn("command_cursor", load.reason)

    def test_b3_cause_hint_debe_ser_texto_o_nulo(self):
        p = payload_v2()
        p["findings"][0]["cause_hint"] = 123
        load = self.lee(p)
        self.assertIsInstance(load, domain.Invalid)


class PublicacionConMemoriaV2(unittest.TestCase):
    def test_b4_publicar_con_memoria_v2_actualiza_el_comentario(self):
        """T04: identidad current publica la actualización de memoria v2."""
        snapshot = domain.Snapshot(
            schema=2,
            generation=1,
            revision=None,
            next_id=3,
            completion=domain.UNKNOWN,
            findings=[
                domain.Finding(
                    id="F1",
                    title="bug x",
                    severity="Low",
                    status=domain.StatusDismissed(command_id=7),
                    primary_anchor=domain.AnchorLegacy(path="a.py", line=1),
                ),
                domain.Finding(
                    id="F2",
                    title="Abierto",
                    severity="High",
                    status=domain.StatusOpen(),
                    primary_anchor=domain.AnchorLegacy(path="b.py", line=2),
                ),
            ],
            command_cursor=7,
            pending_requests=[],
        )
        sticky_body = (
            review.MARKER
            + "\n"
            + f"{review.SHA_PREFIX}{'a' * 40} -->\n"
            + f"{review.COMPLETION_PREFIX}{'a' * 40}:complete -->\n"
            + domain.encode_snapshot(snapshot)
            + "\n\n### Revisión anterior\n\nTexto previo que debe permanecer.\n"
        )
        modelo = "## Detalle\n\n- bug x en a.py\n- bug nuevo z en c.py\n\n" + legado(
            [
                entrada("F-new", file="a.py", line=1, title="bug x"),
                entrada("F2-new", file="c.py", line=9, title="bug nuevo z"),
            ],
            next=3,
        )
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "result.json").write_text(json.dumps({"result": modelo}))
            (work / "manifest.json").write_text(
                json.dumps(
                    {
                        "mode": "full",
                        "reason": "no-prev",
                        "reviewed": ["a.py", "c.py"],
                        "excluded": [],
                    }
                )
            )
            sticky = {"id": 9, "user": "github-actions[bot]", "body": sticky_body}
            envs = {
                "REPO": "x/y",
                "PR_NUMBER": "1",
                "HEAD_SHA": "c" * 40,
            }
            os.environ.pop("GITHUB_STEP_SUMMARY", None)
            with mock.patch.dict(os.environ, envs):
                with (
                    mock.patch.object(
                        review, "fetch_all_comments", return_value=[sticky]
                    ),
                    mock.patch.object(review, "sh"),
                ):
                    review.cmd_publish(argparse.Namespace(work=str(work)))
            body = json.loads((work / "comment.json").read_text())["body"]
        self.assertIn(f"{review.SHA_PREFIX}{'c' * 40} -->", body, "la revisión avanza")
        self.assertIn(
            f"{review.COMPLETION_PREFIX}{'c' * 40}:partial -->",
            body,
            "sin cobertura declarada no se confirma complete",
        )
        self.assertIn('"schema":2', body, "la memoria v2 se conserva en schema 2")
        self.assertIn("bug nuevo z", body, "lo nuevo del modelo se integra")
        self.assertIn('"command_id":7', body, "el descarte F1 conserva su command_id")
        self.assertIn("bug x", body, "el descartado sigue listado como descartado")


class PresupuestosEnBytes(unittest.TestCase):
    def snapshot_con_titulo(self, titulo):
        return domain.Snapshot(
            schema=2,
            generation=1,
            revision=None,
            next_id=2,
            completion=domain.UNKNOWN,
            findings=[
                domain.Finding(
                    id="F1",
                    title=titulo,
                    severity="Low",
                    status=domain.StatusOpen(),
                    primary_anchor=domain.AnchorLegacy(path="a.py", line=1),
                )
            ],
            command_cursor=0,
        )

    def test_v2_en_el_limite_exacto_y_un_byte_encima(self):
        base = domain.encode_snapshot(self.snapshot_con_titulo("x"))
        self.assertIsInstance(base, str)
        fijo = len(base.encode("utf-8")) - 1
        titulo = "x" * (domain.FINDINGS_MAX_BYTES - fijo)
        bloque = domain.encode_snapshot(self.snapshot_con_titulo(titulo))
        self.assertIsInstance(bloque, str)
        self.assertEqual(
            len(bloque.encode("utf-8")), domain.FINDINGS_MAX_BYTES, "justo en el límite"
        )
        de_mas = domain.encode_snapshot(self.snapshot_con_titulo(titulo + "x"))
        self.assertIsInstance(de_mas, domain.CapacityExceeded, "un byte por encima")
        self.assertEqual(de_mas.needed, domain.FINDINGS_MAX_BYTES + 1)

    def test_v2_unicode_cuenta_bytes_y_no_caracteres(self):
        # 4200 'ñ' son 4200 caracteres pero 8400 bytes: el tope es en bytes.
        resultado = domain.encode_snapshot(self.snapshot_con_titulo("ñ" * 4200))
        self.assertIsInstance(resultado, domain.CapacityExceeded)
        self.assertGreater(resultado.needed, domain.FINDINGS_MAX_BYTES)

    def test_legado_overflow_da_capacity_exceeded_sin_recortar(self):
        # 40 abiertos con títulos ñ×130: la memoria íntegra no cabe y NO se
        # recorta (un título recortado rompe same_issue y revive descartes).
        findings = [
            {
                "id": f"F{i}",
                "file": "a.py",
                "line": 1,
                "severity": "Low",
                "title": "ñ" * 130,
                "state": "open",
            }
            for i in range(1, 41)
        ]
        resultado = review.serialize_findings({"findings": findings, "next": 41})
        self.assertIsInstance(resultado, domain.CapacityExceeded)
        self.assertGreater(resultado.needed, domain.FINDINGS_MAX_BYTES)


class FronteraDeEscritura(unittest.TestCase):
    def test_encode_v2_rechaza_revision_parcial(self):
        s = snapshot_v2()
        s.revision = domain.Revision(base_sha="c" * 40)
        with self.assertRaises(ValueError) as ctx:
            domain.encode_snapshot(s)
        self.assertIn("revisión", str(ctx.exception))

    def test_encode_v2_rechaza_id_nulo(self):
        s = snapshot_v2()
        s.findings[0].id = None
        with self.assertRaises(ValueError) as ctx:
            domain.encode_snapshot(s)
        self.assertIn("id", str(ctx.exception))

    def test_encode_v2_neutraliza_el_cierre_del_comentario(self):
        s = snapshot_v2()
        s.findings[0].title = "a --> b --!> c"
        bloque = domain.encode_snapshot(s)
        self.assertIsInstance(bloque, str)
        self.assertEqual(bloque.count("-->"), 1, "sólo el sufijo del bloque")
        self.assertIn("a --› b --!› c", bloque)
        load = domain.read_snapshot(bloque)
        self.assertIsInstance(load, domain.Valid)
        self.assertEqual(load.snapshot.findings[0].title, "a --› b --!› c")

    def test_leer_v2_con_la_revision_exacta_de_m0_no_convierte_a_legacy(self):
        s = snapshot_v2()
        bloque = domain.encode_snapshot(s)
        load = domain.read_snapshot(bloque)
        self.assertIsInstance(load, domain.Valid)
        self.assertNotIsInstance(load, domain.Legacy)
        self.assertEqual(load.snapshot.revision, s.revision)
        self.assertIsNone(
            review.parse_findings_block(bloque),
            "el adaptador de M0 no convierte el estado nuevo a legacy",
        )


class MedicionDelEstadoEnriquecido(unittest.TestCase):
    def enriquecido(self, n):
        findings = [
            domain.Finding(
                id=f"F{i}",
                title=f"Problema {i} en la ruta de pago",
                severity="Medium",
                status=domain.StatusOpen(),
                primary_anchor=domain.AnchorLocated(
                    path=f"src/mod{i % 5}.py",
                    blob_sha="a" * 40,
                    range=(10 * i, 10 * i + 5),
                    excerpt_digest=f"d{i}",
                    symbol_hint="pagar",
                ),
                related_anchors=[
                    domain.AnchorLegacy(path="tests/test_pago.py", line=i)
                ],
                cause_hint="posible condición de carrera en el cobro",
                evidence=[
                    domain.EvidenceSource(
                        anchor=domain.AnchorLegacy(path="src/mod.py", line=10 * i)
                    ),
                    domain.EvidenceUnverified(
                        text="falla intermitente en CI con carga"
                    ),
                ],
            )
            for i in range(1, n + 1)
        ]
        return domain.Snapshot(
            schema=2,
            generation=5,
            revision=domain.Revision(
                base_sha="b" * 40, head_sha="c" * 40, policy_digest="d" * 64
            ),
            next_id=n + 1,
            completion=domain.PARTIAL,
            findings=findings,
            command_cursor=50,
        )

    def test_medicion_representativa_documenta_la_decision(self):
        tamanios = {}
        for n in (1, 3, 10, 60):
            r = domain.encode_snapshot(self.enriquecido(n))
            tamanios[n] = (
                r.needed
                if isinstance(r, domain.CapacityExceeded)
                else len(r.encode("utf-8"))
            )
        # lo que trae una revisión típica chica sí cabe en el bloque v2...
        self.assertLessEqual(tamanios[3], domain.FINDINGS_MAX_BYTES)
        # ...pero el tope de 60 hallazgos enriquecidos NO cabe: por eso la
        # escritura v2 sigue desactivada por defecto y la decisión de
        # almacenamiento queda documentada como pendiente en M1.md.
        self.assertIsInstance(
            domain.encode_snapshot(self.enriquecido(60)), domain.CapacityExceeded
        )
        print(f"M1 medición (bytes UTF-8 de estado enriquecido): {tamanios}")


# ---- F0: identidad, evidencia y resolución ----


def _ancla_locada(digest, path="src/app.py", blob="a" * 40, rango=(2, 2)):
    return domain.AnchorLocated(
        path=path,
        blob_sha=blob,
        range=rango,
        excerpt_digest=digest,
        symbol_hint=None,
    )


def _observacion(titulo, ruta="src/app.py", **kw):
    base = dict(
        title=titulo,
        severity="High",
        primary_anchor=domain.AnchorLegacy(path=ruta, line=1),
        related_anchors=[],
        cause_hint=None,
        evidence=[],
        claim=domain.OPEN,
    )
    base.update(kw)
    return domain.Observation(**base)


class IdentidadDeHallazgos(unittest.TestCase):
    def hechos(self, renames=()):
        return domain.RepositoryFacts(renames=tuple(renames))

    def previos(self):
        return [
            domain.Finding(
                id="F1",
                title="Bug del IVA",
                severity="High",
                status=domain.StatusOpen(),
                primary_anchor=domain.AnchorLegacy(path="src/app.py", line=10),
            ),
            domain.Finding(
                id="F2",
                title="Bug del IVA",
                severity="Medium",
                status=domain.StatusOpen(),
                primary_anchor=domain.AnchorLegacy(path="src/otro.py", line=20),
            ),
            domain.Finding(
                id="F3",
                title="Fuga de recurso",
                severity="Low",
                status=domain.StatusDismissed(command_id=None),
                primary_anchor=domain.AnchorLegacy(path="src/app.py", line=30),
            ),
        ]

    def test_cambio_de_titulo_conserva_id(self):
        obs = _observacion("El IVA se valida tarde y mal", ruta="src/app.py")
        self.assertEqual(
            domain.match_finding(self.previos(), obs, self.hechos()),
            domain.MatchExisting(id="F1"),
        )

    def test_desplazamiento_de_lineas_conserva_id(self):
        obs = _observacion(
            "Bug del IVA",
            ruta="src/app.py",
            primary_anchor=domain.AnchorLegacy(path="src/app.py", line=500),
        )
        self.assertEqual(
            domain.match_finding(self.previos(), obs, self.hechos()),
            domain.MatchExisting(id="F1"),
        )

    def test_renombre_confirmado_conserva_id(self):
        obs = _observacion("Bug del IVA", ruta="src/app_renombrado.py")
        self.assertEqual(
            domain.match_finding(
                self.previos(),
                obs,
                self.hechos(renames=(("src/app_renombrado.py", "src/app.py"),)),
            ),
            domain.MatchExisting(id="F1"),
        )

    def test_dos_bugs_mismo_titulo_en_archivos_distintos_no_se_fusionan(self):
        previos = [self.previos()[0], self.previos()[1]]
        obs_a = _observacion("Bug del IVA", ruta="src/app.py")
        obs_b = _observacion("Bug del IVA", ruta="src/otro.py")
        self.assertEqual(
            domain.match_finding(previos, obs_a, self.hechos()),
            domain.MatchExisting(id="F1"),
        )
        self.assertEqual(
            domain.match_finding(previos, obs_b, self.hechos()),
            domain.MatchExisting(id="F2"),
        )

    def test_dos_candidatos_plausibles_dan_ambiguo_sin_fusionar(self):
        # un archivo dividido en dos: la ruta nueva corresponde a dos viejas
        obs = _observacion("Bug del IVA", ruta="src/iva.py")
        hechos = self.hechos(
            renames=(("src/iva.py", "src/app.py"), ("src/iva.py", "src/otro.py"))
        )
        self.assertEqual(
            domain.match_finding(self.previos(), obs, hechos),
            domain.MatchAmbiguous(ids=("F1", "F2")),
        )

    def test_los_descartes_se_conservan_y_no_reaparecen(self):
        previos = self.previos()
        obs_f3 = _observacion("Fuga de recurso", ruta="src/app.py")
        self.assertEqual(
            domain.match_finding(previos, obs_f3, self.hechos()),
            domain.MatchExisting(id="F3"),
        )
        plan = domain.ReviewPlan(revision=None, changed_paths=())
        report = domain.validar_reporte([obs_f3], domain.UNKNOWN, self.hechos())
        transicion = domain.accept_report(self._snapshot_v2(previos), plan, report)
        self.assertIsInstance(transicion, domain.Replace)
        f3 = {f.id: f for f in transicion.snapshot.findings}["F3"]
        self.assertEqual(f3.status, domain.StatusDismissed(command_id=None))

    def _snapshot_v2(self, findings):
        return domain.Snapshot(
            schema=2,
            generation=1,
            revision=domain.Revision(
                base_sha="b" * 40, head_sha="c" * 40, policy_digest="d" * 64
            ),
            next_id=4,
            completion=domain.UNKNOWN,
            findings=findings,
            command_cursor=0,
        )


class CitasYEvidencia(unittest.TestCase):
    BLOB = ("def pagar():", "    uso del IVA sin validar", "    return total")

    def hechos(self):
        return domain.RepositoryFacts(blobs={("src/app.py", "a" * 40): self.BLOB})

    def observacion_con_cita(self, digest, claim=domain.OPEN):
        return _observacion(
            "Uso del IVA sin validar",
            primary_anchor=_ancla_locada(digest),
            evidence=[
                domain.EvidenceSource(
                    anchor=domain.AnchorLocated(
                        path="src/app.py",
                        blob_sha="a" * 40,
                        range=(2, 2),
                        excerpt_digest=digest,
                    )
                ),
                domain.EvidenceUnverified(text="se reproduce al pagar sin IVA"),
            ],
            claim=claim,
        )

    def test_cita_valida_queda_etiquetada_como_ubicacion_verificada(self):

        digest = hashlib.sha256(self.BLOB[1].rstrip("\n").encode("utf-8")).hexdigest()
        report = domain.validar_reporte(
            [self.observacion_con_cita(digest)], domain.UNKNOWN, self.hechos()
        )
        self.assertEqual(report.rechazadas, ())
        fuente = report.observations[0].evidence[0]
        self.assertIsInstance(fuente, domain.EvidenceSource)

    def test_cita_con_digest_invalido_se_rechaza(self):
        report = domain.validar_reporte(
            [self.observacion_con_cita("d" * 64)], domain.UNKNOWN, self.hechos()
        )
        self.assertTrue(report.rechazadas)
        tipos = [type(e) for e in report.observations[0].evidence]
        self.assertNotIn(
            domain.EvidenceSource, tipos, "la cita rechazada no queda como evidencia"
        )
        self.assertIn(
            domain.EvidenceUnverified,
            tipos,
            "lo no comprobable queda etiquetado aparte",
        )

    def test_afirmacion_sin_evidencia_comprobable_queda_etiquetada(self):

        digest = hashlib.sha256(self.BLOB[1].rstrip("\n").encode("utf-8")).hexdigest()
        report = domain.validar_reporte(
            [self.observacion_con_cita(digest)], domain.UNKNOWN, self.hechos()
        )
        tipos = [type(e) for e in report.observations[0].evidence]
        self.assertIn(domain.EvidenceUnverified, tipos)

    def test_cita_existente_con_interpretacion_falsa_no_marca_reproducido(self):

        digest = hashlib.sha256(self.BLOB[1].rstrip("\n").encode("utf-8")).hexdigest()
        previos = [
            domain.Finding(
                id="F1",
                title="Uso del IVA sin validar",
                severity="High",
                status=domain.StatusOpen(),
                primary_anchor=domain.AnchorLegacy(path="src/app.py", line=2),
            )
        ]
        obs = self.observacion_con_cita(digest, claim=domain.RESOLVED)
        plan = domain.ReviewPlan(
            revision=domain.Revision(
                base_sha="b" * 40, head_sha="c" * 40, policy_digest="d" * 64
            ),
            changed_paths=(),  # nada cambió: la cita existe pero no hay arreglo
        )
        report = domain.validar_reporte([obs], domain.PARTIAL, self.hechos())
        transicion = domain.accept_report(
            domain.Snapshot(
                schema=2,
                generation=1,
                revision=plan.revision,
                next_id=2,
                completion=domain.UNKNOWN,
                findings=previos,
                command_cursor=0,
            ),
            plan,
            report,
        )
        self.assertIsInstance(transicion, domain.Replace)
        # r5: contra P_leg, exactamente un candidato con el mismo título
        # (incluidos los descartados) y ningún otro plausible -> Existing.
        f1 = transicion.snapshot.findings[0]
        self.assertEqual(f1.id, "F1")
        self.assertIsInstance(
            f1.status, domain.StatusOpen, "la cita validada no marca reproducido"
        )
        self.assertTrue(
            any(isinstance(e, domain.EvidenceSource) for e in f1.evidence),
            "la ubicación verificada queda como evidencia",
        )
        self.assertTrue(
            any(isinstance(e, domain.EvidenceUnverified) for e in f1.evidence),
            "la evaluación del modelo queda etiquetada aparte",
        )


class ResolucionConCambioPertinente(unittest.TestCase):
    def _previo(self, estado=domain.StatusOpen()):
        return domain.Finding(
            id="F1",
            title="bug x",
            severity="Low",
            status=estado,
            primary_anchor=domain.AnchorLegacy(path="src/x.py", line=1),
        )

    def _aceptar(self, previo, claim, changed, reverted=(), prev_estado=None):
        obs = _observacion(
            "bug x",
            ruta="src/x.py",
            primary_anchor=domain.AnchorLegacy(path="src/x.py", line=1),
            claim=claim,
        )
        hechos = domain.RepositoryFacts(reverted_paths=tuple(reverted))
        plan = domain.ReviewPlan(
            revision=domain.Revision(
                base_sha="b" * 40, head_sha="c" * 40, policy_digest="d" * 64
            ),
            changed_paths=tuple(changed),
        )
        report = domain.validar_reporte([obs], domain.UNKNOWN, hechos)
        snapshot = domain.Snapshot(
            schema=2,
            generation=1,
            revision=plan.revision,
            next_id=2,
            completion=domain.UNKNOWN,
            findings=[previo],
            command_cursor=0,
        )
        return domain.accept_report(snapshot, plan, report)

    def test_resolver_exige_cambio_pertinente(self):
        t = self._aceptar(self._previo(), domain.RESOLVED, changed=())
        self.assertIsInstance(t.snapshot.findings[0].status, domain.StatusOpen)

    def test_resolucion_con_cambio_pertinente(self):
        t = self._aceptar(self._previo(), domain.RESOLVED, changed=("src/x.py",))
        self.assertIsInstance(t.snapshot.findings[0].status, domain.StatusResolved)

    def test_resolucion_por_reversion_exacta(self):
        t = self._aceptar(
            self._previo(), domain.OPEN, changed=(), reverted=("src/x.py",)
        )
        self.assertIsInstance(t.snapshot.findings[0].status, domain.StatusResolved)

    def test_resolucion_previa_por_reversion_se_conserva(self):
        t = self._aceptar(
            self._previo(domain.StatusResolved(at_sha="c" * 40)),
            domain.OPEN,
            changed=(),
        )
        self.assertIsInstance(t.snapshot.findings[0].status, domain.StatusResolved)

    def test_arreglo_en_archivo_relacionado(self):
        previo = self._previo()
        previo.related_anchors = [domain.AnchorLegacy(path="tests/test_x.py", line=9)]
        t = self._aceptar(previo, domain.RESOLVED, changed=("tests/test_x.py",))
        self.assertIsInstance(t.snapshot.findings[0].status, domain.StatusResolved)

    def test_accept_en_estado_legado_da_keep(self):
        obs = _observacion("bug x", ruta="src/x.py")
        report = domain.validar_reporte([obs], domain.UNKNOWN, domain.RepositoryFacts())
        legado = domain.Snapshot(
            schema=1,
            generation=0,
            revision=None,
            next_id=1,
            completion=domain.UNKNOWN,
            findings=[],
            command_cursor=0,
        )
        plan = domain.ReviewPlan(revision=None, changed_paths=())
        self.assertIsInstance(domain.accept_report(legado, plan, report), domain.Keep)


class NuevosIdsYAmbiguosEnAccept(unittest.TestCase):
    def test_ambiguo_crea_hallazgo_separado_sin_fusionar(self):
        previos = [
            domain.Finding(
                id="F1",
                title="Bug del IVA",
                severity="High",
                status=domain.StatusOpen(),
                primary_anchor=domain.AnchorLegacy(path="src/app.py", line=10),
            ),
            domain.Finding(
                id="F2",
                title="Bug del IVA",
                severity="Medium",
                status=domain.StatusOpen(),
                primary_anchor=domain.AnchorLegacy(path="src/otro.py", line=20),
            ),
        ]
        # renames son pares (ruta_nueva, ruta_vieja): un archivo dividido
        # mapea una ruta nueva a dos viejas y eso es lo que hace ambiguo el match.
        hechos = domain.RepositoryFacts(
            renames=(("src/iva.py", "src/app.py"), ("src/iva.py", "src/otro.py"))
        )
        obs = _observacion("Bug del IVA", ruta="src/iva.py")
        self.assertEqual(
            domain.match_finding(previos, obs, hechos),
            domain.MatchAmbiguous(ids=("F1", "F2")),
        )
        plan = domain.ReviewPlan(
            revision=domain.Revision(
                base_sha="b" * 40, head_sha="c" * 40, policy_digest="d" * 64
            ),
            changed_paths=("src/iva.py",),
        )
        report = domain.validar_reporte([obs], domain.UNKNOWN, hechos)
        snapshot = domain.Snapshot(
            schema=2,
            generation=1,
            revision=plan.revision,
            next_id=3,
            completion=domain.UNKNOWN,
            findings=previos,
            command_cursor=0,
        )
        transicion = domain.accept_report(snapshot, plan, report)
        self.assertIsInstance(transicion, domain.Replace)
        por_id = {f.id: f for f in transicion.snapshot.findings}
        self.assertIn("F3", por_id, "el ambiguo entra separado con id del programa")
        self.assertIn("posible duplicado", por_id["F3"].cause_hint)
        self.assertEqual(por_id["F1"].title, "Bug del IVA")
        self.assertEqual(por_id["F2"].title, "Bug del IVA")


# ---- F0 r2: identidad por anclas, regresiones y anclas validadas ----


def _ancla_locada_en(rango, digest, path="src/app.py", blob="a" * 40):
    return domain.AnchorLocated(
        path=path, blob_sha=blob, range=rango, excerpt_digest=digest
    )


def _hallazgo_locado(
    fid, titulo, rango, digest, estado=domain.StatusOpen(), ruta="src/app.py"
):
    return domain.Finding(
        id=fid,
        title=titulo,
        severity="Medium",
        status=estado,
        primary_anchor=domain.AnchorLocated(
            path=ruta, blob_sha="a" * 40, range=rango, excerpt_digest=digest
        ),
    )


class AnclasComoIdentidad(unittest.TestCase):
    LINEAS = tuple(f"línea {i}" for i in range(1, 31))
    LINEAS_B = LINEAS  # la edición crea un blob nuevo; el extracto sigue verbatim
    BLOB = {("src/app.py", "a" * 40): tuple(f"línea {i}" for i in range(1, 31))}

    def hechos(self):
        return domain.RepositoryFacts(
            blobs={
                **self.BLOB,
                ("src/app.py", "b" * 40): self.LINEAS_B,
            }
        )

    def digest(self, rango):

        extracto = "\n".join(self.LINEAS[rango[0] - 1 : rango[1]])
        return hashlib.sha256(extracto.encode("utf-8")).hexdigest()

    def test_b1_descarte_reconocido_por_digest_aun_con_titulo_reformulado(self):
        # la edición crea un blob nuevo, pero el extracto sigue verbatim en la
        # misma ruta: MISMO digest en la MISMA ruta, sin importar el blob.
        previos = [
            _hallazgo_locado(
                "F1",
                "la entrada no se valida",
                (2, 2),
                self.digest((2, 2)),
                estado=domain.StatusDismissed(command_id=None),
            ),
        ]
        obs = _observacion(
            "la entrada del formulario no se valida (reformulado)",
            primary_anchor=_ancla_locada_en((2, 2), self.digest((2, 2)), blob="b" * 40),
        )
        self.assertEqual(
            domain.match_finding(previos, obs, self.hechos()),
            domain.MatchExisting(id="F1"),
        )

    def test_b1_descarte_con_ancla_compatible_no_genera_nuevo_en_accept(self):
        # solape SIN digest igual: sólo candidato plausible -> Ambiguous con
        # marca de posible duplicado, SIN fusionar; el descartado conserva su
        # estado y no reaparece como abierto.
        previos = [
            _hallazgo_locado(
                "F1",
                "la entrada no se valida",
                (2, 2),
                self.digest((2, 2)),
                estado=domain.StatusDismissed(command_id=None),
            ),
        ]
        obs = _observacion(
            "la entrada del formulario no se valida (reformulado)",
            primary_anchor=_ancla_locada_en((2, 3), self.digest((2, 3))),
        )
        self.assertEqual(
            domain.match_finding(previos, obs, self.hechos()),
            domain.MatchAmbiguous(ids=("F1",)),
        )
        plan = domain.ReviewPlan(
            revision=domain.Revision(
                base_sha="b" * 40, head_sha="c" * 40, policy_digest="d" * 64
            ),
            changed_paths=("src/app.py",),
        )
        report = domain.validar_reporte([obs], domain.UNKNOWN, self.hechos())
        transicion = domain.accept_report(
            domain.Snapshot(
                schema=2,
                generation=1,
                revision=plan.revision,
                next_id=2,
                completion=domain.UNKNOWN,
                findings=previos,
                command_cursor=0,
            ),
            plan,
            report,
        )
        self.assertIsInstance(transicion, domain.Replace)
        por_id = {f.id: f for f in transicion.snapshot.findings}
        self.assertEqual(set(por_id), {"F1", "F2"})
        self.assertIsInstance(por_id["F1"].status, domain.StatusDismissed)
        self.assertIn("posible duplicado de F1", por_id["F2"].cause_hint)

    def test_b3_ancla_primaria_fuera_del_blob_se_rechaza_y_baja(self):
        obs = _observacion(
            "bug x",
            primary_anchor=domain.AnchorLocated(
                path="src/app.py",
                blob_sha="a" * 40,
                range=(900, 905),
                excerpt_digest="f" * 64,
            ),
        )
        report = domain.validar_reporte([obs], domain.UNKNOWN, self.hechos())
        self.assertTrue(report.rechazadas)
        self.assertIsInstance(
            report.observations[0].primary_anchor,
            domain.AnchorLegacy,
            "el ancla que no verifica no sirve como identidad",
        )


class RegresionDeResuelto(unittest.TestCase):
    def test_b2_regresion_con_cambio_pertinente_reabre(self):
        previo = domain.Finding(
            id="F1",
            title="bug x",
            severity="Low",
            status=domain.StatusResolved(at_sha="c" * 40),
            primary_anchor=domain.AnchorLegacy(path="src/app.py", line=1),
        )
        obs = _observacion("bug x otra vez", ruta="src/app.py", claim=domain.OPEN)
        plan = domain.ReviewPlan(
            revision=domain.Revision(
                base_sha="b" * 40, head_sha="c" * 40, policy_digest="d" * 64
            ),
            changed_paths=("src/app.py",),
        )
        report = domain.validar_reporte([obs], domain.UNKNOWN, domain.RepositoryFacts())
        transicion = domain.accept_report(
            domain.Snapshot(
                schema=2,
                generation=1,
                revision=plan.revision,
                next_id=2,
                completion=domain.UNKNOWN,
                findings=[previo],
                command_cursor=0,
            ),
            plan,
            report,
        )
        self.assertIsInstance(transicion, domain.Replace)
        self.assertIsInstance(
            transicion.snapshot.findings[0].status,
            domain.StatusOpen,
            "un bug reintroducido con cambio pertinente se reabre",
        )


# ---- F0 r3: identidad fuerte por digest; solape sólo sugiere ----


class IdentidadFuertePorDigest(unittest.TestCase):
    LINEAS_A = tuple(f"línea {i}" for i in range(1, 31))
    LINEAS_B = ("encabezado nuevo",) + LINEAS_A  # el mismo contenido corrido 1 línea
    BLOBS = {
        ("src/consulta.sql", "a" * 40): LINEAS_A,
        ("src/consulta.sql", "b" * 40): LINEAS_B,
    }

    def hechos(self):
        return domain.RepositoryFacts(blobs=self.BLOBS)

    def digest(self, lineas, rango):

        extracto = "\n".join(lineas[rango[0] - 1 : rango[1]])
        return hashlib.sha256(extracto.encode("utf-8")).hexdigest()

    def hallazgo(self, fid, titulo, estado, blob, rango, digest):
        return domain.Finding(
            id=fid,
            title=titulo,
            severity="Medium",
            status=estado,
            primary_anchor=domain.AnchorLocated(
                path="src/consulta.sql",
                blob_sha=blob,
                range=rango,
                excerpt_digest=digest,
            ),
        )

    def observacion(self, titulo, blob, rango, digest):
        return domain.Observation(
            title=titulo,
            severity="Medium",
            primary_anchor=domain.AnchorLocated(
                path="src/consulta.sql",
                blob_sha=blob,
                range=rango,
                excerpt_digest=digest,
            ),
        )

    def test_a_solape_con_descartada_es_ambiguo(self):
        previos = [
            self.hallazgo(
                "F1",
                "SQL sin límite en el cobro",
                domain.StatusDismissed(command_id=None),
                "a" * 40,
                (5, 8),
                self.digest(self.LINEAS_A, (5, 8)),
            )
        ]
        # contenido NUEVO (el SQL reescrito) en líneas que se solapan con las
        # de la descartada, con digest propio distinto: plausible, no identidad.
        obs = self.observacion(
            "SQL nueva sobre el cobro",
            "b" * 40,
            (5, 8),
            self.digest(self.LINEAS_B, (5, 8)),
        )
        assert (
            obs.primary_anchor.excerpt_digest
            != previos[0].primary_anchor.excerpt_digest
        )
        self.assertEqual(
            domain.match_finding(previos, obs, self.hechos()),
            domain.MatchAmbiguous(ids=("F1",)),
        )

    def test_b_solape_con_division_por_cero_es_ambiguo(self):
        previos = [
            self.hallazgo(
                "F1",
                "División por cero al exportar",
                domain.StatusOpen(),
                "a" * 40,
                (10, 12),
                self.digest(self.LINEAS_A, (10, 12)),
            )
        ]
        obs = self.observacion(
            "División por cero en el reporte nuevo",
            "a" * 40,
            (11, 13),
            self.digest(self.LINEAS_A, (11, 13)),
        )
        self.assertEqual(
            domain.match_finding(previos, obs, self.hechos()),
            domain.MatchAmbiguous(ids=("F1",)),
        )

    def test_c_dos_solapados_plausibles_es_ambiguo(self):
        previos = [
            self.hallazgo(
                "F1",
                "bug uno",
                domain.StatusOpen(),
                "a" * 40,
                (4, 6),
                self.digest(self.LINEAS_A, (4, 6)),
            ),
            self.hallazgo(
                "F2",
                "bug dos",
                domain.StatusOpen(),
                "a" * 40,
                (5, 9),
                self.digest(self.LINEAS_A, (5, 9)),
            ),
        ]
        obs = self.observacion(
            "bug de los rangos",
            "a" * 40,
            (4, 9),
            self.digest(self.LINEAS_A, (4, 9)),
        )
        self.assertEqual(
            domain.match_finding(previos, obs, self.hechos()),
            domain.MatchAmbiguous(ids=("F1", "F2")),
        )

    def test_d_mismo_digest_con_blob_nuevo_reconoce_el_descartado(self):
        previos = [
            self.hallazgo(
                "F1",
                "SQL sin límite en el cobro",
                domain.StatusDismissed(command_id=None),
                "a" * 40,
                (2, 2),
                self.digest(self.LINEAS_A, (2, 2)),
            )
        ]
        # mismo extracto corrido una línea en el blob nuevo (mismo digest),
        # con el título reformulado: se reconoce el descartado.
        obs = self.observacion(
            "SQL sin límite, reformulado tras la edición",
            "b" * 40,
            (3, 3),
            self.digest(self.LINEAS_B, (3, 3)),
        )
        match = domain.match_finding(previos, obs, self.hechos())
        self.assertEqual(match, domain.MatchExisting(id="F1"))
        plan = domain.ReviewPlan(
            revision=domain.Revision(
                base_sha="b" * 40, head_sha="c" * 40, policy_digest="d" * 64
            ),
            changed_paths=("src/consulta.sql",),
        )
        report = domain.validar_reporte([obs], domain.UNKNOWN, self.hechos())
        transicion = domain.accept_report(
            domain.Snapshot(
                schema=2,
                generation=1,
                revision=plan.revision,
                next_id=2,
                completion=domain.UNKNOWN,
                findings=previos,
                command_cursor=0,
            ),
            plan,
            report,
        )
        self.assertIsInstance(transicion, domain.Replace)
        self.assertEqual([f.id for f in transicion.snapshot.findings], ["F1"])
        self.assertIsInstance(
            transicion.snapshot.findings[0].status,
            domain.StatusDismissed,
            "el descartado se conserva; no se crea F2 abierto",
        )


# ---- F0 r4: ancla verificada sin candidato es nuevo; extracto genérico ----


class AnclaVerificadaYExtractoGenerico(unittest.TestCase):
    LINEAS = tuple(
        "bug x" if i == 5 else ("return None" if i in (20, 40) else f"línea {i}")
        for i in range(1, 41)
    )
    BLOB = {("src/app.py", "a" * 40): LINEAS}

    def hechos(self):
        return domain.RepositoryFacts(blobs=self.BLOB)

    def digest(self, rango):

        extracto = "\n".join(self.LINEAS[rango[0] - 1 : rango[1]])
        return hashlib.sha256(extracto.encode("utf-8")).hexdigest()

    def hallazgo(self, fid, titulo, rango, digest, estado=domain.StatusOpen()):
        return domain.Finding(
            id=fid,
            title=titulo,
            severity="Medium",
            status=estado,
            primary_anchor=domain.AnchorLocated(
                path="src/app.py", blob_sha="a" * 40, range=rango, excerpt_digest=digest
            ),
        )

    def test_e_ancla_verificada_sin_candidato_es_nuevo(self):
        previos = [self.hallazgo("F1", "bug x", (5, 5), self.digest((5, 5)))]
        obs = _observacion(
            "división entre cero",
            primary_anchor=_ancla_locada_en((30, 31), self.digest((30, 31))),
        )
        self.assertEqual(
            domain.match_finding(previos, obs, self.hechos()),
            domain.MatchNew(),
            "un bug nuevo con ancla verificada no adopta al único vigente del archivo",
        )

    def test_f_extracto_generico_es_solo_plausible(self):
        previos = [
            self.hallazgo(
                "F1",
                "chequeo genérico",
                (20, 20),
                self.digest((20, 20)),
                estado=domain.StatusDismissed(command_id=None),
            )
        ]
        obs = _observacion(
            "crítico: retorno temprano sin liberar",
            severity="Critical",
            primary_anchor=_ancla_locada_en((40, 40), self.digest((40, 40))),
        )
        self.assertEqual(
            domain.match_finding(previos, obs, self.hechos()),
            domain.MatchAmbiguous(ids=("F1",)),
        )
        plan = domain.ReviewPlan(
            revision=domain.Revision(
                base_sha="b" * 40, head_sha="c" * 40, policy_digest="d" * 64
            ),
            changed_paths=("src/app.py",),
        )
        report = domain.validar_reporte([obs], domain.UNKNOWN, self.hechos())
        transicion = domain.accept_report(
            domain.Snapshot(
                schema=2,
                generation=1,
                revision=plan.revision,
                next_id=2,
                completion=domain.UNKNOWN,
                findings=previos,
                command_cursor=0,
            ),
            plan,
            report,
        )
        self.assertIsInstance(transicion, domain.Replace)
        por_id = {f.id: f for f in transicion.snapshot.findings}
        self.assertEqual(set(por_id), {"F1", "F2"})
        self.assertIsInstance(por_id["F1"].status, domain.StatusDismissed)
        self.assertIn("posible duplicado de F1", por_id["F2"].cause_hint)


# ---- F0 r5: tabla completa de matching contra memoria migrada legada ----


class MigracionLegadaConAnclasVerificadas(unittest.TestCase):
    LINEAS = tuple(
        "falta validar la entrada"
        if i == 10
        else ("bug y" if i == 30 else f"línea {i}")
        for i in range(1, 41)
    )
    BLOB = {("src/app.py", "a" * 40): LINEAS}

    def hechos(self):
        return domain.RepositoryFacts(blobs=self.BLOB)

    def digest(self, rango):

        extracto = "\n".join(self.LINEAS[rango[0] - 1 : rango[1]])
        return hashlib.sha256(extracto.encode("utf-8")).hexdigest()

    def observacion(self, titulo, rango):
        return domain.Observation(
            title=titulo,
            severity="High",
            primary_anchor=domain.AnchorLocated(
                path="src/app.py",
                blob_sha="a" * 40,
                range=rango,
                excerpt_digest=self.digest(rango),
            ),
            claim=domain.OPEN,
        )

    def previos(self):
        return [
            domain.Finding(
                id="F1",
                title="falta validar la entrada",
                severity="High",
                status=domain.StatusDismissed(command_id=None),
                primary_anchor=domain.AnchorLegacy(path="src/app.py", line=10),
            ),
            domain.Finding(
                id="F2",
                title="bug y",
                severity="Medium",
                status=domain.StatusOpen(),
                primary_anchor=domain.AnchorLegacy(path="src/app.py", line=30),
            ),
        ]

    def test_b5_migracion_sin_duplicar_descartes_ni_abiertos(self):
        previos = self.previos()
        obs1 = self.observacion("falta validar la entrada", (10, 12))
        obs2 = self.observacion("bug y", (30, 30))
        self.assertEqual(
            domain.match_finding(previos, obs1, self.hechos()),
            domain.MatchExisting(id="F1"),
        )
        self.assertEqual(
            domain.match_finding(previos, obs2, self.hechos()),
            domain.MatchExisting(id="F2"),
        )
        plan = domain.ReviewPlan(
            revision=domain.Revision(
                base_sha="b" * 40, head_sha="c" * 40, policy_digest="d" * 64
            ),
            changed_paths=("src/app.py",),
        )
        report = domain.validar_reporte([obs1, obs2], domain.UNKNOWN, self.hechos())
        transicion = domain.accept_report(
            domain.Snapshot(
                schema=2,
                generation=1,
                revision=plan.revision,
                next_id=3,
                completion=domain.UNKNOWN,
                findings=previos,
                command_cursor=7,
            ),
            plan,
            report,
        )
        self.assertIsInstance(transicion, domain.Replace)
        self.assertEqual(
            [f.id for f in transicion.snapshot.findings],
            ["F1", "F2"],
            "sin F3/F4: los ids persistidos se conservan",
        )
        por_id = {f.id: f for f in transicion.snapshot.findings}
        self.assertIsInstance(
            por_id["F1"].status,
            domain.StatusDismissed,
            "un descarte confirmado no reaparece por una migración",
        )
        self.assertIsInstance(por_id["F2"].status, domain.StatusOpen)

    def test_linea_legada_en_rango_con_otro_titulo_da_ambiguo(self):
        obs = self.observacion("otra cosa distinta", (10, 12))
        self.assertEqual(
            domain.match_finding(self.previos(), obs, self.hechos()),
            domain.MatchAmbiguous(ids=("F1",)),
        )


# ---- F0 r6: identidad por digest guardado cuando facts sólo trae HEAD ----


def _lineas_viejas():
    lineas = []
    for i in range(1, 41):
        if i in (2, 3):
            lineas.append("uno: la entrada no se valida")
        elif i in (30, 31):
            lineas.append("dos: división por cero al exportar")
        else:
            lineas.append(f"línea {i}")
    return tuple(lineas)


def _lineas_nuevas():
    # push 2: los dos extractos sobreviven (corridos a las líneas 12-13 y 32-33)
    lineas = [f"nuevo {i}" for i in range(1, 41)]
    lineas[11:13] = ["uno: la entrada no se valida", "uno: la entrada no se valida"]
    lineas[31:33] = [
        "dos: división por cero al exportar",
        "dos: división por cero al exportar",
    ]
    return tuple(lineas)


class IdentidadSoloBlobsHead(unittest.TestCase):
    # blob viejo (push 1) y blob HEAD (push 2): los extractos de los dos bugs
    # sobreviven la edición y quedan en otras líneas del blob nuevo.
    LINEAS_VIEJAS = _lineas_viejas()
    LINEAS_NUEVAS = _lineas_nuevas()
    BLOBS = {("src/app.py", "b" * 40): LINEAS_NUEVAS}  # SÓLO el blob HEAD

    def hechos(self):
        return domain.RepositoryFacts(blobs=self.BLOBS)

    def digest_viejo(self, rango):

        extracto = "\n".join(self.LINEAS_VIEJAS[rango[0] - 1 : rango[1]])
        return hashlib.sha256(extracto.encode("utf-8")).hexdigest()

    def digest_nuevo(self, rango):

        extracto = "\n".join(self.LINEAS_NUEVAS[rango[0] - 1 : rango[1]])
        return hashlib.sha256(extracto.encode("utf-8")).hexdigest()

    def previos(self):
        return [
            domain.Finding(
                id="F1",
                title="uno: la entrada no se valida",
                severity="High",
                status=domain.StatusDismissed(command_id=None),
                primary_anchor=domain.AnchorLocated(
                    path="src/app.py",
                    blob_sha="a" * 40,
                    range=(2, 3),
                    excerpt_digest=self.digest_viejo((2, 3)),
                ),
            ),
            domain.Finding(
                id="F2",
                title="dos: división por cero al exportar",
                severity="Critical",
                status=domain.StatusOpen(),
                primary_anchor=domain.AnchorLocated(
                    path="src/app.py",
                    blob_sha="a" * 40,
                    range=(30, 31),
                    excerpt_digest=self.digest_viejo((30, 31)),
                ),
            ),
        ]

    def observacion(self, titulo, rango, digest, claim=domain.OPEN):
        return domain.Observation(
            title=titulo,
            severity="Critical",
            primary_anchor=domain.AnchorLocated(
                path="src/app.py",
                blob_sha="b" * 40,
                range=rango,
                excerpt_digest=digest,
            ),
            claim=claim,
        )

    def test_solo_blobs_head_los_ids_sobreviven_el_segundo_push(self):
        previos = self.previos()
        obs1 = self.observacion(
            "uno: la entrada no se valida (revisado)",
            (12, 13),
            self.digest_nuevo((12, 13)),
        )
        obs2 = self.observacion(
            "dos: división por cero al exportar (revisado)",
            (32, 33),
            self.digest_nuevo((32, 33)),
        )
        self.assertEqual(
            domain.match_finding(previos, obs1, self.hechos()),
            domain.MatchExisting(id="F1"),
        )
        self.assertEqual(
            domain.match_finding(previos, obs2, self.hechos()),
            domain.MatchExisting(id="F2"),
        )
        plan = domain.ReviewPlan(
            revision=domain.Revision(
                base_sha="b" * 40, head_sha="c" * 40, policy_digest="d" * 64
            ),
            changed_paths=("src/app.py",),
        )
        report = domain.validar_reporte([obs1, obs2], domain.UNKNOWN, self.hechos())
        transicion = domain.accept_report(
            domain.Snapshot(
                schema=2,
                generation=1,
                revision=plan.revision,
                next_id=3,
                completion=domain.UNKNOWN,
                findings=previos,
                command_cursor=0,
            ),
            plan,
            report,
        )
        self.assertIsInstance(transicion, domain.Replace)
        por_id = {f.id: f for f in transicion.snapshot.findings}
        self.assertEqual(
            set(por_id), {"F1", "F2"}, "sin F3/F4: los ids persistidos se conservan"
        )
        self.assertIsInstance(por_id["F1"].status, domain.StatusDismissed)
        self.assertIsInstance(por_id["F2"].status, domain.StatusOpen)
        # F5: aserción diferencial endurecida — la identidad vino del digest
        # guardado; sin él la observación sería MatchNew (rojo contra dabe615)
        # y no habría duplicados por orden de mención.
        self.assertNotIn("F3", por_id, "sin duplicados por orden de mención")
        # el título es presentación mutable: accept lo actualiza desde la
        # observación; lo que no cambia es el id ni el estado.
        self.assertTrue(
            por_id["F2"].title.startswith("dos: división por cero al exportar")
        )


# ---- F0 r7: VUELVE r6 (blob editado), R-A order-drop y R-B severity ----


class VuelveR6BlobEditado(unittest.TestCase):
    # blob viejo (push 1) y blob HEAD (push 2, editado): la línea 10 cambió de
    # texto y se corrió a la 11; el título del hallazgo es el mismo.
    LINEAS_VIEJAS = tuple(
        "entrada sin validar (vieja)" if i == 10 else f"línea {i}" for i in range(1, 31)
    )
    LINEAS_NUEVAS = tuple(
        "la entrada valida el IVA tras el refactor" if i == 11 else f"línea {i}"
        for i in range(1, 31)
    )
    BLOBS = {("src/app.py", "b" * 40): LINEAS_NUEVAS}  # SÓLO el blob HEAD

    def hechos(self):
        return domain.RepositoryFacts(blobs=self.BLOBS)

    def digest(self, lineas, rango):

        extracto = "\n".join(lineas[rango[0] - 1 : rango[1]])
        return hashlib.sha256(extracto.encode("utf-8")).hexdigest()

    def test_descartado_y_abierto_con_blob_editado_no_reaparecen(self):
        previos = [
            domain.Finding(
                id="F1",
                title="chequeo de la entrada del formulario",
                severity="High",
                status=domain.StatusDismissed(command_id=None),
                primary_anchor=domain.AnchorLocated(
                    path="src/app.py",
                    blob_sha="a" * 40,
                    range=(10, 10),
                    excerpt_digest=self.digest(self.LINEAS_VIEJAS, (10, 10)),
                ),
            ),
            domain.Finding(
                id="F2",
                title="el cobro no registra el IVA",
                severity="Medium",
                status=domain.StatusOpen(),
                primary_anchor=domain.AnchorLocated(
                    path="src/cobro.py",
                    blob_sha="a" * 40,
                    range=(20, 21),
                    excerpt_digest=self.digest(self.LINEAS_VIEJAS, (20, 21)),
                ),
            ),
        ]
        obs1 = domain.Observation(
            title="chequeo de la entrada del formulario",
            severity="High",
            primary_anchor=domain.AnchorLocated(
                path="src/app.py",
                blob_sha="b" * 40,
                range=(11, 11),
                excerpt_digest=self.digest(self.LINEAS_NUEVAS, (11, 11)),
            ),
            claim=domain.OPEN,
        )
        obs2 = domain.Observation(
            title="el cobro no registra el IVA",
            severity="Medium",
            primary_anchor=domain.AnchorLocated(
                path="src/cobro.py",
                blob_sha="b" * 40,
                range=(20, 21),
                excerpt_digest=self.digest(self.LINEAS_NUEVAS, (20, 21)),
            ),
            claim=domain.OPEN,
        )
        self.assertEqual(
            domain.match_finding(previos, obs1, self.hechos()),
            domain.MatchExisting(id="F1"),
        )
        self.assertEqual(
            domain.match_finding(previos, obs2, self.hechos()),
            domain.MatchExisting(id="F2"),
        )
        plan = domain.ReviewPlan(
            revision=domain.Revision(
                base_sha="b" * 40, head_sha="c" * 40, policy_digest="d" * 64
            ),
            changed_paths=("src/app.py", "src/cobro.py"),
        )
        report = domain.validar_reporte([obs1, obs2], domain.UNKNOWN, self.hechos())
        transicion = domain.accept_report(
            domain.Snapshot(
                schema=2,
                generation=1,
                revision=plan.revision,
                next_id=3,
                completion=domain.UNKNOWN,
                findings=previos,
                command_cursor=0,
            ),
            plan,
            report,
        )
        self.assertIsInstance(transicion, domain.Replace)
        por_id = {f.id: f for f in transicion.snapshot.findings}
        self.assertEqual(set(por_id), {"F1", "F2"}, "sin duplicados nuevos")
        self.assertIsInstance(por_id["F1"].status, domain.StatusDismissed)
        self.assertIsInstance(por_id["F2"].status, domain.StatusOpen)


class SegundaMencion(unittest.TestCase):
    def test_b2_la_segunda_mencion_entra_separada_con_marca(self):
        previos = [
            domain.Finding(
                id="F1",
                title="T",
                severity="Low",
                status=domain.StatusOpen(),
                primary_anchor=domain.AnchorLegacy(path="src/t.py", line=5),
            )
        ]
        obs_nuevo = _observacion("bug nuevo X", ruta="src/t.py")
        obs_dup = _observacion("T", ruta="src/t.py")
        plan = domain.ReviewPlan(
            revision=domain.Revision(
                base_sha="b" * 40, head_sha="c" * 40, policy_digest="d" * 64
            ),
            changed_paths=("src/t.py",),
        )
        report = domain.validar_reporte(
            [obs_nuevo, obs_dup], domain.UNKNOWN, domain.RepositoryFacts()
        )
        transicion = domain.accept_report(
            domain.Snapshot(
                schema=2,
                generation=1,
                revision=plan.revision,
                next_id=2,
                completion=domain.UNKNOWN,
                findings=previos,
                command_cursor=0,
            ),
            plan,
            report,
        )
        self.assertIsInstance(transicion, domain.Replace)
        por_id = {f.id: f for f in transicion.snapshot.findings}
        # F1 conserva su id con el contenido de la primera mención; la
        # segunda entra separada con marca de posible duplicado.
        self.assertEqual(por_id["F1"].title, "bug nuevo X")
        self.assertEqual(por_id["F2"].title, "T")
        self.assertIn("posible duplicado de F1", por_id["F2"].cause_hint)


class SeveridadNormalizada(unittest.TestCase):
    def _aceptar(self, severity):
        previos = [
            domain.Finding(
                id="F1",
                title="bug x",
                severity="Low",
                status=domain.StatusOpen(),
                primary_anchor=domain.AnchorLegacy(path="src/x.py", line=1),
            )
        ]
        obs = _observacion("bug x", ruta="src/x.py")
        obs.severity = severity
        plan = domain.ReviewPlan(
            revision=domain.Revision(
                base_sha="b" * 40, head_sha="c" * 40, policy_digest="d" * 64
            ),
            changed_paths=("src/x.py",),
        )
        report = domain.validar_reporte([obs], domain.UNKNOWN, domain.RepositoryFacts())
        return domain.accept_report(
            domain.Snapshot(
                schema=2,
                generation=1,
                revision=plan.revision,
                next_id=2,
                completion=domain.UNKNOWN,
                findings=previos,
                command_cursor=0,
            ),
            plan,
            report,
        )

    def test_b3_alias_minusculas_se_normaliza(self):
        transicion = self._aceptar("high")
        self.assertEqual(transicion.snapshot.findings[0].severity, "High")
        bloque = domain.encode_snapshot(transicion.snapshot)
        self.assertIsInstance(bloque, str, "encode sin ValueError")

    def test_b3_invalida_se_degrada_con_marca(self):
        transicion = self._aceptar("critico")
        self.assertEqual(transicion.snapshot.findings[0].severity, "Medium")
        bloque = domain.encode_snapshot(transicion.snapshot)
        self.assertIsInstance(bloque, str)


# ---- F0 r7: R-A order-drop, R-B severity, R-C cableado ----


class OrdenDeMenciones(unittest.TestCase):
    def test_b5_la_segunda_mencion_entra_separada_con_marca(self):
        previos = [
            domain.Finding(
                id="F1",
                title="T",
                severity="Low",
                status=domain.StatusOpen(),
                primary_anchor=domain.AnchorLegacy(path="src/t.py", line=5),
            )
        ]
        obs_t = _observacion("T", ruta="src/t.py")
        obs_nuevo = _observacion("bug nuevo X", ruta="src/t.py")
        plan = domain.ReviewPlan(
            revision=domain.Revision(
                base_sha="b" * 40, head_sha="c" * 40, policy_digest="d" * 64
            ),
            changed_paths=("src/t.py",),
        )
        report = domain.validar_reporte(
            [obs_t, obs_nuevo], domain.UNKNOWN, domain.RepositoryFacts()
        )
        transicion = domain.accept_report(
            domain.Snapshot(
                schema=2,
                generation=1,
                revision=plan.revision,
                next_id=2,
                completion=domain.UNKNOWN,
                findings=previos,
                command_cursor=0,
            ),
            plan,
            report,
        )
        self.assertIsInstance(transicion, domain.Replace)
        por_id = {f.id: f for f in transicion.snapshot.findings}
        # la primera mención gana el id de F1; la segunda (duplicado del fid
        # ya tocado) entra separada con marca de posible duplicado.
        self.assertEqual(por_id["F1"].title, "T")
        self.assertIn("F2", por_id)
        self.assertEqual(por_id["F2"].title, "bug nuevo X")
        self.assertIn("posible duplicado de F1", por_id["F2"].cause_hint)


class SeveridadDeObservacion(unittest.TestCase):
    def _aceptar(self, severity):
        previos = [
            domain.Finding(
                id="F1",
                title="bug x",
                severity="Low",
                status=domain.StatusOpen(),
                primary_anchor=domain.AnchorLegacy(path="src/x.py", line=1),
            )
        ]
        obs = _observacion("bug x", ruta="src/x.py")
        obs.severity = severity
        plan = domain.ReviewPlan(
            revision=domain.Revision(
                base_sha="b" * 40, head_sha="c" * 40, policy_digest="d" * 64
            ),
            changed_paths=("src/x.py",),
        )
        report = domain.validar_reporte([obs], domain.UNKNOWN, domain.RepositoryFacts())
        return domain.accept_report(
            domain.Snapshot(
                schema=2,
                generation=1,
                revision=plan.revision,
                next_id=2,
                completion=domain.UNKNOWN,
                findings=previos,
                command_cursor=0,
            ),
            plan,
            report,
        )

    def test_b6_alias_minusculas_se_normaliza(self):
        transicion = self._aceptar("high")
        self.assertEqual(transicion.snapshot.findings[0].severity, "High")
        # y el encode no revienta con el valor normalizado
        self.assertIsInstance(domain.encode_snapshot(transicion.snapshot), str)

    def test_b6_invalida_se_degrada_con_marca(self):
        transicion = self._aceptar("critico")
        self.assertEqual(transicion.snapshot.findings[0].severity, "Medium")
        self.assertIsInstance(domain.encode_snapshot(transicion.snapshot), str)


# ---- F0 r7: R-C parser de observaciones (el env lo valida el adaptador) ----


class ObservacionDeEntrada(unittest.TestCase):
    def test_bloque_con_anclas_se_parsea_a_located(self):
        entrada = {
            "title": "t",
            "severity": "high",
            "file": "a.py",
            "line": 3,
            "anchor": {
                "path": "a.py",
                "blob_sha": "a" * 40,
                "range": [3, 4],
                "excerpt_digest": "x" * 64,
            },
            "related_anchors": [
                {"path": "b.py", "line": 7},
            ],
            "evidence": [{"kind": "unverified", "text": "no verificado"}],
            "claim": "open",
        }
        obs = domain.observation_de_entrada(entrada)
        self.assertIsInstance(obs.primary_anchor, domain.AnchorLocated)
        self.assertEqual(obs.primary_anchor.blob_sha, "a" * 40)
        self.assertEqual([type(a) for a in obs.related_anchors], [domain.AnchorLegacy])
        self.assertEqual([type(e) for e in obs.evidence], [domain.EvidenceUnverified])
        self.assertEqual(obs.claim, domain.OPEN)

    def test_bloque_legacy_se_parsea_a_anchor_legacy(self):
        entrada = {"title": "t", "severity": "High", "file": "a.py", "line": 3}
        obs = domain.observation_de_entrada(entrada)
        self.assertIsInstance(obs.primary_anchor, domain.AnchorLegacy)
        self.assertEqual(
            (obs.primary_anchor.path, obs.primary_anchor.line), ("a.py", 3)
        )


# ---- F0 r8: título único con otro P_leg en el rango observado ----


class TituloUnicoConOtroLegadoEnRango(unittest.TestCase):
    def test_titulo_unico_con_otro_legado_en_rango_da_ambiguo(self):
        previos = [
            domain.Finding(
                id="F1",
                title="T",
                severity="Low",
                status=domain.StatusOpen(),
                primary_anchor=domain.AnchorLegacy(path="src/app.py", line=10),
            ),
            domain.Finding(
                id="F2",
                title="otro",
                severity="Medium",
                status=domain.StatusOpen(),
                primary_anchor=domain.AnchorLegacy(path="src/app.py", line=11),
            ),
        ]
        obs = _observacion("T", ruta="src/app.py")
        obs.primary_anchor = domain.AnchorLocated(
            path="src/app.py",
            blob_sha="a" * 40,
            range=(10, 12),
            excerpt_digest=self.digest((10, 12)),
        )
        self.assertEqual(
            domain.match_finding(previos, obs, self.hechos()),
            domain.MatchAmbiguous(ids=("F1", "F2")),
        )

    def digest(self, rango):
        import hashlib

        extracto = "\n".join(f"línea {i}" for i in range(rango[0], rango[1] + 1))
        return hashlib.sha256(extracto.encode("utf-8")).hexdigest()

    def hechos(self):
        lineas = tuple(f"línea {i}" for i in range(1, 41))
        return domain.RepositoryFacts(blobs={("src/app.py", "a" * 40): lineas})


class Schema3Compatibility(unittest.TestCase):
    def test_preserva_estado_y_solicitudes(self):
        legado = domain.serialize_findings(
            {
                "findings": [
                    {
                        "id": "F1",
                        "file": "a.py",
                        "line": 1,
                        "severity": "Low",
                        "title": "legado abierto",
                        "state": "OPEN",
                    },
                    {
                        "id": "F2",
                        "file": "b.py",
                        "line": 2,
                        "severity": "Low",
                        "title": "legado descartado",
                        "state": "DISMISSED",
                    },
                ],
                "next": 5,
                "seen": 98,
            }
        )
        carga = domain.read_snapshot(legado)
        self.assertIsInstance(carga, domain.Legacy)
        self.assertEqual([f.id for f in carga.snapshot.findings], ["F1", "F2"])
        self.assertEqual(carga.snapshot.next_id, 5)
        self.assertEqual(carga.snapshot.command_cursor, 98)
        self.assertEqual(carga.snapshot.completion, domain.UNKNOWN)

        v2 = snapshot_v2()
        bloque = domain.encode_snapshot(v2)
        carga = domain.read_snapshot(bloque)
        self.assertIsInstance(carga, domain.Valid)
        s = carga.snapshot
        self.assertEqual(s.schema, 2)
        self.assertEqual([f.id for f in s.findings], ["F1", "F2", "F3"])
        self.assertEqual(s.next_id, 4)
        self.assertIsInstance(s.findings[2].status, domain.StatusDismissed)
        self.assertEqual(s.command_cursor, 98)
        self.assertEqual(s.pending_requests[0].id, "req-1")
        self.assertEqual(s.pending_requests[0].kind, "explain")
        self.assertEqual(s.pending_requests[0].finding_id, "F1")

        v3 = domain.snapshot_a_v3(v2)
        self.assertEqual(v3.schema, 3)
        self.assertEqual(v3.request_count, 1)
        self.assertEqual(v3.pending_requests[0].id, 1)
        self.assertEqual(v3.pending_requests[0].legacy_id, "req-1")
        self.assertEqual(v3.pending_requests[0].kind, "explain")
        self.assertEqual(v3.pending_requests[0].finding_id, "F1")
        v3 = domain.replace(
            v3,
            receipts=(
                domain.Receipt(command_id=98, effect="descartado F3 por comando"),
            ),
        )
        enc = domain.encode_snapshot(v3, domain.StorageBudget())
        self.assertIsInstance(enc, domain.EncodedCheckpoint)
        self.assertEqual(enc.schema, 3)
        self.assertEqual(enc.bytes, len(enc.block.encode("utf-8")))
        carga = domain.read_snapshot(enc.block)
        self.assertIsInstance(carga, domain.Valid)
        s = carga.snapshot
        self.assertEqual(s.schema, 3)
        self.assertEqual([f.id for f in s.findings], ["F1", "F2", "F3"])
        self.assertEqual(s.next_id, 4)
        self.assertIsInstance(s.findings[2].status, domain.StatusDismissed)
        self.assertEqual(s.findings[2].status.command_id, 98)
        self.assertEqual(s.command_cursor, 98)
        self.assertEqual(s.request_count, 1)
        self.assertEqual(s.pending_requests[0].id, 1)
        self.assertEqual(s.pending_requests[0].legacy_id, "req-1")
        self.assertEqual(s.pending_requests[0].kind, "explain")
        self.assertEqual(s.pending_requests[0].finding_id, "F1")
        self.assertEqual(s.receipts[0].command_id, 98)
        self.assertEqual(s.receipts[0].effect, "descartado F3 por comando")

    def test_invalid_or_future_never_initializes_empty(self):
        carga = domain.read_snapshot("")
        self.assertIsInstance(carga, domain.Missing)
        corrupto = domain.read_snapshot(
            domain.FINDINGS_PREFIX
            + '{"schema": 3, "generation": 1, "findings": [{"id": "F1"}]}'
            + domain.FINDINGS_SUFFIX
        )
        self.assertIsInstance(corrupto, domain.Invalid)
        self.assertNotIsInstance(corrupto, domain.Missing)
        futuro = domain.read_snapshot(
            domain.FINDINGS_PREFIX
            + '{"schema": 4, "generation": 1, "findings": []}'
            + domain.FINDINGS_SUFFIX
        )
        self.assertIsInstance(futuro, domain.Future)
        self.assertEqual(futuro.version, 4)
        legado = domain.read_snapshot(
            domain.serialize_findings(
                {
                    "findings": [
                        {
                            "id": "F1",
                            "file": "a.py",
                            "line": 1,
                            "severity": "Low",
                            "title": "t",
                            "state": "OPEN",
                        }
                    ],
                    "next": 2,
                    "seen": 0,
                }
            )
        )
        self.assertEqual(legado.snapshot.completion, domain.UNKNOWN)

    def test_identity_strings_roundtrip(self):
        raro = "src/niño\ttab\nlínea -->x --!>y ✓"
        v3 = domain.snapshot_a_v3(snapshot_v2())
        v3 = domain.replace(
            v3,
            findings=[
                domain.replace(
                    v3.findings[0],
                    title=raro,
                    primary_anchor=domain.AnchorLocated(
                        path=raro,
                        blob_sha="a" * 40,
                        range=(10, 20),
                        excerpt_digest="d1",
                        symbol_hint=raro,
                    ),
                    cause_hint=raro,
                    evidence=[domain.EvidenceUnverified(text=raro)],
                )
            ]
            + v3.findings[1:],
            pending_requests=[
                domain.WorkRequest(
                    id=1, legacy_id="req-->1 --!>2", kind="explain", finding_id="F1"
                )
            ],
            receipts=[domain.Receipt(command_id=98, effect="descarta --> F3")],
        )
        enc = domain.encode_snapshot(v3, domain.StorageBudget())
        cuerpo = enc.block[: len(enc.block) - len(domain.FINDINGS_SUFFIX)]
        self.assertNotIn("-->", cuerpo)
        self.assertNotIn("--!>", cuerpo)
        self.assertIn("--\\u003e", cuerpo)
        carga = domain.read_snapshot(enc.block)
        self.assertIsInstance(carga, domain.Valid)
        s = carga.snapshot
        f = s.findings[0]
        self.assertEqual(f.title, raro)
        self.assertEqual(f.primary_anchor.path, raro)
        self.assertEqual(f.primary_anchor.symbol_hint, raro)
        self.assertEqual(f.cause_hint, raro)
        self.assertEqual(f.evidence[0].text, raro)
        self.assertEqual(s.pending_requests[0].legacy_id, "req-->1 --!>2")
        self.assertEqual(s.receipts[0].effect, "descarta --> F3")

    def test_v2_rechaza_ids_de_solicitud_duplicados(self):
        v2 = json.loads(
            domain.encode_snapshot(snapshot_v2())[
                len(domain.FINDINGS_PREFIX) : -len(domain.FINDINGS_SUFFIX)
            ]
        )
        v2["pending_requests"].append(dict(v2["pending_requests"][0]))
        bloque = domain.FINDINGS_PREFIX + json.dumps(v2) + domain.FINDINGS_SUFFIX
        carga = domain.read_snapshot(bloque)
        self.assertIsInstance(carga, domain.Invalid)
        self.assertIn("duplicado", carga.reason)

    def test_descartes_solo_tocan_abiertos_y_no_inflan_cursor(self):
        """B1 ronda 2: 'descartar todo' respeta resueltos y el cursor."""
        snapshot = snapshot_v2()
        snapshot = domain.snapshot_a_v3(snapshot)
        snapshot = replace(
            snapshot,
            command_cursor=98,
            receipts=(domain.Receipt(command_id=98, effect="recibo previo"),),
        )
        descartados = domain.aplicar_descartes(
            snapshot,
            {"F1", "F2", "F999"},
            True,
            comment_id=99,
        )
        por_id = {f.id: f for f in descartados.findings}
        self.assertIsInstance(
            por_id["F1"].status, domain.StatusDismissed, "abierto: descartado"
        )
        self.assertEqual(por_id["F1"].status.command_id, 99, "command_id = comentario")
        self.assertEqual(
            por_id["F2"].status,
            domain.StatusResolved(at_sha="b" * 40),
            "'descartar todo' no toca resueltos",
        )
        self.assertEqual(
            por_id["F3"].status.command_id,
            98,
            "el descartado previo conserva su command_id",
        )
        self.assertEqual(
            descartados.command_cursor, 98, "el cursor no se infla con descartes"
        )
        self.assertEqual(
            [r.command_id for r in descartados.receipts],
            [98, 99],
            "un recibo por comando, tras los previos",
        )

    def test_validador_v3_rechaza_bloques_malformados(self):
        """Frontera del codec: un rechazo por regla, con su motivo afirmado."""
        base = json.loads(
            domain.encode_snapshot(
                domain.snapshot_a_v3(snapshot_v2()), domain.StorageBudget()
            ).block[len(domain.FINDINGS_PREFIX) : -len(domain.FINDINGS_SUFFIX)]
        )

        def variante(cambios):
            payload = json.loads(json.dumps(base))
            cambios(payload)
            return domain.FINDINGS_PREFIX + json.dumps(payload) + domain.FINDINGS_SUFFIX

        def id_duplicado(p):
            p["pending_requests"].append(
                {"id": 1, "legacy_id": "otro", "kind": "explain", "finding_id": "F1"}
            )
            p["request_count"] = 2

        def id_menor_que_uno(p):
            p["pending_requests"][0]["id"] = 0

        def id_fuera_de_count(p):
            p["pending_requests"][0]["id"] = 2
            p["request_count"] = 1

        def legacy_duplicado(p):
            p["pending_requests"].append(
                {"id": 2, "legacy_id": "req-1", "kind": "explain", "finding_id": "F1"}
            )
            p["request_count"] = 2

        def recibo_duplicado(p):
            if not p["receipts"]:
                p["receipts"].append({"command_id": 98, "effect": "efecto"})
            p["receipts"].append(dict(p["receipts"][0]))

        def origin_no_texto(p):
            p["pending_requests"][0]["origin"] = {"mal": 1}

        def base_de_generacion_invalida(p):
            p["pending_requests"][0]["basis_generation"] = "x"

        def state_vacio(p):
            p["pending_requests"][0]["state"] = ""

        def recibo_vencido(p):
            p["receipts"].append({"command_id": 99, "effect": "efecto"})

        casos = [
            (id_duplicado, "id de solicitud duplicado"),
            (id_menor_que_uno, "id de solicitud menor que 1"),
            (id_fuera_de_count, "supera request_count"),
            (legacy_duplicado, "legacy_id de solicitud duplicado"),
            (origin_no_texto, "origin de solicitud debe ser texto"),
            (base_de_generacion_invalida, "basis_generation de solicitud inválida"),
            (state_vacio, "state de solicitud inválido"),
            (recibo_duplicado, "command_id de recibo duplicado"),
            (recibo_vencido, "supera command_cursor"),
        ]
        for cambios, motivo in casos:
            with self.subTest(regla=motivo):
                carga = domain.read_snapshot(variante(cambios))
                self.assertIsInstance(carga, domain.Invalid)
                self.assertIn(motivo, carga.reason)

    def test_normaliza_politica_una_vez(self):
        p = domain.normalize_policy(
            {
                "finding_identity": "titles",
                "findings_max_count": 25,
                "findings_max_bytes": 9000,
                "rules_digest": "abc",
                "exclude_patterns": ["dist/**"],
                "schema_version": 3,
                "diff_mode": "incremental",
            }
        )
        self.assertEqual(p.finding_identity, "current")
        self.assertEqual(p.findings_max_count, 25)
        self.assertEqual(p.findings_max_bytes, 9000)
        self.assertEqual(p.rules_digest, "abc")
        self.assertEqual(p.exclude_patterns, ("dist/**",))
        self.assertEqual(p.schema_version, 3)
        self.assertEqual(p.diff_mode, "incremental")
        self.assertEqual(domain.normalize_policy({}).finding_identity, "current")
        self.assertEqual(
            domain.normalize_policy({"exclude_patterns": None}).exclude_patterns, ()
        )
        with self.assertRaises(ValueError):
            domain.normalize_policy({"finding_identity": "bogus"})
        with self.assertRaises(ValueError):
            domain.normalize_policy({"diff_mode": "bogus"})


class CheckpointCapacity(unittest.TestCase):
    """T05: perfil de estado propuesto y límites del comentario completo."""

    def _snapshot_rico(self, relleno="x"):
        hallazgos = [
            domain.Finding(
                id=f"F{i}",
                title=f"hallazgo enriquecido {i} " + "ñ" * 200,
                severity="High",
                status=domain.StatusOpen(),
                primary_anchor=domain.AnchorLocated(
                    path=f"src/modulo_{i}.py",
                    blob_sha="a" * 40,
                    range=(10, 20),
                    excerpt_digest="d1",
                    symbol_hint=f"simbolo_{i}",
                ),
                related_anchors=[domain.AnchorLegacy(path=f"src/rel_{i}.py", line=3)],
                cause_hint="causa con --> y --!> dentro",
                evidence=[
                    domain.EvidenceSource(
                        anchor=domain.AnchorLegacy(path=f"src/e_{i}.py", line=7)
                    ),
                    domain.EvidenceCheck(
                        check_id=f"ci-{i}",
                        head_sha="b" * 40,
                        producer="ci",
                        conclusion="pass",
                        url=f"https://ejemplo/{i}",
                    ),
                    domain.EvidenceUnverified(text="sin verificación --> aún"),
                ],
            )
            for i in range(8)
        ]
        hallazgos.append(
            domain.Finding(
                id="F9",
                title="descartado confirmado",
                severity="Low",
                status=domain.StatusDismissed(command_id=12),
                primary_anchor=domain.AnchorLegacy(path="old.py", line=1),
            )
        )
        return domain.Snapshot(
            schema=3,
            generation=5,
            revision=domain.Revision(
                base_sha="b" * 40, head_sha="c" * 40, policy_digest="d" * 64
            ),
            next_id=20,
            completion=domain.PARTIAL,
            findings=hallazgos,
            command_cursor=12,
            request_count=2,
            pending_requests=[
                domain.WorkRequest(
                    id=1, kind="explain", finding_id="F1", legacy_id="req-1"
                ),
                domain.WorkRequest(id=2, kind="review", origin="comentario"),
            ],
            receipts=(
                domain.Receipt(command_id=12, effect="descartados por comando: F9"),
                domain.Receipt(command_id=11, effect="descartar todo"),
            ),
        )

    def test_representative_state_roundtrip(self):
        perfil = domain.StorageBudget(
            max_bytes=domain.STATE_BYTES_PROPOSED,
            comment_max_bytes=domain.COMMENT_MAX_BYTES,
            comment_max_chars=domain.COMMENT_MAX_CHARS,
        )
        encoded = domain.encode_snapshot(self._snapshot_rico(), perfil)
        self.assertIsInstance(encoded, domain.EncodedCheckpoint)
        self.assertEqual(encoded.schema, 3)
        self.assertLessEqual(encoded.bytes, domain.STATE_BYTES_PROPOSED)
        carga = domain.read_snapshot(encoded.block)
        self.assertIsInstance(carga, domain.Valid)
        s = carga.snapshot
        self.assertEqual(s.request_count, 2)
        self.assertEqual([r.command_id for r in s.receipts], [12, 11])
        self.assertEqual(len(s.findings), 9)

        # Con el perfil propuesto el comentario respeta 60000 bytes / 60000
        # caracteres, y los límites del presupuesto mandan (recorte por bytes
        # y por caracteres, discriminables por separado).
        resultado = {"result": "COVERAGE: partial\n" + ("detalle ñ " * 7000)}
        manifest = {
            "mode": "full",
            "reason": "no-prev",
            "reviewed": ["src/modulo_0.py"],
            "excluded": [],
        }
        comentario = review.compose(
            resultado,
            manifest,
            sha="e" * 40,
            provider="opencode-go",
            findings=None,
            budget=perfil,
        )
        self.assertLessEqual(len(comentario.encode("utf-8")), domain.COMMENT_MAX_BYTES)
        self.assertLessEqual(len(comentario), domain.COMMENT_MAX_CHARS)

        ajustado = domain.StorageBudget(
            max_bytes=domain.STATE_BYTES_PROPOSED,
            comment_max_bytes=20000,
            comment_max_chars=15000,
        )
        ascii_ = {"result": "COVERAGE: partial\n" + "d" * 40000}
        con_ascii = review.compose(
            ascii_,
            manifest,
            sha="e" * 40,
            provider="opencode-go",
            budget=ajustado,
        )
        self.assertLessEqual(len(con_ascii), ajustado.comment_max_chars)
        multibyte = {"result": "COVERAGE: partial\n" + "ñ" * 30000}
        con_multibyte = review.compose(
            multibyte,
            manifest,
            sha="e" * 40,
            provider="opencode-go",
            budget=ajustado,
        )
        self.assertLessEqual(
            len(con_multibyte.encode("utf-8")), ajustado.comment_max_bytes
        )

    def test_presupuesto_en_camino_con_hallazgos(self):
        """B: budget aplica con merged no vacío y jamás recorta el checkpoint."""
        perfil = domain.StorageBudget(
            max_bytes=domain.STATE_BYTES_PROPOSED,
            comment_max_bytes=domain.COMMENT_MAX_BYTES,
            comment_max_chars=domain.COMMENT_MAX_CHARS,
        )
        ajustado = domain.StorageBudget(
            max_bytes=domain.STATE_BYTES_PROPOSED,
            comment_max_bytes=20000,
            comment_max_chars=20000,
        )
        snapshot = self._snapshot_rico()
        encoded = domain.encode_snapshot(snapshot, perfil)
        self.assertIsInstance(encoded, domain.EncodedCheckpoint)
        hallazgos = [
            {
                "id": f.id,
                "file": f.primary_anchor.path,
                "line": 1,
                "severity": f.severity,
                "title": f.title,
                "state": "open",
            }
            for f in snapshot.findings
        ]
        findings = {
            "merged": hallazgos,
            "new_ids": ["F0", "F1"],
            "block": encoded.block,
            "model_ok": True,
        }
        resultado = {"result": "COVERAGE: partial\n" + ("detalle largo " * 3000)}
        manifest = {
            "mode": "full",
            "reason": "no-prev",
            "reviewed": ["src/modulo_0.py"],
            "excluded": [],
        }
        comentario = review.compose(
            resultado,
            manifest,
            sha="e" * 40,
            provider="opencode-go",
            findings=findings,
            budget=ajustado,
        )
        self.assertLessEqual(
            len(comentario.encode("utf-8")), ajustado.comment_max_bytes
        )
        self.assertLessEqual(len(comentario), ajustado.comment_max_chars)
        self.assertIn(
            encoded.block,
            comentario,
            "el checkpoint viaja íntegro, jamás recortado",
        )

    def test_budget_mas_chico_que_el_checkpoint_no_lo_recorta(self):
        """B: presupuesto menor que el checkpoint → se rechaza el budget."""
        perfil = domain.StorageBudget(
            max_bytes=domain.STATE_BYTES_PROPOSED,
            comment_max_bytes=domain.COMMENT_MAX_BYTES,
            comment_max_chars=domain.COMMENT_MAX_CHARS,
        )
        snapshot = self._snapshot_rico()
        encoded = domain.encode_snapshot(snapshot, perfil)
        self.assertGreater(encoded.bytes, 9000)
        findings = {
            "merged": [],
            "new_ids": [],
            "block": encoded.block,
            "model_ok": True,
        }
        resultado = {"result": "COVERAGE: partial\n"}
        manifest = {
            "mode": "full",
            "reason": "no-prev",
            "reviewed": [],
            "excluded": [],
        }
        chico = domain.StorageBudget(
            max_bytes=domain.STATE_BYTES_PROPOSED,
            comment_max_bytes=5000,
            comment_max_chars=5000,
        )
        comentario = review.compose(
            resultado,
            manifest,
            sha="e" * 40,
            provider="opencode-go",
            findings=findings,
            budget=chico,
        )
        self.assertIn(
            encoded.block,
            comentario,
            "el checkpoint confirmado viaja completo",
        )

    def test_estructura_con_budget_sin_duplicados(self):
        findings = {
            "merged": [
                {
                    "id": "F1",
                    "file": "a.py",
                    "line": 1,
                    "severity": "Low",
                    "title": "t",
                    "state": "open",
                }
            ],
            "new_ids": [],
            "block": domain.encode_snapshot(
                domain.Snapshot(
                    schema=2,
                    generation=1,
                    revision=domain.Revision(
                        base_sha="b" * 40,
                        head_sha="c" * 40,
                        policy_digest="d" * 64,
                    ),
                    next_id=2,
                    completion=domain.PARTIAL,
                    findings=[
                        domain.Finding(
                            id="F1",
                            title="t",
                            severity="Low",
                            status=domain.StatusOpen(),
                            primary_anchor=domain.AnchorLegacy(path="a.py", line=1),
                        )
                    ],
                    command_cursor=0,
                )
            ),
            "model_ok": True,
        }
        snapshot_chico = domain.Snapshot(
            schema=2,
            generation=1,
            revision=domain.Revision(
                base_sha="b" * 40, head_sha="c" * 40, policy_digest="d" * 64
            ),
            next_id=2,
            completion=domain.PARTIAL,
            findings=[
                domain.Finding(
                    id="F1",
                    title="t",
                    severity="Low",
                    status=domain.StatusOpen(),
                    primary_anchor=domain.AnchorLegacy(path="a.py", line=1),
                )
            ],
            command_cursor=0,
        )
        findings_block = domain.encode_snapshot(snapshot_chico)
        findings["block"] = findings_block
        args = (
            {
                "result": "COVERAGE: complete\n"
                + (
                    "texto del revisor y detalles largos con muchísimo relleno adicional "
                    * 5000
                )
            },
            {"mode": "full", "reason": "no-prev", "reviewed": ["a.py"], "excluded": []},
        )
        for nombre, budget in (
            ("sin budget", None),
            (
                "con budget",
                domain.StorageBudget(max_bytes=domain.STATE_BYTES_PROPOSED),
            ),
        ):
            with self.subTest(rama=nombre):
                out = review.compose(
                    *args,
                    sha="e" * 40,
                    provider="opencode-go",
                    findings=findings,
                    budget=budget,
                )
                self.assertEqual(out.count("## Detalle del revisor"), 1)
                self.assertEqual(out.count("Alcance de la revisión"), 1)
                if budget is not None:
                    detalle = out[out.index("## Detalle del revisor") :]
                    self.assertIn(
                        "_(Revisión recortada al presupuesto de capacidad.)_",
                        detalle,
                        "la prosa recortada y su aviso viven en Detalle",
                    )

    def test_sin_revisables_publica_el_mensaje_y_coincide_con_la_base(self):
        doradas = [
            (
                {"result": ""},
                {
                    "mode": "full",
                    "reason": "no-prev",
                    "reviewed": [],
                    "excluded": [{"path": "dist/x.js", "reason": "filtro dist/**"}],
                },
                "<!-- ai-review:sticky -->\n<!-- ai-review:sha=eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee -->\n<!-- ai-review:completion=eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee:complete -->\n### Revisión automática · DeepSeek V4.1 Flash · OpenCode Go · eeeeeee\n\nNo hay archivos revisables en este PR (todo quedó excluido por filtro).\n\n<details><summary>Alcance de la revisión</summary>\n\n- Revisados: 0 archivo(s)\n- Excluidos: 1\n  - `dist/x.js` (filtro dist/**)\n\n</details>",
            ),
            (
                {"result": "COVERAGE: partial\ntexto del revisor"},
                {
                    "mode": "full",
                    "reason": "no-prev",
                    "reviewed": ["a.py"],
                    "excluded": [],
                },
                "<!-- ai-review:sticky -->\n<!-- ai-review:sha=eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee -->\n<!-- ai-review:completion=eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee:partial -->\n### Revisión automática · DeepSeek V4.1 Flash · OpenCode Go · eeeeeee\n\n> [!WARNING]\n> **Revisión incompleta:** el revisor no declaró su cobertura.\n\nCOVERAGE: partial\ntexto del revisor\n\n<details><summary>Alcance de la revisión</summary>\n\n- Revisados: 1 archivo(s)\n\n</details>",
            ),
        ]
        for resultado, manifest, dorada in doradas:
            with self.subTest(reviewed=len(manifest["reviewed"])):
                out = review.compose(
                    resultado,
                    manifest,
                    sha="e" * 40,
                    provider="opencode-go",
                )
                self.assertEqual(out, dorada, "byte-idéntico a la base")

    def test_sin_revisables_con_presupuesto_publica_el_mismo_mensaje(self):
        manifest = {
            "mode": "full",
            "reason": "no-prev",
            "reviewed": [],
            "excluded": [{"path": "dist/x.js", "reason": "filtro dist/**"}],
        }
        sin = review.compose(
            {"result": ""}, manifest, sha="e" * 40, provider="opencode-go"
        )
        con = review.compose(
            {"result": ""},
            manifest,
            sha="e" * 40,
            provider="opencode-go",
            budget=domain.StorageBudget(max_bytes=domain.STATE_BYTES_PROPOSED),
        )
        self.assertEqual(con, sin)
        self.assertIn(
            "No hay archivos revisables en este PR (todo quedó excluido por filtro).",
            con,
        )

    def _hallazgos_chicos(self, titulo="t", cuantos=1):
        snapshot = domain.Snapshot(
            schema=2,
            generation=1,
            revision=domain.Revision(
                base_sha="b" * 40, head_sha="c" * 40, policy_digest="d" * 64
            ),
            next_id=cuantos + 1,
            completion=domain.PARTIAL,
            findings=[
                domain.Finding(
                    id="F1",
                    title="t",
                    severity="Low",
                    status=domain.StatusOpen(),
                    primary_anchor=domain.AnchorLegacy(path="a.py", line=1),
                )
            ],
            command_cursor=0,
        )
        merged = [
            {
                "id": f"F{i}",
                "file": "a.py",
                "line": i,
                "severity": "Low",
                "title": titulo,
                "state": "open",
            }
            for i in range(1, cuantos + 1)
        ]
        return {
            "merged": merged,
            "new_ids": [],
            "block": domain.encode_snapshot(snapshot),
            "model_ok": True,
        }

    def test_margen_menor_que_el_aviso_no_excede_el_presupuesto(self):
        manifest = {
            "mode": "full",
            "reason": "no-prev",
            "reviewed": ["a.py"],
            "excluded": [],
        }
        resultado = {"result": "COVERAGE: complete\n" + "x" * 5000}
        for camino, findings in (
            ("genérico", None),
            ("hallazgos", self._hallazgos_chicos()),
        ):
            sin = review.compose(
                resultado,
                manifest,
                sha="e" * 40,
                provider="opencode-go",
                findings=findings,
            )
            vacio = review.compose(
                {"result": "COVERAGE: complete\n"},
                manifest,
                sha="e" * 40,
                provider="opencode-go",
                findings=findings,
                budget=domain.StorageBudget(max_bytes=domain.STATE_BYTES_PROPOSED),
            )
            for margen in range(0, 120):
                tope = len(vacio) + margen
                presupuesto = domain.StorageBudget(
                    max_bytes=domain.STATE_BYTES_PROPOSED,
                    comment_max_bytes=tope,
                    comment_max_chars=tope,
                )
                with self.subTest(camino=camino, margen=margen):
                    out = review.compose(
                        resultado,
                        manifest,
                        sha="e" * 40,
                        provider="opencode-go",
                        findings=findings,
                        budget=presupuesto,
                    )
                    if out == sin:
                        continue  # el fijo no cabe: presupuesto rechazado, topes actuales
                    self.assertLessEqual(len(out), tope)
                    self.assertLessEqual(len(out.encode("utf-8")), tope)

    def test_fijo_inflado_rechaza_el_presupuesto_en_camino_con_hallazgos(self):
        # 200 títulos largos: las secciones (fijas) pasan el presupuesto aunque
        # el bloque de memoria quepa de sobra.
        findings = self._hallazgos_chicos(titulo="t" * 150, cuantos=200)
        manifest = {
            "mode": "full",
            "reason": "no-prev",
            "reviewed": ["a.py"],
            "excluded": [],
        }
        resultado = {"result": "COVERAGE: complete\n" + "x" * 5000}
        presupuesto = domain.StorageBudget(
            max_bytes=domain.STATE_BYTES_PROPOSED,
            comment_max_bytes=20000,
            comment_max_chars=20000,
        )
        self.assertLess(len(findings["block"]), 20000)
        out = review.compose(
            resultado,
            manifest,
            sha="e" * 40,
            provider="opencode-go",
            findings=findings,
            budget=presupuesto,
        )
        sin = review.compose(
            resultado, manifest, sha="e" * 40, provider="opencode-go", findings=findings
        )
        self.assertGreater(len(sin), 20000)
        self.assertEqual(
            out, sin, "el presupuesto no cabe: se publican los topes actuales"
        )

    def test_mensaje_sin_revisables_con_findings_vacios(self):
        findings = {"merged": [], "new_ids": [], "block": "", "model_ok": True}
        out = review.compose(
            {"result": ""},
            {"mode": "full", "reason": "no-prev", "reviewed": [], "excluded": []},
            sha="e" * 40,
            provider="opencode-go",
            findings=findings,
        )
        self.assertIn("No hay archivos revisables", out)

    def test_frontera_exacta_del_perfil_propuesto(self):
        """T05: 40000 bytes exactos se aceptan; 40001 se rechazan."""
        perfil = domain.StorageBudget(
            max_bytes=domain.STATE_BYTES_PROPOSED,
            comment_max_bytes=domain.COMMENT_MAX_BYTES,
            comment_max_chars=domain.COMMENT_MAX_CHARS,
        )
        base = self._snapshot_rico()
        base_enc = domain.encode_snapshot(base, perfil)
        self.assertIsInstance(base_enc, domain.EncodedCheckpoint)
        limite = domain.STATE_BYTES_PROPOSED
        relleno = limite - base_enc.bytes
        self.assertGreater(relleno, 0)

        clavado = domain.replace(
            base,
            findings=[
                domain.replace(
                    base.findings[0],
                    title=base.findings[0].title + "z" * relleno,
                )
            ]
            + base.findings[1:],
        )
        acepta = domain.encode_snapshot(clavado, perfil)
        self.assertIsInstance(acepta, domain.EncodedCheckpoint)
        self.assertEqual(acepta.bytes, limite)

        excedido = domain.replace(
            clavado,
            findings=[
                domain.replace(
                    clavado.findings[0],
                    title=clavado.findings[0].title + "z",
                )
            ]
            + clavado.findings[1:],
        )
        rechazado = domain.encode_snapshot(excedido, perfil)
        self.assertIsInstance(rechazado, domain.CapacityExceeded)
        self.assertEqual(rechazado.needed, limite + 1)
        self.assertIsInstance(
            domain.encode_snapshot(clavado, perfil),
            domain.EncodedCheckpoint,
            "el checkpoint confirmado en la frontera queda intacto",
        )

    def test_overflow_preserves_confirmed_state(self):
        perfil = domain.StorageBudget(
            max_bytes=domain.STATE_BYTES_PROPOSED,
            comment_max_bytes=domain.COMMENT_MAX_BYTES,
            comment_max_chars=domain.COMMENT_MAX_CHARS,
        )
        base = self._snapshot_rico()
        encoded = domain.encode_snapshot(base, perfil)
        self.assertIsInstance(encoded, domain.EncodedCheckpoint)
        desbordado = domain.replace(
            base,
            findings=base.findings
            + [
                domain.Finding(
                    id="F10",
                    title="ñ" * 400,
                    severity="Low",
                    status=domain.StatusOpen(),
                    primary_anchor=domain.AnchorLegacy(path="x.py", line=1),
                )
            ],
        )
        checado = None
        for _ in range(200):
            intento = domain.encode_snapshot(desbordado, perfil)
            if isinstance(intento, domain.CapacityExceeded):
                checado = intento
                break
            desbordado = domain.replace(
                desbordado,
                findings=desbordado.findings
                + [
                    domain.Finding(
                        id=f"F{1000 + len(desbordado.findings)}",
                        title="ñ" * 400,
                        severity="Low",
                        status=domain.StatusOpen(),
                        primary_anchor=domain.AnchorLegacy(path="x.py", line=1),
                    )
                ],
                next_id=2000 + len(desbordado.findings),
            )
        self.assertIsInstance(checado, domain.CapacityExceeded)
        self.assertGreater(checado.needed, checado.limit)
        de_vuelta = domain.read_snapshot(encoded.block)
        self.assertIsInstance(de_vuelta, domain.Valid)
        confirmado = de_vuelta.snapshot
        self.assertEqual(confirmado.revision.head_sha, "c" * 40)
        self.assertEqual(confirmado.command_cursor, 12)
        descartado = next(
            f
            for f in confirmado.findings
            if isinstance(f.status, domain.StatusDismissed)
        )
        self.assertEqual(descartado.status.command_id, 12)
        self.assertEqual(confirmado.request_count, 2)
        self.assertEqual([r.command_id for r in confirmado.receipts], [12, 11])


class ReportBoundary(unittest.TestCase):
    """Formas inválidas tipadas, resolución por hechos reales y cobertura."""

    def _snapshot_v2(self, findings):
        return domain.Snapshot(
            schema=2,
            generation=1,
            revision=domain.Revision(
                base_sha="b" * 40, head_sha="c" * 40, policy_digest="d" * 64
            ),
            next_id=max((domain.finding_number(f.id) or 0) for f in findings) + 1
            if findings
            else 1,
            completion=domain.UNKNOWN,
            findings=list(findings),
            command_cursor=0,
        )

    def _fuga(self, ruta="src/fuga.py", relacionadas=()):
        return domain.Finding(
            id="F1",
            title="Fuga de recurso",
            severity="High",
            status=domain.StatusOpen(),
            primary_anchor=domain.AnchorLegacy(path=ruta, line=3),
            related_anchors=list(relacionadas),
        )

    def test_malformed_ranges_are_typed_errors(self):
        base = {
            "title": "t",
            "severity": "High",
            "file": "a.py",
            "line": 3,
            "state": "open",
            "anchor": {
                "path": "a.py",
                "blob_sha": "a" * 40,
                "excerpt_digest": "x" * 64,
            },
        }
        casos = [
            ("rango corto", {"range": [3]}),
            ("rango no numérico", {"range": [3, "x"]}),
            ("rango booleano", {"range": [True, 3]}),
            ("rango invertido", {"range": [4, 2]}),
        ]
        for nombre, anexo in casos:
            with self.subTest(caso=nombre):
                entrada = dict(base, anchor=dict(base["anchor"], **anexo))
                salida = domain.observation_de_entrada(entrada)
                self.assertIsInstance(salida, domain.ObservationRejected)
                self.assertTrue(salida.motivo)

        legada_corta = dict(base, anchor={"path": "a.py", "line": True})
        with self.subTest(caso="línea booleana legada"):
            self.assertIsInstance(
                domain.observation_de_entrada(legada_corta), domain.ObservationRejected
            )

    def test_bloque_invalido_no_avanza_cobertura(self):
        entrada = {
            "title": "t",
            "severity": "High",
            "file": "a.py",
            "line": 3,
            "state": "open",
            "anchor": {
                "path": "a.py",
                "blob_sha": "a" * 40,
                "range": [3],
                "excerpt_digest": "x" * 64,
            },
        }
        rechazada = domain.observation_de_entrada(entrada)
        self.assertIsInstance(rechazada, domain.ObservationRejected)
        report = domain.validar_reporte(
            [rechazada], "complete", domain.RepositoryFacts()
        )
        self.assertEqual(len(report.rechazadas_forma), 1)
        transicion = domain.accept_report(
            self._snapshot_v2([]), domain.ReviewPlan(), report
        )
        self.assertIsInstance(transicion, domain.Replace)
        self.assertEqual(transicion.snapshot.completion, domain.UNKNOWN)
        self.assertEqual(transicion.snapshot.findings, [])

    def test_resolucion_por_reversion_exacta(self):
        hechos = domain.RepositoryFacts(
            revision=domain.Revision(
                base_sha="b" * 40, head_sha="c" * 40, policy_digest=""
            ),
            changed_paths=("src/otro.py",),
            reverted_paths=("src/fuga.py",),
        )
        obs = _observacion("Fuga de recurso", ruta="src/fuga.py", claim=domain.RESOLVED)
        report = domain.validar_reporte([obs], "partial", hechos)
        plan = domain.ReviewPlan(
            revision=hechos.revision, changed_paths=("src/fuga.py",)
        )
        transicion = domain.accept_report(
            self._snapshot_v2([self._fuga()]), plan, report
        )
        f1 = {f.id: f for f in transicion.snapshot.findings}["F1"]
        self.assertIsInstance(f1.status, domain.StatusResolved)
        self.assertEqual(f1.status.at_sha, "c" * 40)

    def test_resolucion_por_cambio_relacionado(self):
        fuga = self._fuga(relacionadas=[domain.AnchorLegacy(path="src/uso.py", line=7)])
        hechos = domain.RepositoryFacts(
            revision=domain.Revision(
                base_sha="b" * 40, head_sha="c" * 40, policy_digest=""
            ),
            changed_paths=("src/uso.py",),
            delta_calculado=True,
        )
        obs = _observacion("Fuga de recurso", ruta="src/fuga.py", claim=domain.RESOLVED)
        report = domain.validar_reporte([obs], "partial", hechos)
        plan = domain.ReviewPlan(
            revision=hechos.revision, changed_paths=("src/fuga.py",)
        )
        transicion = domain.accept_report(self._snapshot_v2([fuga]), plan, report)
        f1 = {f.id: f for f in transicion.snapshot.findings}["F1"]
        self.assertIsInstance(
            f1.status, domain.StatusResolved, "el cambio pertinente está en la related"
        )

    def test_ausencia_en_la_respuesta_no_resuelve(self):
        hechos = domain.RepositoryFacts(
            revision=domain.Revision(
                base_sha="b" * 40, head_sha="c" * 40, policy_digest=""
            ),
            changed_paths=("src/fuga.py",),
        )
        report = domain.validar_reporte([], "complete", hechos)
        plan = domain.ReviewPlan(
            revision=hechos.revision, changed_paths=("src/fuga.py",)
        )
        transicion = domain.accept_report(
            self._snapshot_v2([self._fuga()]), plan, report
        )
        f1 = {f.id: f for f in transicion.snapshot.findings}["F1"]
        self.assertIsInstance(f1.status, domain.StatusOpen)

    def test_delta_calculado_vacio_no_cae_al_alcance_revisado(self):
        hechos = domain.RepositoryFacts(
            revision=domain.Revision(
                base_sha="b" * 40, head_sha="c" * 40, policy_digest=""
            ),
            changed_paths=(),
            delta_calculado=True,
        )
        obs = _observacion("Fuga de recurso", ruta="src/fuga.py", claim=domain.RESOLVED)
        report = domain.validar_reporte([obs], "partial", hechos)
        plan = domain.ReviewPlan(
            revision=hechos.revision, changed_paths=("src/fuga.py",)
        )
        transicion = domain.accept_report(
            self._snapshot_v2([self._fuga()]), plan, report
        )
        f1 = {f.id: f for f in transicion.snapshot.findings}["F1"]
        self.assertIsInstance(
            f1.status,
            domain.StatusOpen,
            "delta real vacío calculado: el alcance revisado no suple pertinencia",
        )

    def test_los_hechos_reales_mandan_sobre_lo_revisado(self):
        hechos = domain.RepositoryFacts(
            revision=domain.Revision(
                base_sha="b" * 40, head_sha="c" * 40, policy_digest=""
            ),
            changed_paths=("src/otro.py",),
            delta_calculado=True,
        )
        obs = _observacion("Fuga de recurso", ruta="src/fuga.py", claim=domain.RESOLVED)
        report = domain.validar_reporte([obs], "partial", hechos)
        plan = domain.ReviewPlan(
            revision=hechos.revision, changed_paths=("src/fuga.py",)
        )
        transicion = domain.accept_report(
            self._snapshot_v2([self._fuga()]), plan, report
        )
        f1 = {f.id: f for f in transicion.snapshot.findings}["F1"]
        self.assertIsInstance(
            f1.status,
            domain.StatusOpen,
            "sin cambio real pertinente, el resuelto del modelo no resuelve",
        )
        self.assertTrue(
            any(
                isinstance(e, domain.EvidenceUnverified)
                and "sin cambio pertinente" in (e.text or "")
                for e in f1.evidence
            )
        )

        otro = domain.Finding(
            id="F2",
            title="Typo en otro",
            severity="Low",
            status=domain.StatusOpen(),
            primary_anchor=domain.AnchorLegacy(path="src/otro.py", line=1),
        )
        obs_otro = _observacion(
            "Typo en otro", ruta="src/otro.py", claim=domain.RESOLVED
        )
        report = domain.validar_reporte([obs_otro], "partial", hechos)
        transicion = domain.accept_report(self._snapshot_v2([otro]), plan, report)
        f2 = {f.id: f for f in transicion.snapshot.findings}["F2"]
        self.assertIsInstance(f2.status, domain.StatusResolved)


OBSERVACIONES_PILOTO2 = json.loads(
    (ROOT / "tests" / "fixtures" / "t16_piloto2_observaciones.json").read_text()
)


class ReporteDelPilotoSinPerdidas(unittest.TestCase):
    """T16-piloto-2: con anclas legadas en un solo archivo, accept_report tiraba
    toda observación después de la primera (6 -> 1 en el run 38017166195)."""

    REVISION = domain.Revision(
        base_sha="b" * 40, head_sha="c" * 40, policy_digest="d" * 64
    )

    def _vacio(self):
        return domain.Snapshot(
            schema=3,
            generation=1,
            revision=self.REVISION,
            next_id=1,
            completion=domain.UNKNOWN,
            findings=[],
            command_cursor=0,
        )

    # La política del coordinador en producción (identidad externa `current`).
    TITLES = domain.ReviewPolicy(finding_identity="titles")
    ANCHORS = domain.ReviewPolicy(finding_identity="anchors")

    def _aceptar(self, snapshot, run, policy=TITLES):
        return self._aceptar_entradas(snapshot, OBSERVACIONES_PILOTO2[run], policy)

    def _resumen(self, snapshot):
        return [(f.id, f.severity, f.title) for f in snapshot.findings]

    def test_las_seis_observaciones_del_primer_push_entran(self):
        estado = self._aceptar(self._vacio(), "38017166195")
        self.assertEqual(
            self._resumen(estado),
            [
                (
                    "F1",
                    "Critical",
                    "Ejecución arbitraria de código: eval sobre el contenido del archivo",
                ),
                ("F2", "High", "ultimo siempre lanza IndexError (off-by-one)"),
                (
                    "F3",
                    "Medium",
                    "mediana no es la mediana para listas de tamaño par (y falla con lista vacía)",
                ),
                ("F4", "Medium", "promedio lanza ZeroDivisionError con lista vacía"),
                ("F5", "Low", "El archivo abierto en leer_config nunca se cierra"),
                ("F6", "Low", "El módulo no tiene ninguna prueba"),
            ],
        )
        self.assertEqual(estado.next_id, 7)

    def test_el_mismo_reporte_en_el_segundo_push_conserva_los_ids(self):
        primero = self._aceptar(self._vacio(), "38017166195")
        segundo = self._aceptar(primero, "38017166195")
        self.assertEqual(self._resumen(segundo), self._resumen(primero))
        self.assertEqual(segundo.next_id, 7, "ninguna observación entra duplicada")

    def test_el_segundo_push_real_no_pierde_observaciones(self):
        for policy in (self.TITLES, self.ANCHORS):
            with self.subTest(identidad=policy.finding_identity):
                estado = self._aceptar(self._vacio(), "38017336748", policy)
                self.assertEqual(
                    [f.id for f in estado.findings],
                    ["F1", "F2", "F3", "F4", "F5", "F6"],
                )

    def test_el_segundo_push_sin_ids_sobre_el_primero_las_registra_todas(self):
        """El reporte real del #85 se emitió sin memoria (sin ids y con títulos
        reescritos). Con `anchors`, sin ancla verificada, cada título reescrito
        entra como posible duplicado; nunca se tira."""
        primero = self._aceptar(self._vacio(), "38017166195", self.ANCHORS)
        segundo = self._aceptar(primero, "38017336748", self.ANCHORS)
        nuevos = segundo.findings[len(primero.findings) :]
        self.assertEqual(
            [f.id for f in nuevos], ["F7", "F8", "F9", "F10", "F11", "F12"]
        )
        self.assertEqual(
            {f.cause_hint for f in nuevos},
            {"posible duplicado de F1, F2, F3, F4, F5, F6"},
        )
        self.assertEqual(self._resumen(segundo)[:6], self._resumen(primero))

    # Con prev_findings.md el modelo repite el id de cada previo aunque reescriba
    # el título (prompt: "repítelos con su mismo id"): las 5 viejas del segundo
    # push real con el id del primero, por línea; dividir_todo es nueva.
    IDS_DEL_MODELO = {15: "F1", 9: "F2", 20: "F3", 5: "F4", 12: "F5"}

    def _segundo_push_con_ids(self):
        return [
            {**e, "id": self.IDS_DEL_MODELO.get(e["line"], "F-new")}
            for e in OBSERVACIONES_PILOTO2["38017336748"]
        ]

    def _aceptar_entradas(self, snapshot, entradas, policy=TITLES):
        observaciones = [domain.observation_de_entrada(e) for e in entradas]
        plan = domain.ReviewPlan(
            revision=self.REVISION,
            changed_paths=("piloto/caso_t16.py",),
            policy=policy,
        )
        report = domain.validar_reporte(
            observaciones, domain.UNKNOWN, domain.RepositoryFacts()
        )
        return domain.accept_report(snapshot, plan, report).snapshot

    def test_el_id_que_repite_el_modelo_conserva_la_identidad(self):
        primero = self._aceptar(self._vacio(), "38017166195")
        segundo = self._aceptar_entradas(primero, self._segundo_push_con_ids())
        self.assertEqual(
            self._resumen(segundo),
            [
                (
                    "F1",
                    "Critical",
                    "eval sobre el contenido de un archivo (ejecución arbitraria de código)",
                ),
                ("F2", "High", "ultimo siempre lanza IndexError"),
                (
                    "F3",
                    "Medium",
                    "mediana devuelve el elemento central superior en listas de longitud par",
                ),
                ("F4", "Low", "promedio lanza ZeroDivisionError con entrada vacía"),
                ("F5", "Low", "leer_config no cierra el archivo abierto"),
                ("F6", "Low", "El módulo no tiene ninguna prueba"),
                ("F7", "High", "Condición invertida en dividir_todo: divide por cero"),
            ],
        )
        self.assertEqual(
            {f.cause_hint for f in segundo.findings}, {None}, "sin duplicados"
        )

    def test_el_id_de_un_descartado_no_lo_revive(self):
        primero = self._aceptar(self._vacio(), "38017166195")
        descartado = domain.aplicar_descartes(primero, ["F1"], comment_id=1)
        segundo = self._aceptar_entradas(descartado, self._segundo_push_con_ids())
        por_id = {f.id: f for f in segundo.findings}
        self.assertIsInstance(por_id["F1"].status, domain.StatusDismissed)
        self.assertEqual(
            [f.id for f in segundo.findings],
            ["F1", "F2", "F3", "F4", "F5", "F6", "F7"],
            "el eval descartado no vuelve con otro id",
        )

    def test_un_id_de_otro_archivo_no_se_adopta(self):
        primero = self._aceptar(self._vacio(), "38017166195")
        entrada = {
            **OBSERVACIONES_PILOTO2["38017336748"][2],
            "id": "F2",
            "file": "otro.py",
        }
        segundo = self._aceptar_entradas(primero, [entrada])
        self.assertEqual(
            [(f.id, f.title) for f in segundo.findings[6:]],
            [("F7", "Condición invertida en dividir_todo: divide por cero")],
        )

    def test_el_titulo_igual_prefiere_el_vivo_sobre_el_descartado(self):
        """F1 de ai-review en af981c7: con un descartado y un vivo del mismo
        título en el archivo, la observación iba al descartado y se perdía."""
        ancla = domain.AnchorLegacy(path="piloto/caso_t16.py", line=9)
        previos = [
            domain.Finding(
                id="F1",
                title="ultimo siempre lanza IndexError",
                severity="High",
                status=domain.StatusDismissed(command_id=1),
                primary_anchor=ancla,
            ),
            domain.Finding(
                id="F2",
                title="ultimo siempre lanza IndexError",
                severity="High",
                status=domain.StatusOpen(),
                primary_anchor=ancla,
            ),
        ]
        estado = replace(self._vacio(), findings=previos, next_id=3)
        entrada = {
            "id": None,
            "file": "piloto/caso_t16.py",
            "line": 9,
            "severity": "Critical",
            "title": "ultimo siempre lanza IndexError",
            "state": "open",
        }
        segundo = self._aceptar_entradas(estado, [entrada])
        self.assertEqual(
            [(f.id, f.severity, type(f.status).__name__) for f in segundo.findings],
            [("F1", "High", "StatusDismissed"), ("F2", "Critical", "StatusOpen")],
        )

    def test_la_identidad_externa_current_tambien_honra_los_ids(self):
        """F2 de ai-review en af981c7: normalize_policy usa el vocabulario
        externo; `current` no debe apagar la identidad de la ruta directa."""
        primero = self._aceptar(self._vacio(), "38017166195")
        segundo = self._aceptar_entradas(
            primero,
            self._segundo_push_con_ids(),
            policy=domain.ReviewPolicy(finding_identity="current"),
        )
        self.assertEqual(
            [f.id for f in segundo.findings],
            ["F1", "F2", "F3", "F4", "F5", "F6", "F7"],
        )

    def test_con_anchors_el_id_del_modelo_no_decide(self):
        primero = self._aceptar(self._vacio(), "38017166195")
        segundo = self._aceptar_entradas(
            primero,
            self._segundo_push_con_ids(),
            policy=self.ANCHORS,
        )
        self.assertEqual(len(segundo.findings), 12, "el programa asigna los ids")

    def test_el_mismo_reporte_con_ids_equivale_a_la_ruta_directa(self):
        primero = self._aceptar(self._vacio(), "38017166195")
        coordinada = self._aceptar_entradas(primero, self._segundo_push_con_ids())
        previo_directo, _ = review.merge_findings(
            None,
            {"findings": OBSERVACIONES_PILOTO2["38017166195"]},
            changed_files=["piloto/caso_t16.py"],
            reverted_files=[],
            dismiss_ids=[],
            dismiss_all=False,
        )
        directa, _ = review.merge_findings(
            previo_directo,
            {"findings": self._segundo_push_con_ids()},
            changed_files=["piloto/caso_t16.py"],
            reverted_files=[],
            dismiss_ids=[],
            dismiss_all=False,
        )
        self.assertEqual(
            [(f["id"], f["severity"], f["title"]) for f in directa["findings"]],
            self._resumen(coordinada),
        )

    def test_una_coincidencia_sin_previo_entra_como_nueva(self):
        original = domain.match_finding

        def coincide_con_un_id_ajeno(previos, obs, facts):
            if obs.title.startswith("ultimo"):
                return domain.MatchExisting(id="F99")
            return original(previos, obs, facts)

        with mock.patch.object(domain, "match_finding", coincide_con_un_id_ajeno):
            estado = self._aceptar(self._vacio(), "38017166195", self.ANCHORS)
        self.assertEqual(len(estado.findings), 6, "nada se descarta en silencio")

    def test_la_ruta_directa_y_la_coordinada_dejan_los_mismos_hallazgos(self):
        for run in ("38017166195", "38017336748"):
            with self.subTest(run=run):
                coordinada = self._aceptar(self._vacio(), run)
                directa, _ = review.merge_findings(
                    None,
                    {"findings": OBSERVACIONES_PILOTO2[run]},
                    changed_files=["piloto/caso_t16.py"],
                    reverted_files=[],
                    dismiss_ids=[],
                    dismiss_all=False,
                )
                self.assertEqual(
                    [(f["id"], f["severity"], f["title"]) for f in directa["findings"]],
                    self._resumen(coordinada),
                )
