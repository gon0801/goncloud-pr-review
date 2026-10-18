"""Caso del PR #20 de goncloud-hermes: capacidad del sticky (PRR-capacidad r1).

El sticky real de ese PR (schema 3, 20 hallazgos con 16 resueltos, bloque de
7498 bytes) más una ronda más de hallazgos desbordaba el tope de 8000 bytes:
el publicador del coordinador respondía Unconfirmed ("escritura incierta") y
salía 1 sin ningún aviso visible en el comentario. Aquí vive el repro con el
sticky real (solo lectura de GitHub) y las pruebas de capacidad del perfil
ampliado.
"""

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import review  # noqa: E402
import review_domain as domain  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


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
        self.assertEqual(list(resuelta.evidence), [])
        self.assertEqual(
            [a.path for a in resuelta.related_anchors],
            ["src/viejo.py"],
            "viajan para reabrir",
        )
        self.assertEqual(
            resuelta.cause_hint,
            "ya corregido",
            "la causa viaja: match_finding desempata contra resueltos",
        )
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

    def test_un_resuelto_compacto_se_reabre_por_su_ancla_relacionada(self):
        resuelto = domain.Finding(
            id="F1",
            title="Fuga en el cierre del pool",
            severity="High",
            status=domain.StatusResolved(at_sha="c" * 40),
            primary_anchor=domain.AnchorLegacy(path="src/principal.py", line=3),
            related_anchors=[domain.AnchorLegacy(path="src/pruebas.py", line=None)],
            evidence=[domain.EvidenceUnverified(text="cita del modelo")],
        )
        gorda = domain.Finding(
            id="F2",
            title="Abierta con evidencia pesada",
            severity="Medium",
            status=domain.StatusOpen(),
            primary_anchor=domain.AnchorLegacy(path="src/vivo.py", line=7),
            evidence=[domain.EvidenceUnverified(text="x" * 400)],
        )
        snapshot = domain.Snapshot(
            schema=3,
            generation=2,
            revision=domain.Revision(
                base_sha="b" * 40, head_sha="c" * 40, policy_digest="d" * 64
            ),
            next_id=3,
            completion=domain.PARTIAL,
            findings=[resuelto, gorda],
            command_cursor=0,
        )
        integro = domain.encode_snapshot(snapshot)
        compacto = domain.encode_snapshot(domain.compactar_cerrados(snapshot))
        self.assertLess(len(compacto.block), len(integro.block))
        presupuesto = domain.StorageBudget(
            max_bytes=(len(integro.block) + len(compacto.block)) // 2
        )
        encoded = domain.encode_snapshot(snapshot, presupuesto)
        self.assertIsInstance(encoded, domain.EncodedCheckpoint)
        recargado = domain.read_snapshot(encoded.block).snapshot
        self.assertEqual(
            list(recargado.findings[0].related_anchors),
            list(resuelto.related_anchors),
            "las relacionadas viajan: _cambio_pertinente las lee",
        )
        observacion = domain.observation_de_entrada(
            {
                "file": "src/principal.py",
                "line": 3,
                "title": "Fuga en el cierre del pool",
                "severity": "High",
                "state": "open",
            }
        )
        revision = domain.Revision(
            base_sha="b" * 40, head_sha="9" * 40, policy_digest="d" * 64
        )
        facts = domain.RepositoryFacts(
            revision=revision,
            changed_paths=("src/pruebas.py",),
            delta_calculado=True,
        )
        plan = domain.ReviewPlan(
            revision=revision,
            changed_paths=("src/pruebas.py",),
            policy=domain.ReviewPolicy(),
        )
        report = domain.validar_reporte([observacion], domain.PARTIAL, facts)
        decision = domain.accept_report(recargado, plan, report)
        self.assertIsInstance(decision, domain.Replace)
        reabierto = decision.snapshot.findings[0]
        self.assertEqual(reabierto.id, "F1")
        self.assertIsInstance(
            reabierto.status,
            domain.StatusOpen,
            "la regresión en el archivo relacionado reabre la resolución",
        )

    def test_un_resuelto_compacto_se_reabre_por_cause_hint(self):
        resuelto_a = domain.Finding(
            id="F1",
            title="Fuga en el cierre del pool",
            severity="High",
            status=domain.StatusResolved(at_sha="c" * 40),
            primary_anchor=domain.AnchorLegacy(path="src/principal.py", line=3),
            cause_hint="doble registro del cleanup",
            evidence=[domain.EvidenceUnverified(text="cita del modelo")],
        )
        resuelto_b = domain.Finding(
            id="F2",
            title="Carrera en el pool gemelo",
            severity="Medium",
            status=domain.StatusResolved(at_sha="c" * 40),
            primary_anchor=domain.AnchorLegacy(path="src/principal.py", line=40),
            cause_hint="renombre a mitad de corrida",
            evidence=[domain.EvidenceUnverified(text="y" * 100)],
        )
        snapshot = domain.Snapshot(
            schema=3,
            generation=2,
            revision=domain.Revision(
                base_sha="b" * 40, head_sha="c" * 40, policy_digest="d" * 64
            ),
            next_id=3,
            completion=domain.PARTIAL,
            findings=[resuelto_a, resuelto_b],
            command_cursor=0,
        )
        compacto = domain.compactar_cerrados(snapshot)
        self.assertEqual(
            compacto.findings[0].cause_hint,
            "doble registro del cleanup",
            "la causa viaja en el compacto: match_finding la lee para desempatar",
        )
        observacion = domain.Observation(
            title="Regresión distinta de ambos títulos",
            severity="High",
            primary_anchor=domain.AnchorLegacy(path="src/principal.py", line=99),
            related_anchors=[],
            cause_hint="doble registro del cleanup",
            evidence=[],
            claim=domain.OPEN,
        )
        revision = domain.Revision(
            base_sha="b" * 40, head_sha="9" * 40, policy_digest="d" * 64
        )
        facts = domain.RepositoryFacts(
            revision=revision,
            changed_paths=("src/principal.py",),
            delta_calculado=True,
        )
        self.assertEqual(
            domain.match_finding(compacto.findings, observacion, facts),
            domain.MatchExisting(id="F1"),
        )
        plan = domain.ReviewPlan(
            revision=revision,
            changed_paths=("src/principal.py",),
            policy=domain.ReviewPolicy(finding_identity=domain.IDENTIDAD_ANCLAS),
        )
        report = domain.validar_reporte([observacion], domain.PARTIAL, facts)
        decision = domain.accept_report(compacto, plan, report)
        self.assertIsInstance(decision, domain.Replace)
        reabierto = decision.snapshot.findings[0]
        self.assertEqual(reabierto.id, "F1")
        self.assertIsInstance(
            reabierto.status,
            domain.StatusOpen,
            "la regresión con la misma causa reabre la resolución compacta",
        )


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


PARSER_G = Path(__file__).resolve().parent / "fixtures" / "parsear_bot_g.py"


class ElParserDeGleeElStickyNuevo(unittest.TestCase):
    """El parser con el que merge.py de agent-work decide el merge, copia
    versionada como fixture (origen y sha256 del original anotados ahí) para
    que la prueba corra sin depender de dónde esté clonado G."""

    def _merge(self):
        spec = importlib.util.spec_from_file_location("parsear_bot_g", PARSER_G)
        modulo = importlib.util.module_from_spec(spec)
        sys.modules["parsear_bot_g"] = modulo
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


class ElScriptNoDependeDelLocale(unittest.TestCase):
    """CodeRabbit cr:4303977195/cr:4303977204: read_text y write_text sin
    encoding. Con un locale no UTF-8 el script debe armar exactamente el
    mismo archivo, byte a byte."""

    def _correr(self, entorno, salida):
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", ""),
        }
        env.update(entorno)
        hecho = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "armar_sticky_pr20.py"),
                "--salida",
                str(salida),
            ],
            env=env,
            capture_output=True,
            text=True,
        )
        return hecho

    def test_con_locale_c_arma_el_mismo_archivo_byte_a_byte(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta_utf8 = Path(tmp) / "sticky-utf8.md"
            ruta_c = Path(tmp) / "sticky-c.md"
            utf8 = self._correr({"PYTHONUTF8": "1"}, ruta_utf8)
            self.assertEqual(utf8.returncode, 0, utf8.stderr)
            con_c = self._correr(
                {
                    "PYTHONUTF8": "0",
                    "PYTHONCOERCECLOCALE": "0",
                    "LANG": "C",
                    "LC_ALL": "C",
                },
                ruta_c,
            )
            self.assertEqual(con_c.returncode, 0, con_c.stderr)
            self.assertEqual(
                ruta_c.read_bytes(),
                ruta_utf8.read_bytes(),
                "el sticky armado no depende del locale",
            )


def snapshot_grande():
    """Estado válido cerca del techo del perfil (bloque de ~39 KB)."""
    hallazgos = [
        domain.Finding(
            id=f"F{i}",
            title=(
                f"Fuga del pool de conexiones en el cierre del lote {i} "
                "con reintentos y reembolsos pendientes"
            ),
            severity="Medium",
            status=domain.StatusOpen(),
            primary_anchor=domain.AnchorLocated(
                path=f"src/modulo/paquete_{i}/nombre_largo_de_ruta.py",
                blob_sha="a" * 40,
                range=(10, 15),
                excerpt_digest="b" * 64,
            ),
            evidence=[domain.EvidenceUnverified(text="x" * 120)],
        )
        for i in range(1, 61)
    ]
    return domain.Snapshot(
        schema=3,
        generation=1,
        revision=domain.Revision(
            base_sha="b" * 40, head_sha="c" * 40, policy_digest="d" * 64
        ),
        next_id=61,
        completion=domain.COMPLETE_CLAIM,
        findings=hallazgos,
        command_cursor=0,
    )


class ElEstadoMandaSobreElLibro(unittest.TestCase):
    """El libro de cobertura (<30000) viaja ANTES del bloque de estado
    (<40000): si el fijo no cabe en el presupuesto del comentario (60000),
    la línea del libro se suelta (sin ella la siguiente pasada revisa full);
    si ni sin libro cabe manda el aviso de capacidad, nunca el corte a
    ciegas que parte el JSON del estado."""

    MANIFEST = {
        "mode": "full",
        "reason": "no-prev",
        "reviewed": ["src/modulo/paquete_1/nombre_largo_de_ruta.py"],
        "excluded": [],
    }

    def _resultado(self):
        return {
            "result": "COVERAGE: complete\n"
            + "detalle del revisor " * 200
            + "CENTINELA-DE-PROSA",
            "review_provider": "opencode-go",
            "model_ok": True,
        }

    def _findings(self, block, snapshot):
        return {
            "merged": domain.hallazgos_legacy(snapshot),
            "new_ids": [],
            "block": block,
            "model_ok": True,
            "completion": domain.COMPLETE_CLAIM,
        }

    def _snapshot_grande(self):
        snapshot = snapshot_grande()
        encoded = domain.encode_snapshot(snapshot)
        self.assertIsInstance(encoded, domain.EncodedCheckpoint)
        self.assertGreater(len(encoded.block.encode("utf-8")), 36000)
        self.assertLessEqual(len(encoded.block.encode("utf-8")), 40000)
        return snapshot, encoded.block

    def _libro_grande(self):
        libro = {}
        for i in range(400):
            libro[f"src/paquete_{i}/modulo/ruta_bien_larga_de_archivo_{i:03d}.py"] = (
                "a" * 40
            )
            linea = review.libro_a_linea("c" * 40, libro)
            if linea is None:
                libro.pop(next(reversed(libro)))
                break
        linea = review.libro_a_linea("c" * 40, libro)
        self.assertIsNotNone(linea)
        self.assertGreater(len(linea.encode("utf-8")), 28000, "libro cerca del tope")
        return libro

    def _sticky_verificado(self, cuerpo):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "sticky.md"
            ruta.write_text(cuerpo, encoding="utf-8")
            hecho = subprocess.run(
                [sys.executable, "scripts/verify_sticky.py", "--body-file", str(ruta)],
                capture_output=True,
                text=True,
            )
        self.assertEqual(hecho.returncode, 0, hecho.stdout + hecho.stderr)

    def test_libro_al_tope_y_estado_grande_publican_sin_libro(self):
        snapshot, block = self._snapshot_grande()
        libro = self._libro_grande()
        cuerpo = review.compose(
            self._resultado(),
            self.MANIFEST,
            sha="c" * 40,
            provider="opencode-go",
            findings=self._findings(block, snapshot),
            budget=domain.StorageBudget(),
            libro=libro,
        )
        self.assertLessEqual(len(cuerpo), domain.COMMENT_MAX_CHARS)
        self.assertLessEqual(len(cuerpo.encode("utf-8")), domain.COMMENT_MAX_BYTES)
        carga = domain.read_snapshot(cuerpo)
        self.assertIsInstance(
            carga, domain.Valid, f"estado roto: {getattr(carga, 'reason', carga)}"
        )
        self.assertNotIn(review.COVERED_PREFIX, cuerpo, "el libro cede ante el estado")
        self.assertEqual(review.reviewed_completion(cuerpo), "complete")
        self.assertEqual(review.reviewed_sha(cuerpo), "c" * 40)
        self.assertIn("detalle del revisor", cuerpo)
        self.assertEqual(
            cuerpo.count("## Detalle del revisor"),
            1,
            "la prosa ocupa un solo hueco: el header no se duplica",
        )
        self.assertEqual(
            cuerpo.count("CENTINELA-DE-PROSA"),
            1,
            "la prosa del revisor viaja una sola vez, no recortada y luego íntegra",
        )
        self._sticky_verificado(cuerpo)

    def test_prosa_que_no_cabe_entera_se_recorta_y_publica_el_estado(self):
        snapshot, block = self._snapshot_grande()
        resultado = {
            "result": "COVERAGE: complete\n" + "detalle del revisor " * 1500,
            "review_provider": "opencode-go",
            "model_ok": True,
        }
        cuerpo = review.compose(
            resultado,
            self.MANIFEST,
            sha="c" * 40,
            provider="opencode-go",
            findings=self._findings(block, snapshot),
            budget=domain.StorageBudget(),
            libro=self._libro_grande(),
        )
        self.assertLessEqual(len(cuerpo), domain.COMMENT_MAX_CHARS)
        self.assertLessEqual(len(cuerpo.encode("utf-8")), domain.COMMENT_MAX_BYTES)
        carga = domain.read_snapshot(cuerpo)
        self.assertIsInstance(
            carga,
            domain.Valid,
            f"el estado cabría sin libro y prosa recortada: {carga!r}",
        )
        self.assertNotIn(review.COVERED_PREFIX, cuerpo, "el libro cede ante el estado")
        self.assertIn(
            "Revisión recortada al presupuesto de capacidad",
            cuerpo,
            "la prosa se recorta, el estado no",
        )
        self.assertEqual(review.reviewed_completion(cuerpo), "complete")
        self.assertEqual(review.reviewed_sha(cuerpo), "c" * 40)

    def test_libro_chico_sigue_viajando(self):
        snapshot, block = self._snapshot_grande()
        libro = {"src/chico.py": "a" * 40}
        cuerpo = review.compose(
            self._resultado(),
            self.MANIFEST,
            sha="c" * 40,
            provider="opencode-go",
            findings=self._findings(block, snapshot),
            budget=domain.StorageBudget(),
            libro=libro,
        )
        self.assertLessEqual(len(cuerpo.encode("utf-8")), domain.COMMENT_MAX_BYTES)
        carga = domain.read_snapshot(cuerpo)
        self.assertIsInstance(carga, domain.Valid)
        cabeza, leido = review.libro_de(cuerpo)
        self.assertEqual(cabeza, "c" * 40)
        self.assertEqual(leido, libro)

    def test_el_caso_del_pr20_con_libro_realista(self):
        sticky = sticky_pr20()
        decision = decision_de_ronda(sticky["body"], ronda_extra())
        snapshot = decision.snapshot
        block = domain.encode_snapshot(snapshot).block
        libro = {f["file"]: "e" * 40 for f in domain.hallazgos_legacy(snapshot)}
        cuerpo = review.compose(
            dict(ronda_extra(), review_provider="opencode-go", model_ok=True),
            {
                "mode": "full",
                "reason": "no-prev",
                "reviewed": sorted(libro),
                "excluded": [],
            },
            sha=ronda_extra()["head_sha"],
            provider="opencode-go",
            findings=self._findings(block, snapshot),
            budget=domain.StorageBudget(),
            libro=libro,
        )
        self.assertLessEqual(len(cuerpo.encode("utf-8")), domain.COMMENT_MAX_BYTES)
        carga = domain.read_snapshot(cuerpo)
        self.assertIsInstance(carga, domain.Valid)
        self.assertEqual(len(carga.snapshot.findings), 24)
        cabeza, leido = review.libro_de(cuerpo)
        self.assertEqual(cabeza, ronda_extra()["head_sha"])
        self.assertEqual(leido, libro)
        self._sticky_verificado(cuerpo)

    def test_ni_sin_libro_cabe_entonces_aviso_y_nunca_corte_a_ciegas(self):
        snapshot, block = self._snapshot_grande()
        cuerpo = review.compose(
            self._resultado(),
            self.MANIFEST,
            sha="c" * 40,
            provider="opencode-go",
            findings=self._findings(block, snapshot),
            budget=domain.StorageBudget(
                comment_max_bytes=45000, comment_max_chars=45000
            ),
            libro=None,
        )
        self.assertIn(review.CAUTION_MARK, cuerpo)
        self.assertIn("no cabe", cuerpo)
        self.assertIsNone(review.reviewed_completion(cuerpo), "sin confirmación")
        self.assertNotIn(review.FINDINGS_PREFIX, cuerpo, "nunca un bloque partido")
        self.assertLessEqual(len(cuerpo), review.GITHUB_COMMENT_MAX)


class ElPasoTresNoBorraMemoria(unittest.TestCase):
    """El paso 3 de la escalera (ni con ni sin libro cabe) deja a compose un
    cuerpo SIN bloque. El escritor jamás lo publica: conserva el sticky, le
    parcha el banner CAUTION y responde Unconfirmed; sin sticky previo no
    crea nada. Una sola fuente de verdad: cuerpo_publicable."""

    def _visible_sin_espacio(self, snapshot):
        def visible(bloque, resto):
            return review.compose(
                {
                    "result": "COVERAGE: complete\n" + "detalle del revisor " * 200,
                    "review_provider": "opencode-go",
                    "model_ok": True,
                },
                {
                    "mode": "full",
                    "reason": "no-prev",
                    "reviewed": ["src/a.py"],
                    "excluded": [],
                },
                sha=snapshot.revision.head_sha,
                provider="opencode-go",
                findings={
                    "merged": domain.hallazgos_legacy(snapshot),
                    "new_ids": [],
                    "block": bloque,
                    "model_ok": True,
                    "completion": snapshot.completion,
                },
                budget=domain.StorageBudget(
                    comment_max_bytes=45000, comment_max_chars=45000
                ),
            )

        return visible

    def test_con_sticky_observado_lo_conserva_y_avisa(self):
        snapshot = snapshot_grande()
        sticky = sticky_pr20()
        falso = _ComentariosFalsos([sticky])
        resultado = review.publish_checkpoint(
            domain.Commit(snapshot=snapshot),
            sticky,
            falso,
            visible=self._visible_sin_espacio(snapshot),
        )
        self.assertIsInstance(resultado, review.Unconfirmed)
        self.assertEqual(falso.creaciones, [])
        self.assertEqual(len(falso.patches), 1)
        cuerpo = falso.leer()[0]["body"]
        carga = domain.read_snapshot(cuerpo)
        self.assertIsInstance(
            carga, (domain.Valid, domain.Legacy), "el sticky conserva su memoria"
        )
        self.assertEqual(carga.block, domain.read_snapshot(sticky["body"]).block)
        self.assertIn(review.CAUTION_MARK, cuerpo, "aviso visible")

    def test_sin_sticky_previo_no_crea_nada(self):
        snapshot = snapshot_grande()
        falso = _ComentariosFalsos([])
        resultado = review.publish_checkpoint(
            domain.Commit(snapshot=snapshot),
            None,
            falso,
            visible=self._visible_sin_espacio(snapshot),
        )
        self.assertIsInstance(resultado, review.Unconfirmed)
        self.assertEqual(falso.creaciones, [], "jamás un sticky sin bloque")
        self.assertEqual(falso.comentarios, [])

    def test_un_visible_normal_sigue_publicando(self):
        snapshot = snapshot_grande()
        sticky = sticky_pr20()
        falso = _ComentariosFalsos([sticky])
        resultado = review.publish_checkpoint(
            domain.Commit(snapshot=snapshot), sticky, falso
        )
        self.assertIsInstance(resultado, review.PublishReceipt)
        carga = domain.read_snapshot(falso.leer()[0]["body"])
        self.assertIsInstance(carga, domain.Valid)
        self.assertEqual(carga.block, domain.encode_snapshot(snapshot).block)


class ElPublicadorClasicoNoBorraMemoria(unittest.TestCase):
    """cmd_publish con un estado que produce un comentario sin espacio (el
    fijo pasa de 60000 aun sin libro: ~86 hallazgos con rutas y títulos
    largos, bloque legado de ~38 KB + secciones de ~33 KB): sin
    cuerpo_publicable parchaba el cuerpo de aviso de compose sobre el sticky
    (memoria borrada) y marcaba revisado. Con el guard: Keep+banner; sin
    sticky previo no se crea nada."""

    def _sticky_legado(self):
        bloque = review.serialize_findings({"findings": [], "next": 1})
        self.assertIsInstance(bloque, str)
        return {
            "id": 55,
            "user": "github-actions[bot]",
            "body": (
                review.MARKER
                + "\n"
                + f"{review.SHA_PREFIX}{'a' * 40} -->\n"
                + f"{review.COMPLETION_PREFIX}{'a' * 40}:complete -->\n"
                + bloque
            ),
        }

    def _correr(self, comments, cuantos=86):
        hallazgos = [
            {
                "file": f"src/{'m' * 195}_{i}.py",
                "line": i,
                "severity": "Medium",
                "title": f"Problema {i}: " + "f" * 145,
                "state": "open",
            }
            for i in range(1, cuantos + 1)
        ]
        resultado = {
            "result": review.FINDINGS_PREFIX
            + json.dumps({"findings": hallazgos, "next": 200}, separators=(",", ":"))
            + review.FINDINGS_SUFFIX,
            "review_provider": "opencode-go",
        }
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "result.json").write_text(json.dumps(resultado))
            (work / "manifest.json").write_text(
                json.dumps(
                    {
                        "mode": "full",
                        "reason": "no-prev",
                        "reviewed": ["a.py"],
                        "excluded": [],
                    }
                )
            )
            salida = Path(tmp) / "salida.txt"
            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "REPO": "x/y",
                        "PR_NUMBER": "1",
                        "HEAD_SHA": "c" * 40,
                        "GITHUB_OUTPUT": str(salida),
                    },
                ),
                mock.patch.object(review, "fetch_all_comments", return_value=comments),
                mock.patch.object(review, "_repo_actual", return_value=None),
                mock.patch.object(review, "libro_actualizado", return_value=None),
            ):
                llamadas = []

                def falso_sh(*args, **kwargs):
                    llamadas.append(list(args))

                    class Hecho:
                        stdout = ""

                    return Hecho()

                with mock.patch.object(review, "sh", falso_sh):
                    review.cmd_publish(argparse.Namespace(work=str(work)))
            cuerpo = json.loads((work / "comment.json").read_text())["body"]
            revisado = salida.read_text().strip() if salida.exists() else ""
            return cuerpo, llamadas, revisado

    def test_con_sticky_conserva_el_bloque_y_avisa(self):
        sticky = self._sticky_legado()
        cuerpo, llamadas, revisado = self._correr([sticky])
        self.assertEqual(len(llamadas), 1, "solo el PATCH del banner")
        self.assertIn("PATCH", llamadas[0])
        carga = domain.read_snapshot(cuerpo)
        self.assertIsInstance(
            carga, (domain.Valid, domain.Legacy), "el sticky conserva su memoria"
        )
        self.assertEqual(carga.block, domain.read_snapshot(sticky["body"]).block)
        self.assertIn(review.CAUTION_MARK, cuerpo)
        self.assertIn("reviewed=false", revisado)

    def test_sin_sticky_previo_no_crea_nada(self):
        cuerpo, llamadas, revisado = self._correr([])
        self.assertEqual(llamadas, [], "sin sticky no se crea comentario")
        self.assertIn("reviewed=false", revisado)

    def test_un_comentario_que_cabe_sigue_publicando(self):
        sticky = self._sticky_legado()
        cuerpo, llamadas, revisado = self._correr([sticky], cuantos=3)
        self.assertEqual(len(llamadas), 1)
        carga = domain.read_snapshot(cuerpo)
        self.assertIsInstance(carga, domain.Legacy)
        self.assertEqual(len(carga.raw["findings"]), 3)
        self.assertEqual(carga.snapshot.findings[-1].id, "F3")
        self.assertNotIn(review.CAUTION_MARK, cuerpo)
        self.assertIn("reviewed=true", revisado)


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
        self.assertEqual(len(carga.raw["findings"]), 62)
        self.assertEqual(carga.raw["next"], 63)


class ElArmadorDelStickyRechazaElDesborde(unittest.TestCase):
    """CodeRabbit: armar_sticky_pr20 leía .block de un encode que puede
    devolver CapacityExceeded: AttributeError en vez de un error claro."""

    @staticmethod
    def cargar_armador():
        especificacion = importlib.util.spec_from_file_location(
            "armar_sticky_pr20", ROOT / "scripts" / "armar_sticky_pr20.py"
        )
        modulo = importlib.util.module_from_spec(especificacion)
        especificacion.loader.exec_module(modulo)
        return modulo

    def test_una_ronda_que_desborda_da_error_claro(self):
        armador = self.cargar_armador()
        body, ronda = armador.cargar_ronda()
        ronda["model_block"]["findings"] = [
            {
                "file": f"src/hermes/modulo_{i:03d}.py",
                "line": i + 1,
                "severity": "Medium",
                "title": "hallazgo enorme " + "x" * 400 + f" numero {i}",
                "state": "open",
            }
            for i in range(120)
        ]
        with self.assertRaises(SystemExit) as contexto:
            armador.armar(body, ronda)
        mensaje = str(contexto.exception)
        self.assertIn("no cabe", mensaje)
        self.assertIn("40000", mensaje)


if __name__ == "__main__":
    unittest.main()
