"""Caso del PR #20 de goncloud-hermes: capacidad del sticky (PRR-capacidad r1).

El sticky real de ese PR (schema 3, 20 hallazgos con 16 resueltos, bloque de
7498 bytes) más una ronda más de hallazgos desbordaba el tope de 8000 bytes:
el publicador del coordinador respondía Unconfirmed ("escritura incierta") y
salía 1 sin ningún aviso visible en el comentario. Aquí vive el repro con el
sticky real (solo lectura de GitHub) y las pruebas de capacidad del perfil
ampliado.
"""

import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import review  # noqa: E402
import review_domain as domain  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
MERGE_PY = Path("/Users/dn/dev/wt/agent-work-host/scripts/agent-work/merge.py")


def sticky_pr20():
    return {
        "id": 77,
        "user": "github-actions[bot]",
        "body": (FIXTURES / "pr20-sticky-hermes.md").read_text(),
    }


def ronda_extra():
    return json.loads((FIXTURES / "pr20-ronda-extra.json").read_text())


class _ComentariosFalsos:
    def __init__(self, comentarios):
        self.comentarios = list(comentarios)
        self.patches = []
        self.creaciones = []

    def leer(self):
        return [dict(c) for c in self.comentarios]

    def parchar(self, cid, body):
        self.patches.append((cid, body))
        for c in self.comentarios:
            if c["id"] == cid:
                c["body"] = body

    def crear(self, body):
        self.creaciones.append(body)
        self.comentarios.append({"id": 100 + len(self.creaciones), "body": body})


def snapshot_compacto():
    """Abiertos con todo, cerrados que sobran: evidencia, causas, relacionadas."""
    ancla_locada = domain.AnchorLocated(
        path="src/a.py",
        blob_sha="a" * 40,
        range=(10, 20),
        excerpt_digest="e" * 64,
        symbol_hint="clase_pool",
    )
    return domain.Snapshot(
        schema=3,
        generation=3,
        revision=domain.Revision(
            base_sha="b" * 40, head_sha="c" * 40, policy_digest="d" * 64
        ),
        next_id=9,
        completion=domain.PARTIAL,
        findings=[
            domain.Finding(
                id="F1",
                title="Abierto con evidencia",
                severity="High",
                status=domain.StatusOpen(),
                primary_anchor=domain.AnchorLegacy(path="src/vivo.py", line=7),
                related_anchors=[domain.AnchorLegacy(path="src/otro.py", line=None)],
                cause_hint="doble cierre",
                evidence=[domain.EvidenceUnverified(text="cita del modelo")],
            ),
            domain.Finding(
                id="F2",
                title="Resuelto con ancla localizada",
                severity="Medium",
                status=domain.StatusResolved(at_sha="c" * 40),
                primary_anchor=ancla_locada,
                related_anchors=[domain.AnchorLegacy(path="src/viejo.py", line=2)],
                cause_hint="ya corregido",
                evidence=[
                    domain.EvidenceSource(anchor=ancla_locada),
                    domain.EvidenceUnverified(text="evaluación del modelo"),
                ],
            ),
            domain.Finding(
                id="F3",
                title="Resuelto por reversión",
                severity="Low",
                status=domain.StatusResolved(at_sha="9" * 40),
                primary_anchor=domain.AnchorLegacy(path="src/b.py", line=3),
                evidence=[domain.EvidenceUnverified(text="histórico")],
            ),
            domain.Finding(
                id="F4",
                title="Descartado por comando",
                severity="High",
                status=domain.StatusDismissed(command_id=31),
                primary_anchor=domain.AnchorLegacy(path="src/c.py", line=4),
            ),
            domain.Finding(
                id="F5",
                title="Resuelto en t16_piloto",
                severity="Critical",
                status=domain.StatusResolved(at_sha=None),
                primary_anchor=domain.AnchorLegacy(path="src/d.py", line=None),
            ),
        ],
        command_cursor=31,
        pending_requests=[
            domain.WorkRequest(id=2, kind="explain", finding_id="F2", legacy_id="req-1")
        ],
        request_count=5,
        receipts=(domain.Receipt(command_id=31, effect="descartados por comando: F4"),),
    )


def presupuesto_que_obliga_a_compactar():
    """Un tope entre el bloque íntegro y el compactado: obliga al segundo
    intento sin ser un número mágico."""
    integro = domain.encode_snapshot(snapshot_compacto())
    assert isinstance(integro, domain.EncodedCheckpoint)
    compacto = domain.encode_snapshot(domain.compactar_cerrados(snapshot_compacto()))
    assert isinstance(compacto, domain.EncodedCheckpoint)
    assert len(compacto.block) < len(integro.block)
    return domain.StorageBudget(
        max_bytes=(len(integro.block) + len(compacto.block)) // 2
    )


def decision_de_ronda(sticky_body, ronda):
    """El coordinador acepta la ronda del worker sobre la memoria del sticky
    con la misma aceptación del dominio que usa cmd_reconcile."""
    previo = domain.read_snapshot(sticky_body).snapshot
    observaciones = [
        domain.observation_de_entrada(e) for e in ronda["model_block"]["findings"]
    ]
    revision = domain.Revision(
        base_sha=ronda["base_sha"],
        head_sha=ronda["head_sha"],
        policy_digest=previo.revision.policy_digest,
    )
    facts = domain.RepositoryFacts(
        revision=revision,
        changed_paths=tuple(ronda["changed_paths"]),
        delta_calculado=True,
    )
    plan = domain.ReviewPlan(
        revision=revision,
        changed_paths=tuple(ronda["changed_paths"]),
        policy=domain.ReviewPolicy(),
    )
    report = domain.validar_reporte(observaciones, domain.COMPLETE_CLAIM, facts)
    decision = domain.accept_report(previo, plan, report)
    assert isinstance(decision, domain.Replace)
    return domain.Commit(snapshot=decision.snapshot, work_after_commit=())


class ElCasoDelPr20Publica(unittest.TestCase):
    """Hoy: CapacityExceeded -> Unconfirmed y exit 1 sin aviso. Con el perfil
    ampliado activo, la misma ronda publica."""

    def test_ronda_extra_publica_en_lugar_de_desbordar(self):
        sticky = sticky_pr20()
        decision = decision_de_ronda(sticky["body"], ronda_extra())
        previos = {f.id for f in domain.read_snapshot(sticky["body"]).snapshot.findings}
        visible = review.revision_visible(
            dict(ronda_extra(), review_provider="opencode-go", model_ok=True),
            decision.snapshot,
            previos,
            ronda_extra()["head_sha"],
        )
        falso = _ComentariosFalsos([sticky])
        recibo = review.publish_checkpoint(decision, sticky, falso, visible=visible)
        self.assertIsInstance(recibo, review.PublishReceipt)
        self.assertEqual(falso.creaciones, [])
        cuerpo = falso.leer()[0]["body"]
        carga = domain.read_snapshot(cuerpo)
        self.assertIsInstance(carga, domain.Valid)
        self.assertEqual([f.id for f in carga.snapshot.findings][-1], "F24")
        self.assertLessEqual(
            len(carga.block.encode("utf-8")), domain.StorageBudget().max_bytes
        )
        self.assertEqual(review.reviewed_completion(cuerpo), "complete")


class SesentaAbiertosNuncaSilenciosos(unittest.TestCase):
    """60 hallazgos abiertos con evidencia: caben o degradan con aviso visible
    en el sticky; nunca un exit silencioso ni un sticky nuevo."""

    def _hallazgos(self, con_evidencia):
        abiertos = []
        for i in range(1, 61):
            ancla = domain.AnchorLocated(
                path=f"src/modulo_{i}.py",
                blob_sha="a" * 40,
                range=(1, 3),
                excerpt_digest="b" * 64,
            )
            relacionados = (
                [
                    domain.AnchorLocated(
                        path=f"src/otro_{i}.py",
                        blob_sha="a" * 40,
                        range=(5, 6),
                        excerpt_digest="c" * 64,
                    )
                ]
                if con_evidencia
                else []
            )
            evidencia = [domain.EvidenceSource(anchor=ancla)] if con_evidencia else []
            abiertos.append(
                domain.Finding(
                    id=f"F{i}",
                    title=f"Fuga de recurso {i} en el cierre del pool de conexiones del store",
                    severity="Medium",
                    status=domain.StatusOpen(),
                    primary_anchor=ancla,
                    related_anchors=relacionados,
                    cause_hint="doble registro del cleanup" if con_evidencia else None,
                    evidence=evidencia,
                )
            )
        return domain.Snapshot(
            schema=3,
            generation=9,
            revision=domain.Revision(
                base_sha="b" * 40, head_sha="c" * 40, policy_digest="d" * 64
            ),
            next_id=61,
            completion=domain.PARTIAL,
            findings=abiertos,
            command_cursor=4,
        )

    def test_con_evidencia_degradan_con_aviso_visible(self):
        snapshot = self._hallazgos(con_evidencia=True)
        self.assertIsInstance(
            domain.encode_snapshot(snapshot, domain.StorageBudget()),
            domain.CapacityExceeded,
            "el caso debe desbordar de verdad",
        )
        sticky = sticky_pr20()
        falso = _ComentariosFalsos([sticky])
        resultado = review.publish_checkpoint(
            domain.Commit(snapshot=snapshot), sticky, falso
        )
        self.assertIsInstance(resultado, review.Unconfirmed)
        self.assertEqual(falso.creaciones, [], "jamás crea un sticky sin bloque")
        self.assertEqual(len(falso.patches), 1, "el aviso va al sticky existente")
        cuerpo = falso.leer()[0]["body"]
        self.assertIn(review.CAUTION_MARK, cuerpo, "aviso visible")
        self.assertIn("desborde", cuerpo)
        self.assertIsNotNone(review.reviewed_completion(cuerpo))
        self.assertIsInstance(domain.read_snapshot(cuerpo), domain.Valid)

    def test_sin_evidencia_caben(self):
        snapshot = self._hallazgos(con_evidencia=False)
        encoded = domain.encode_snapshot(snapshot, domain.StorageBudget())
        self.assertIsInstance(encoded, domain.EncodedCheckpoint)
        sticky = sticky_pr20()
        falso = _ComentariosFalsos([sticky])
        recibo = review.publish_checkpoint(
            domain.Commit(snapshot=snapshot), sticky, falso
        )
        self.assertIsInstance(recibo, review.PublishReceipt)


class RoundtripDelEstadoCompacto(unittest.TestCase):
    """La forma compacta conserva identidad: IDs, severidad, títulos, at_sha,
    command_id de descartes, next_id, cursor, recibos y solicitudes. Punto
    fijo: re-encodear lo leído reproduce el mismo bloque."""

    def test_compacta_cerrados_y_conserva_lo_esencial(self):
        presupuesto = presupuesto_que_obliga_a_compactar()
        encoded = domain.encode_snapshot(snapshot_compacto(), presupuesto)
        self.assertIsInstance(encoded, domain.EncodedCheckpoint)
        self.assertLessEqual(encoded.bytes, presupuesto.max_bytes)
        carga = domain.read_snapshot(encoded.block)
        self.assertIsInstance(carga, domain.Valid)
        s = carga.snapshot
        self.assertEqual([f.id for f in s.findings], ["F1", "F2", "F3", "F4", "F5"])
        abierta = s.findings[0]
        self.assertEqual(len(abierta.evidence), 1)
        self.assertEqual(abierta.cause_hint, "doble cierre")
        self.assertEqual([a.path for a in abierta.related_anchors], ["src/otro.py"])
        resuelta = s.findings[1]
        self.assertEqual(resuelta.severity, "Medium")
        self.assertEqual(resuelta.status.at_sha, "c" * 40)
        self.assertEqual(resuelta.title, "Resuelto con ancla localizada")
        self.assertEqual(resuelta.primary_anchor.path, "src/a.py")
        self.assertEqual(resuelta.primary_anchor.range, (10, 20))
        self.assertEqual(resuelta.evidence, ())
        self.assertEqual(resuelta.related_anchors, ())
        self.assertIsNone(resuelta.cause_hint)
        self.assertEqual(s.findings[2].status.at_sha, "9" * 40)
        descartada = s.findings[3]
        self.assertEqual(descartada.status.command_id, 31)
        self.assertEqual(s.findings[4].status.at_sha, None)
        self.assertEqual(s.next_id, 9)
        self.assertEqual(s.command_cursor, 31)
        self.assertEqual(s.request_count, 5)
        self.assertEqual([r.command_id for r in s.receipts], [31])
        self.assertEqual(
            [(r.id, r.kind, r.finding_id) for r in s.pending_requests],
            [(2, "explain", "F2")],
        )
        reencode = domain.encode_snapshot(carga.snapshot, presupuesto)
        self.assertEqual(reencode.block, encoded.block, "punto fijo")

    def test_sin_presion_nadie_se_compacta(self):
        encoded = domain.encode_snapshot(snapshot_compacto())
        self.assertIsInstance(encoded, domain.EncodedCheckpoint)
        carga = domain.read_snapshot(encoded.block)
        resuelta = carga.snapshot.findings[1]
        self.assertEqual(len(resuelta.evidence), 2)
        self.assertEqual(resuelta.cause_hint, "ya corregido")


class StickiesViejosSeLeenSinPerdida(unittest.TestCase):
    """Los stickies escritos con el schema actual (y los legado y v2) siguen
    leéndose completos después de la activación."""

    def test_el_sticky_del_pr20_roundtrip_intacto(self):
        antes = domain.read_snapshot(sticky_pr20()["body"]).snapshot
        encoded = domain.encode_snapshot(antes)
        self.assertIsInstance(encoded, domain.EncodedCheckpoint)
        despues = domain.read_snapshot(encoded.block).snapshot
        self.assertEqual(domain._findings_json(antes), domain._findings_json(despues))
        self.assertEqual(antes.generation, despues.generation)
        self.assertEqual(antes.next_id, despues.next_id)
        self.assertEqual(antes.completion, despues.completion)
        self.assertEqual(antes.command_cursor, despues.command_cursor)
        self.assertEqual(antes.request_count, despues.request_count)
        self.assertEqual(
            [(r.id, r.kind, r.legacy_id) for r in antes.pending_requests],
            [(r.id, r.kind, r.legacy_id) for r in despues.pending_requests],
        )

    def test_legado_y_v2_siguen_leendose(self):
        legacy = {
            "findings": [
                {
                    "id": "F1",
                    "file": "a.py",
                    "line": 1,
                    "severity": "High",
                    "title": "Título",
                    "state": "resolved",
                }
            ],
            "next": 2,
            "seen": 4,
        }
        bloque = review.serialize_findings(legacy)
        carga = domain.read_snapshot(f"{review.MARKER}\n{bloque}")
        self.assertIsInstance(carga, domain.Legacy)
        self.assertEqual(carga.raw["findings"][0]["state"], "resolved")
        self.assertEqual(carga.raw["seen"], 4)
        v2 = domain.Snapshot(
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
                    title="Con evidencia",
                    severity="Low",
                    status=domain.StatusOpen(),
                    primary_anchor=domain.AnchorLegacy(path="a.py", line=1),
                    evidence=[domain.EvidenceUnverified(text="cita")],
                )
            ],
            command_cursor=0,
        )
        encoded = domain.encode_snapshot(v2)
        self.assertIsInstance(encoded, str)
        carga2 = domain.read_snapshot(f"{review.MARKER}\n{encoded}")
        self.assertIsInstance(carga2, domain.Valid)
        self.assertEqual(len(carga2.snapshot.findings[0].evidence), 1)


@unittest.skipUnless(MERGE_PY.exists(), "el repo G no está clonado en esta máquina")
class ElParserDeGleeElStickyNuevo(unittest.TestCase):
    """merge.py de agent-work decide el merge con el parser real del sticky."""

    def _merge(self):
        spec = importlib.util.spec_from_file_location("merge_de_g", MERGE_PY)
        modulo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(modulo)
        return modulo

    def _cuerpo(self, snapshot, marcador="complete"):
        head = snapshot.revision.head_sha
        bloque = domain.encode_snapshot(snapshot).block
        return "\n".join(
            [
                review.MARKER,
                f"{review.SHA_PREFIX}{head} -->",
                f"{review.COMPLETION_PREFIX}{head}:{marcador} -->",
                bloque,
                "### Revisión automática",
            ]
        )

    def test_lee_completion_y_findings_del_sticky_post_ronda(self):
        sticky = sticky_pr20()
        decision = decision_de_ronda(sticky["body"], ronda_extra())
        marca = self._merge().parsear_bot(self._cuerpo(decision.snapshot, "complete"))
        self.assertTrue(marca.bloque_valido)
        self.assertEqual(marca.sha, decision.snapshot.revision.head_sha)
        self.assertEqual(marca.estado, "complete")
        por_id = {h.fid: h for h in marca.hallazgos}
        self.assertEqual(len(por_id), 24)
        self.assertEqual(por_id["F21"].estado, "open")
        self.assertEqual(por_id["F17"].estado, "open")
        self.assertEqual(por_id["F1"].estado, "resolved")
        frenan = {
            h.fid
            for h in marca.hallazgos
            if h.estado == "open" and h.severidad in ("Critical", "High", "Medium")
        }
        self.assertIn("F21", frenan, "el merge de G sigue frenando por abiertos")

    def test_lee_el_bloque_compacto(self):
        encoded = domain.encode_snapshot(
            snapshot_compacto(), presupuesto_que_obliga_a_compactar()
        )
        self.assertIsInstance(encoded, domain.EncodedCheckpoint)
        cuerpo = "\n".join(
            [
                review.MARKER,
                f"{review.SHA_PREFIX}{'c' * 40} -->",
                f"{review.COMPLETION_PREFIX}{'c' * 40}:partial -->",
                encoded.block,
                "### Revisión automática",
            ]
        )
        marca = self._merge().parsear_bot(cuerpo)
        self.assertTrue(marca.bloque_valido)
        self.assertEqual(len(marca.hallazgos), 5)


class ElCaminoLegadoTambienPublica(unittest.TestCase):
    """El writer legado del publish clásico con el perfil ampliado: un estado
    legado que ayer no cabía en 8000 hoy publica."""

    MANIFEST = {
        "mode": "full",
        "reason": "no-prev",
        "reviewed": ["nuevo.py"],
        "excluded": [],
    }

    def _sticky_legado(self):
        previo = {
            "findings": [
                {
                    "id": f"F{i}",
                    "file": "a.py",
                    "line": 1,
                    "severity": "Low",
                    "title": f"Retardo en la cola de cobro de la pasarela, caso {i} con reintentos",
                    "state": "resolved" if i % 2 else "open",
                }
                for i in range(1, 53)
            ],
            "next": 53,
            "seen": 3,
        }
        bloque = review.serialize_findings(previo)
        self.assertIsInstance(bloque, str)
        self.assertLessEqual(len(bloque.encode("utf-8")), 8000, "cabe hoy")
        self.assertGreater(len(bloque.encode("utf-8")), 7500, "cerca del tope viejo")
        return review.MARKER + "\n" + bloque

    def _resultado(self):
        nuevos = [
            {
                "file": "nuevo.py",
                "line": i,
                "severity": "Medium",
                "title": f"bug nuevo {i} del flujo de cobro",
                "state": "open",
            }
            for i in range(1, 11)
        ]
        return {
            "result": review.FINDINGS_PREFIX
            + json.dumps({"findings": nuevos, "next": 46}, separators=(",", ":"))
            + review.FINDINGS_SUFFIX
        }

    def test_merged_sobre_8000_publica(self):
        out = review.build_findings(
            self._resultado(),
            self.MANIFEST,
            {"id": 9, "user": "github-actions[bot]", "body": self._sticky_legado()},
            "x/y",
            1,
            "bot",
            [],
        )
        self.assertNotIn("keep", out, "el perfil ampliado evita el Keep")
        bloque = out["block"]
        self.assertGreater(len(bloque.encode("utf-8")), 8000, "ayer no cabía")
        self.assertLessEqual(
            len(bloque.encode("utf-8")), domain.StorageBudget().max_bytes
        )
        carga = domain.read_snapshot(f"{review.MARKER}\n{bloque}")
        self.assertIsInstance(carga, domain.Legacy)
        self.assertEqual(len(carga.raw["findings"]), 55)
        self.assertEqual(carga.raw["next"], 56)


if __name__ == "__main__":
    unittest.main()
