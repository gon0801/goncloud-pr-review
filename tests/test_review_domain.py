import json
import sys
import tempfile
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


class PublicacionConMemoriaConservada(unittest.TestCase):
    def test_b4_publicar_con_memoria_v2_conserva_el_comentario_y_avisa(self):
        import argparse
        import os
        from unittest import mock

        snapshot = domain.Snapshot(
            schema=2,
            generation=2,
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
                os.environ.pop("GITHUB_STEP_SUMMARY", None)
                with (
                    mock.patch.object(
                        review, "fetch_all_comments", return_value=[sticky]
                    ),
                    mock.patch.object(review, "sh"),
                ):
                    review.cmd_publish(argparse.Namespace(work=str(work)))
            body = json.loads((work / "comment.json").read_text())["body"]
        original_sha = f"{review.SHA_PREFIX}{'a' * 40} -->"
        self.assertIn(original_sha, body, "el SHA revisado no avanza")
        self.assertNotIn(f"{review.SHA_PREFIX}{'c' * 40}", body)
        self.assertNotIn(f"{'c' * 40}:complete", body, "la cobertura no se confirma")
        self.assertNotIn("bug nuevo z", body, "lo nuevo no se publica encima")
        self.assertNotIn("bug x</code>", body)
        self.assertIn("conservada", body, "aviso visible para el operador")
        self.assertIn(review.CAUTION_MARK, body)
        self.assertIn("Texto previo que debe permanecer.", body)


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
        import hashlib

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
        import hashlib

        digest = hashlib.sha256(self.BLOB[1].rstrip("\n").encode("utf-8")).hexdigest()
        report = domain.validar_reporte(
            [self.observacion_con_cita(digest)], domain.UNKNOWN, self.hechos()
        )
        tipos = [type(e) for e in report.observations[0].evidence]
        self.assertIn(domain.EvidenceUnverified, tipos)

    def test_cita_existente_con_interpretacion_falsa_no_marca_reproducido(self):
        import hashlib

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
        # r4: la observación trae ancla verificada sin candidato por ancla ->
        # entra como hallazgo NUEVO (no adopta al legado por título+ruta).
        hallazgos = transicion.snapshot.findings
        f_nuevo = hallazgos[-1]
        self.assertEqual(f_nuevo.id, "F2")
        self.assertIsInstance(
            f_nuevo.status, domain.StatusOpen, "la cita validada no marca reproducido"
        )
        self.assertTrue(
            any(isinstance(e, domain.EvidenceSource) for e in f_nuevo.evidence),
            "la ubicación verificada queda como evidencia",
        )
        self.assertTrue(
            any(isinstance(e, domain.EvidenceUnverified) for e in f_nuevo.evidence),
            "la evaluación del modelo queda etiquetada aparte",
        )
        f1 = hallazgos[0]
        self.assertEqual(f1.id, "F1")
        self.assertIsInstance(f1.status, domain.StatusOpen, "el legado no se toca")


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
        import hashlib

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
        import hashlib

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
        import hashlib

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
