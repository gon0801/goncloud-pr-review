import json
import sys
import unittest
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
        command_cursor=11,
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
        load = domain.read_snapshot(
            PRE + json.dumps({"schema": 3, "findings": [], "next_id": 1}) + SUF
        )
        self.assertIsInstance(load, domain.Future)
        self.assertEqual(load.version, 3)
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

    def test_b1_sticky_v2_se_conserva_sin_perdida_en_publicacion(self):
        snapshot, sticky_body = self.memoria_v2_en_sticky()
        sticky = {"id": 9, "body": sticky_body}
        result = {"result": legado([entrada("F-new", file="c.py")])}
        manifest = {"mode": "full", "reason": "no-prev", "reviewed": ["c.py"]}
        out = review.build_findings(
            result, manifest, sticky, repo="x/y", pr=1, login="bot", comments=[]
        )
        self.assertEqual(out["block"], sticky_body, "el bloque v2 sale intacto")
        self.assertTrue(out.get("keep"))
        de_vuelta = domain.read_snapshot(out["block"])
        self.assertIsInstance(de_vuelta, domain.Valid)
        self.assertEqual([f.id for f in de_vuelta.snapshot.findings], ["F1", "F2"])
        self.assertEqual(
            de_vuelta.snapshot.findings[0].status,
            domain.StatusDismissed(command_id=7),
            "un descarte confirmado no reaparece",
        )

    def test_b1_sticky_de_version_futura_tambien_se_conserva(self):
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
