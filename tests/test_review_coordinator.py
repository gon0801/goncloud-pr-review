import argparse
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import review  # noqa: E402
import review_domain as domain  # noqa: E402


def snapshot_base(generation=1, findings=(), completion=domain.UNKNOWN, schema=3):
    return domain.Snapshot(
        schema=schema,
        generation=generation,
        revision=domain.Revision(
            base_sha="b" * 40, head_sha="c" * 40, policy_digest="d" * 64
        ),
        next_id=1,
        completion=completion,
        findings=list(findings),
        command_cursor=0,
    )


TARGET = domain.ReviewTarget(
    repository="o/r",
    pr_number=1,
    head_sha="c" * 40,
    base_sha="b" * 40,
    policy_digest="d" * 64,
)
PUSH = domain.Origin(kind="push")
POLICY = domain.ReviewPolicy()


def hechos(revision=None):
    return domain.RepositoryFacts(
        revision=revision
        or domain.Revision(
            base_sha="b" * 40, head_sha="c" * 40, policy_digest="d" * 64
        ),
        changed_paths=("a.py",),
        delta_calculado=True,
    )


def request_de(snapshot, rid):
    return next((r for r in snapshot.pending_requests if r.id == rid), None)


class RequestTransitions(unittest.TestCase):
    """T07: solicitudes persistidas antes de despachar."""

    def test_persists_request_before_work(self):
        decision = domain.reconcile(
            snapshot_base(),
            domain.RequestReview(origin=PUSH, target=TARGET),
            hechos(),
            POLICY,
        )
        self.assertIsInstance(decision, domain.Commit)
        self.assertEqual(len(decision.snapshot.pending_requests), 1)
        solicitud = decision.snapshot.pending_requests[0]
        self.assertEqual(solicitud.id, 1)
        self.assertEqual(solicitud.state, "pending")
        self.assertEqual(solicitud.kind, "review")
        self.assertEqual(
            solicitud.target,
            {"head_sha": "c" * 40, "base_sha": "b" * 40, "policy_digest": "d" * 64},
        )
        self.assertEqual(decision.snapshot.request_count, 1)
        self.assertEqual(len(decision.work_after_commit), 1)
        self.assertIs(decision.work_after_commit[0], solicitud)

    def test_evento_automatico_repetido_se_agrupa(self):
        estado = snapshot_base()
        primera = domain.reconcile(
            estado, domain.RequestReview(origin=PUSH, target=TARGET), hechos(), POLICY
        )
        repetida = domain.reconcile(
            primera.snapshot,
            domain.RequestReview(origin=PUSH, target=TARGET),
            hechos(),
            POLICY,
        )
        self.assertIsInstance(repetida, domain.Commit)
        self.assertEqual(
            repetida.snapshot.pending_requests,
            primera.snapshot.pending_requests,
            "agrupada: el estado no cambia",
        )
        self.assertEqual(
            repetida.work_after_commit,
            tuple(primera.snapshot.pending_requests),
            "el trabajo ya persistido se re-deriva para la recuperación",
        )

    def test_comando_explicito_mismo_sha_crea_nueva_solicitud(self):
        estado = snapshot_base()
        automatica = domain.reconcile(
            estado, domain.RequestReview(origin=PUSH, target=TARGET), hechos(), POLICY
        )
        comando = domain.reconcile(
            automatica.snapshot,
            domain.AuthorizedCommand(
                origin=domain.Origin(kind="comando", comment_id=9),
                target=TARGET,
                action="revisar",
            ),
            hechos(),
            POLICY,
        )
        self.assertIsInstance(comando, domain.Commit)
        self.assertEqual(len(comando.snapshot.pending_requests), 2)
        self.assertEqual([r.id for r in comando.snapshot.pending_requests], [1, 2])
        self.assertEqual(comando.snapshot.request_count, 2)
        self.assertEqual(len(comando.work_after_commit), 1)
        self.assertEqual(comando.work_after_commit[0].id, 2)
        self.assertEqual(comando.work_after_commit[0].origin, "comando")

    def test_compactacion_e_ids_nunca_reutilizados(self):
        estado = snapshot_base()
        creada = domain.reconcile(
            estado, domain.RequestReview(origin=PUSH, target=TARGET), hechos(), POLICY
        )
        resultado = domain.ReportReady(
            origin=domain.Origin(kind="push", run_id=11),
            request_id=1,
            run_id=11,
            attempt=1,
            observaciones=(),
            cobertura=domain.PARTIAL,
        )
        aceptado = domain.reconcile(creada.snapshot, resultado, hechos(), POLICY)
        self.assertIsInstance(aceptado, domain.Commit)
        self.assertEqual(aceptado.snapshot.pending_requests, [])
        self.assertEqual(aceptado.snapshot.request_count, 1)
        nueva = domain.reconcile(
            aceptado.snapshot,
            domain.RequestReview(origin=PUSH, target=TARGET),
            hechos(),
            POLICY,
        )
        self.assertIsInstance(nueva, domain.Commit)
        self.assertEqual(nueva.snapshot.pending_requests[0].id, 2)
        self.assertEqual(nueva.snapshot.request_count, 2)

    def test_only_pending_request_accepts_result(self):
        estado = snapshot_base()
        creada = domain.reconcile(
            estado, domain.RequestReview(origin=PUSH, target=TARGET), hechos(), POLICY
        )
        observacion = domain.Observation(
            title="Fuga nueva",
            severity="High",
            primary_anchor=domain.AnchorLegacy(path="a.py", line=1),
            claim=domain.OPEN,
        )
        resultado = domain.ReportReady(
            origin=domain.Origin(kind="push", run_id=11),
            request_id=1,
            run_id=11,
            attempt=1,
            observaciones=(observacion,),
            cobertura=domain.PARTIAL,
        )
        aceptado = domain.reconcile(creada.snapshot, resultado, hechos(), POLICY)
        self.assertIsInstance(aceptado, domain.Commit)
        self.assertEqual([f.title for f in aceptado.snapshot.findings], ["Fuga nueva"])
        self.assertEqual(aceptado.snapshot.pending_requests, [])

        repetido = domain.reconcile(aceptado.snapshot, resultado, hechos(), POLICY)
        self.assertIsInstance(repetido, domain.Keep)

        desconocido = domain.reconcile(
            aceptado.snapshot,
            domain.replace(resultado, request_id=99),
            hechos(),
            POLICY,
        )
        self.assertIsInstance(desconocido, domain.Keep)

    def test_descarte_entre_preparacion_y_resultado(self):
        estado = snapshot_base()
        creada = domain.reconcile(
            estado, domain.RequestReview(origin=PUSH, target=TARGET), hechos(), POLICY
        )
        previo = domain.Finding(
            id="F1",
            title="Vieja",
            severity="Low",
            status=domain.StatusOpen(),
            primary_anchor=domain.AnchorLegacy(path="a.py", line=1),
        )
        con_descarte = domain.replace(creada.snapshot, findings=[previo], next_id=2)
        con_descarte = domain.aplicar_descartes(
            con_descarte, {"F1"}, False, comment_id=3
        )
        con_descarte = domain.avanzar_cursor(con_descarte, 3)
        observacion = domain.Observation(
            title="Vieja",
            severity="Low",
            primary_anchor=domain.AnchorLegacy(path="a.py", line=1),
            claim=domain.OPEN,
        )
        resultado = domain.ReportReady(
            origin=domain.Origin(kind="push", run_id=11),
            request_id=1,
            run_id=11,
            attempt=1,
            observaciones=(observacion,),
            cobertura=domain.PARTIAL,
        )
        aceptado = domain.reconcile(con_descarte, resultado, hechos(), POLICY)
        self.assertIsInstance(aceptado, domain.Commit)
        f1 = {f.id: f for f in aceptado.snapshot.findings}["F1"]
        self.assertIsInstance(
            f1.status,
            domain.StatusDismissed,
            "el resultado se acepta sobre el snapshot actual: el descarte sobrevive",
        )
        self.assertEqual(aceptado.snapshot.command_cursor, 3)

    def test_resultado_vencido_no_acredita_cobertura_y_deja_trabajo_vigente(self):
        estado = snapshot_base()
        creada = domain.reconcile(
            estado, domain.RequestReview(origin=PUSH, target=TARGET), hechos(), POLICY
        )
        revision_nueva = domain.Revision(
            base_sha="b" * 40, head_sha="e" * 40, policy_digest="9" * 64
        )
        observacion = domain.Observation(
            title="Fuga",
            severity="High",
            primary_anchor=domain.AnchorLegacy(path="a.py", line=1),
            claim=domain.OPEN,
        )
        resultado = domain.ReportReady(
            origin=domain.Origin(kind="push", run_id=11),
            request_id=1,
            run_id=11,
            attempt=1,
            observaciones=(observacion,),
            cobertura=domain.COMPLETE_CLAIM,
        )
        vencido = domain.reconcile(
            creada.snapshot, resultado, hechos(revision_nueva), POLICY
        )
        self.assertIsInstance(vencido, domain.Commit)
        self.assertEqual(
            vencido.snapshot.completion,
            domain.UNKNOWN,
            "el resultado antiguo no acredita cobertura",
        )
        self.assertEqual(vencido.snapshot.findings, [])
        self.assertEqual([r.id for r in vencido.snapshot.pending_requests], [2])
        nueva = request_de(vencido.snapshot, 2)
        self.assertEqual(nueva.target["head_sha"], "e" * 40)
        self.assertEqual(nueva.target["policy_digest"], "9" * 64)
        self.assertEqual(len(vencido.work_after_commit), 1)

    def test_push_nuevo_poda_la_solicitud_superseded(self):
        estado = snapshot_base()
        primera = domain.reconcile(
            estado, domain.RequestReview(origin=PUSH, target=TARGET), hechos(), POLICY
        )
        target_nuevo = domain.ReviewTarget(
            repository="o/r",
            pr_number=1,
            head_sha="e" * 40,
            base_sha="b" * 40,
            policy_digest="d" * 64,
        )
        segunda = domain.reconcile(
            primera.snapshot,
            domain.RequestReview(origin=PUSH, target=target_nuevo),
            hechos(),
            POLICY,
        )
        self.assertIsInstance(segunda, domain.Commit)
        self.assertEqual([r.id for r in segunda.snapshot.pending_requests], [2])
        self.assertEqual(segunda.snapshot.request_count, 2)

        vencido = domain.reconcile(
            segunda.snapshot,
            domain.ReportReady(
                origin=domain.Origin(kind="push", run_id=11),
                request_id=1,
                run_id=11,
                attempt=1,
                observaciones=(),
                cobertura=domain.PARTIAL,
            ),
            hechos(),
            POLICY,
        )
        self.assertIsInstance(vencido, domain.Keep)

    def test_tombstones_antiguos_se_podian(self):
        estado = snapshot_base()
        primera = domain.reconcile(
            estado, domain.RequestReview(origin=PUSH, target=TARGET), hechos(), POLICY
        )
        terminal = domain.reconcile(
            primera.snapshot,
            domain.ReportFailed(
                request_id=1, run_id=11, attempt=1, motivo="x", retryable=False
            ),
            hechos(),
            POLICY,
        )
        segunda = domain.reconcile(
            terminal.snapshot,
            domain.RequestReview(origin=PUSH, target=TARGET),
            hechos(),
            POLICY,
        )
        self.assertIsInstance(segunda, domain.Commit)
        self.assertEqual([r.id for r in segunda.snapshot.pending_requests], [2])
        self.assertEqual(segunda.snapshot.request_count, 2)

    def test_vencido_con_viva_del_target_nuevo_reusa_y_no_duplica(self):
        """Con una solicitud viva del target nuevo, el vencido de la vieja
        re-despacha la viva en lugar de crear otra."""
        estado = snapshot_base()
        primera = domain.reconcile(
            estado, domain.RequestReview(origin=PUSH, target=TARGET), hechos(), POLICY
        )
        target_nuevo = domain.ReviewTarget(
            repository="o/r",
            pr_number=1,
            head_sha="e" * 40,
            base_sha="b" * 40,
            policy_digest="d" * 64,
        )
        # el comando explícito no poda
        segunda = domain.reconcile(
            primera.snapshot,
            domain.AuthorizedCommand(
                origin=domain.Origin(kind="comando", comment_id=9),
                target=target_nuevo,
                action="revisar",
            ),
            hechos(),
            POLICY,
        )
        self.assertEqual([r.id for r in segunda.snapshot.pending_requests], [1, 2])
        revision_nueva = domain.Revision(
            base_sha="b" * 40, head_sha="e" * 40, policy_digest="d" * 64
        )
        vencido = domain.reconcile(
            segunda.snapshot,
            domain.ReportReady(
                origin=domain.Origin(kind="push", run_id=11),
                request_id=1,
                run_id=11,
                attempt=1,
                observaciones=(),
                cobertura=domain.PARTIAL,
            ),
            hechos(revision_nueva),
            POLICY,
        )
        self.assertIsInstance(vencido, domain.Commit)
        self.assertEqual([r.id for r in vencido.snapshot.pending_requests], [2])
        self.assertEqual([s.id for s in vencido.work_after_commit], [2])

    def test_sin_target_o_sin_revision_no_acreditan(self):
        observacion = domain.Observation(
            title="Fuga",
            severity="High",
            primary_anchor=domain.AnchorLegacy(path="a.py", line=1),
            claim=domain.OPEN,
        )
        resultado = domain.ReportReady(
            origin=domain.Origin(kind="push", run_id=11),
            request_id=1,
            run_id=11,
            attempt=1,
            observaciones=(observacion,),
            cobertura=domain.COMPLETE_CLAIM,
        )
        antigua = domain.replace(snapshot_base(), request_count=1)
        antigua = domain.replace(
            antigua,
            pending_requests=[
                domain.WorkRequest(
                    id=1, kind="review", origin="push", basis_generation=1
                )
            ],
        )
        vencida = domain.reconcile(antigua, resultado, hechos(), POLICY)
        self.assertIsInstance(vencida, domain.Commit)
        self.assertEqual(vencida.snapshot.completion, domain.UNKNOWN)

        con_target = domain.replace(
            antigua,
            pending_requests=[
                domain.replace(
                    antigua.pending_requests[0],
                    target=TARGET.json(),
                )
            ],
        )
        sin_revision = domain.reconcile(
            con_target,
            resultado,
            domain.RepositoryFacts(revision=None, delta_calculado=False),
            POLICY,
        )
        self.assertIsInstance(sin_revision, domain.Commit)
        self.assertEqual(sin_revision.snapshot.completion, domain.UNKNOWN)

    def test_cada_campo_de_vigencia_se_discrimina(self):
        """Cambiar un solo campo de vigencia vence la solicitud: sin hallazgos
        acreditados, cobertura unknown y reexpedición con el target nuevo."""
        for campo, valor in (
            ("head_sha", "e" * 40),
            ("base_sha", "9" * 40),
            ("policy_digest", "9" * 64),
        ):
            with self.subTest(campo=campo):
                estado = snapshot_base()
                creada = domain.reconcile(
                    estado,
                    domain.RequestReview(origin=PUSH, target=TARGET),
                    hechos(),
                    POLICY,
                )
                revision = domain.Revision(
                    base_sha="b" * 40,
                    head_sha="c" * 40,
                    policy_digest="d" * 64,
                )
                setattr(revision, campo, valor)
                observacion = domain.Observation(
                    title="Fuga",
                    severity="High",
                    primary_anchor=domain.AnchorLegacy(path="a.py", line=1),
                    claim=domain.OPEN,
                )
                vencido = domain.reconcile(
                    creada.snapshot,
                    domain.ReportReady(
                        origin=domain.Origin(kind="push", run_id=11),
                        request_id=1,
                        run_id=11,
                        attempt=1,
                        observaciones=(observacion,),
                        cobertura=domain.COMPLETE_CLAIM,
                    ),
                    hechos(revision),
                    POLICY,
                )
                self.assertIsInstance(vencido, domain.Commit)
                self.assertEqual(vencido.snapshot.completion, domain.UNKNOWN)
                self.assertEqual(vencido.snapshot.findings, [])
                self.assertEqual([r.id for r in vencido.snapshot.pending_requests], [2])
                self.assertEqual(
                    vencido.snapshot.pending_requests[0].target[campo], valor
                )

    def test_observacion_rechazada_no_crashea_y_no_acredita(self):
        """CodeRabbit r1: la frontera valida el artifact antes de aceptar."""
        estado = snapshot_base()
        creada = domain.reconcile(
            estado, domain.RequestReview(origin=PUSH, target=TARGET), hechos(), POLICY
        )
        rechazada = domain.observation_de_entrada(
            {
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
        )
        self.assertIsInstance(rechazada, domain.ObservationRejected)
        vencido = domain.reconcile(
            creada.snapshot,
            domain.ReportReady(
                origin=domain.Origin(kind="push", run_id=11),
                request_id=1,
                run_id=11,
                attempt=1,
                observaciones=(rechazada,),
                cobertura=domain.COMPLETE_CLAIM,
            ),
            hechos(),
            POLICY,
        )
        self.assertIsInstance(vencido, domain.Commit)
        self.assertEqual(
            vencido.snapshot.completion,
            domain.UNKNOWN,
            "un bloque con formas inválidas no acredita cobertura",
        )
        self.assertEqual(vencido.snapshot.pending_requests, [])

    def test_transiciones_de_fallo(self):
        estado = snapshot_base()
        creada = domain.reconcile(
            estado, domain.RequestReview(origin=PUSH, target=TARGET), hechos(), POLICY
        )
        reintento = domain.reconcile(
            creada.snapshot,
            domain.ReportFailed(
                request_id=1,
                run_id=11,
                attempt=1,
                motivo="proveedor 503",
                retryable=True,
            ),
            hechos(),
            POLICY,
        )
        self.assertIsInstance(reintento, domain.Commit)
        self.assertEqual(
            reintento.snapshot.pending_requests[0].state, "failed_retryable"
        )
        # un intento nuevo puede satisfacer la solicitud fallida
        resultado = domain.ReportReady(
            origin=domain.Origin(kind="push", run_id=12),
            request_id=1,
            run_id=12,
            attempt=2,
            observaciones=(),
            cobertura=domain.PARTIAL,
        )
        aceptado = domain.reconcile(reintento.snapshot, resultado, hechos(), POLICY)
        self.assertIsInstance(aceptado, domain.Commit)
        self.assertEqual(aceptado.snapshot.pending_requests, [])

        creada2 = domain.reconcile(
            aceptado.snapshot,
            domain.RequestReview(origin=PUSH, target=TARGET),
            hechos(),
            POLICY,
        )
        terminal = domain.reconcile(
            creada2.snapshot,
            domain.ReportFailed(
                request_id=2,
                run_id=13,
                attempt=1,
                motivo="solicitud revocada",
                retryable=False,
            ),
            hechos(),
            POLICY,
        )
        self.assertIsInstance(terminal, domain.Commit)
        finalizada = request_de(terminal.snapshot, 2)
        self.assertEqual(finalizada.state, "finished")
        self.assertEqual(finalizada.motivo, "solicitud revocada")
        repetido = domain.reconcile(
            terminal.snapshot,
            domain.ReportReady(
                origin=domain.Origin(kind="push", run_id=14),
                request_id=2,
                run_id=14,
                attempt=1,
                observaciones=(),
                cobertura=domain.PARTIAL,
            ),
            hechos(),
            POLICY,
        )
        self.assertIsInstance(repetido, domain.Keep)


class _ComentarioFalso:
    """GitHub falso: aplica la escritura y puede perder la respuesta."""

    def __init__(self, comentarios):
        self.comentarios = list(comentarios)
        self.patches = []
        self.creaciones = []
        self.perder_patch = False
        self.perder_creacion = False

    def leer(self):
        return [dict(c) for c in self.comentarios]

    def parchar(self, cid, body):
        self.patches.append((cid, body))
        for c in self.comentarios:
            if c["id"] == cid:
                c["body"] = body
        if self.perder_patch:
            raise RuntimeError("respuesta de PATCH perdida")

    def crear(self, body):
        self.creaciones.append(body)
        cid = 100 + len(self.creaciones)
        self.comentarios.append({"id": cid, "body": body})
        if self.perder_creacion:
            raise RuntimeError("respuesta de POST perdida")


def cuerpo_v3(snapshot):
    return f"{review.MARKER}\n{domain.encode_snapshot(snapshot)}"


def decision_con_trabajo(estado):
    creada = domain.reconcile(
        estado, domain.RequestReview(origin=PUSH, target=TARGET), hechos(), POLICY
    )
    return creada


class CheckpointConservaLaCabecera(unittest.TestCase):
    """Ronda 3 del piloto (#91): el checkpoint de admisión escribía el bloque
    antes de sha=/completion=; reviewed_completion exige esas líneas tras el
    marcador y el worker leía la revisión previa como incompleta."""

    HEAD = "e" * 40

    def _con_revision(self, estado):
        return (
            f"{review.MARKER}\n{review.SHA_PREFIX}{self.HEAD} -->\n"
            f"{review.COMPLETION_PREFIX}{self.HEAD}:complete -->\n"
            f"{domain.encode_snapshot(estado).block}\n\n"
            "### Revisión automática · DeepSeek V4.1 Flash · OpenCode Go · eeeeeee"
        )

    def test_la_admision_conserva_sha_y_completion_tras_el_marcador(self):
        estado = snapshot_base(generation=1)
        falso = _ComentarioFalso([{"id": 7, "body": self._con_revision(estado)}])
        decision = decision_con_trabajo(estado)
        recibo = review.publish_checkpoint(decision, falso.leer()[0], falso)
        self.assertIsInstance(recibo, review.PublishReceipt)
        cuerpo = falso.patches[-1][1]
        self.assertEqual(
            cuerpo.split("\n")[:3],
            [
                review.MARKER,
                f"{review.SHA_PREFIX}{self.HEAD} -->",
                f"{review.COMPLETION_PREFIX}{self.HEAD}:complete -->",
            ],
        )
        self.assertEqual(
            review.prev_de_memoria(cuerpo)["completion"],
            "complete",
            "el worker del siguiente push no ve la revisión previa como incompleta",
        )
        recarga = domain.read_snapshot(cuerpo)
        self.assertIsInstance(recarga, domain.Valid)
        self.assertEqual(recarga.snapshot.generation, decision.snapshot.generation)
        self.assertTrue(cuerpo.endswith("OpenCode Go · eeeeeee"))

    def test_el_aviso_de_fallo_conserva_sha_y_completion_tras_el_marcador(self):
        estado = snapshot_base(generation=1)
        bloque = domain.encode_snapshot(estado).block
        resto = self._con_revision(estado).split("\n", 1)[1]
        resto = review.strip_findings_block(resto, last=False).strip()
        cuerpo = review.revision_visible(
            {review.ERROR_KEY: "el proveedor no respondió"}, estado, set(), "f" * 40
        )(bloque, resto)
        self.assertEqual(
            cuerpo.split("\n")[:4],
            [
                review.MARKER,
                f"{review.SHA_PREFIX}{self.HEAD} -->",
                f"{review.COMPLETION_PREFIX}{self.HEAD}:complete -->",
                bloque,
            ],
        )
        self.assertIn("No se pudo revisar el commit fffffff", cuerpo)
        self.assertEqual(review.reviewed_completion(cuerpo), "complete")


class CheckpointRecovery(unittest.TestCase):
    """T08: recuperar escrituras inciertas sin repetir efectos."""

    def test_patch_response_lost(self):
        estado = snapshot_base(generation=1)
        falso = _ComentarioFalso([{"id": 7, "body": cuerpo_v3(estado)}])
        decision = decision_con_trabajo(estado)
        falso.perder_patch = True
        resultado = review.publish_checkpoint(decision, falso.leer()[0], falso)
        self.assertIsInstance(resultado, review.Unconfirmed)

        observado = falso.leer()[0]
        confirmado = review.publish_checkpoint(decision, observado, falso)
        self.assertIsInstance(confirmado, review.PublishReceipt)
        self.assertEqual(len(falso.patches), 1, "no reaplica el PATCH ya aplicado")
        self.assertEqual(confirmado.generacion, decision.snapshot.generation)
        self.assertEqual(len(confirmado.trabajo), 1)

    def test_caida_antes_de_dispatch_y_recuperacion(self):
        estado = snapshot_base(generation=1)
        falso = _ComentarioFalso([{"id": 7, "body": cuerpo_v3(estado)}])
        decision = decision_con_trabajo(estado)
        recibo = review.publish_checkpoint(decision, falso.leer()[0], falso)
        self.assertIsInstance(recibo, review.PublishReceipt)

        # caída antes de despachar: el próximo evento relee y recupera el trabajo
        despachados = []
        review.dispatch_confirmed(recibo, despachar=despachados.append)
        self.assertEqual([s.id for s in despachados], [1])

    def test_dispatch_incierto_y_artifact_vencido(self):
        estado = snapshot_base(generation=1)
        falso = _ComentarioFalso([{"id": 7, "body": cuerpo_v3(estado)}])
        decision = decision_con_trabajo(estado)
        recibo = review.publish_checkpoint(decision, falso.leer()[0], falso)

        intentos = []

        def despacho_incierto(solicitud):
            intentos.append(solicitud.id)
            if len(intentos) == 1:
                raise RuntimeError("dispatch incierto")

        with self.assertRaises(RuntimeError):
            review.dispatch_confirmed(recibo, despachar=despacho_incierto)
        review.dispatch_confirmed(recibo, despachar=despacho_incierto)
        self.assertEqual(intentos, [1, 1], "la recuperación repite el intento")

        vencido = {
            "repository": "o/r",
            "workflow": "ai-review-worker",
            "ref": "refs/heads/main",
            "sha": "f" * 40,
            "run_id": 11,
            "attempt": 1,
        }
        artifact = {
            "request_id": 1,
            "run_id": 999,
            "attempt": 1,
            "pr_head_sha": "c" * 40,
            "policy_digest": "d" * 64,
        }
        request = {
            "id": 1,
            "target": TARGET.json(),
            "repository": "o/r",
            "workflow": "ai-review-worker",
            "ref": "refs/heads/main",
            "workflow_sha": "f" * 40,
        }
        resultado = review.authenticate_result(vencido, artifact, request)
        self.assertIsInstance(resultado, review.Rejected)

    def test_post_incierto_y_duplicados(self):
        estado = snapshot_base(generation=1)
        falso = _ComentarioFalso([])
        decision = decision_con_trabajo(estado)
        falso.perder_creacion = True
        resultado = review.publish_checkpoint(decision, None, falso)
        self.assertIsInstance(resultado, review.Unconfirmed)
        confirmado = review.publish_checkpoint(decision, falso.leer()[0], falso)
        self.assertIsInstance(confirmado, review.PublishReceipt)
        self.assertEqual(len(falso.creaciones), 1, "no crea otro comentario")

        duplicados = _ComentarioFalso(
            [
                {"id": 1, "body": cuerpo_v3(estado)},
                {"id": 2, "body": cuerpo_v3(estado)},
            ]
        )
        detenido = review.publish_checkpoint(decision, duplicados.leer()[0], duplicados)
        self.assertIsInstance(detenido, review.Unconfirmed)
        self.assertEqual(duplicados.patches, [])
        self.assertEqual(duplicados.creaciones, [])

    def test_publish_reemplaza_el_primer_bloque_del_sticky(self):
        estado = snapshot_base(generation=1)
        decision = decision_con_trabajo(estado)
        citado = cuerpo_v3(snapshot_base(generation=9))
        cuerpo = f"{cuerpo_v3(estado)}\n\nLa prosa cita un bloque del PR:\n{citado}\n"
        falso = _ComentarioFalso([{"id": 7, "body": cuerpo}])
        resultado = review.publish_checkpoint(
            decision, falso.leer()[0], falso, login="bot"
        )
        self.assertIsInstance(resultado, review.PublishReceipt)
        self.assertEqual(len(falso.patches), 1)
        cuerpo_nuevo = falso.leer()[0]["body"]
        carga = domain.read_snapshot(cuerpo_nuevo)
        self.assertEqual(
            carga.snapshot.generation,
            decision.snapshot.generation,
            "el checkpoint nuevo gobierna (primer bloque)",
        )
        self.assertIn(citado, cuerpo_nuevo, "el bloque citado sobrevive a la escritura")

    def test_forma_real_de_comentarios_no_crashea(self):
        """CodeRabbit r1: fetch_all_comments trae user como texto."""
        estado = snapshot_base(generation=1)
        decision = decision_con_trabajo(estado)
        falso = _ComentarioFalso([{"id": 7, "body": cuerpo_v3(estado), "user": "bot"}])
        resultado = review.publish_checkpoint(
            decision, falso.leer()[0], falso, login="bot"
        )
        self.assertIsInstance(resultado, review.PublishReceipt)

    def test_otros_logins_no_cuentan_como_duplicados(self):
        estado = snapshot_base(generation=1)
        decision = decision_con_trabajo(estado)
        falso = _ComentarioFalso(
            [
                {"id": 1, "body": cuerpo_v3(estado), "login": "bot"},
                {"id": 2, "body": cuerpo_v3(estado), "login": "intruso"},
            ]
        )
        resultado = review.publish_checkpoint(
            decision, falso.leer()[0], falso, login="bot"
        )
        self.assertIsInstance(resultado, review.PublishReceipt)

        dos_del_bot = _ComentarioFalso(
            [
                {"id": 1, "body": cuerpo_v3(estado), "login": "bot"},
                {"id": 2, "body": cuerpo_v3(estado), "login": "bot"},
            ]
        )
        detenido = review.publish_checkpoint(
            decision, dos_del_bot.leer()[0], dos_del_bot, login="bot"
        )
        self.assertIsInstance(detenido, review.Unconfirmed)
        self.assertEqual(dos_del_bot.patches, [])

    def test_keep_es_un_no_op_confirmado(self):
        estado = snapshot_base(generation=1)
        falso = _ComentarioFalso([{"id": 7, "body": cuerpo_v3(estado)}])
        resultado = review.publish_checkpoint(
            domain.Keep(reason="resultado repetido"),
            falso.leer()[0],
            falso,
            login="bot",
        )
        self.assertIsInstance(resultado, review.PublishReceipt)
        self.assertEqual(resultado.trabajo, ())
        self.assertEqual(falso.patches, [])

    def test_request_de_la_solicitud_persistida_no_del_artifact(self):
        estado = snapshot_base(generation=1)
        creada = decision_con_trabajo(estado)
        solicitud = creada.work_after_commit[0]
        artifact = {
            "request_id": 1,
            "run_id": 11,
            "attempt": 1,
            "pr_head_sha": "1" * 40,
            "policy_digest": "9" * 64,
            "target": {
                "head_sha": "1" * 40,
                "base_sha": "b" * 40,
                "policy_digest": "9" * 64,
            },
            "observaciones": (),
            "cobertura": domain.COMPLETE_CLAIM,
        }
        request = review.request_de_solicitud(
            solicitud, repository="o/r", ref="main", workflow_sha="f" * 40
        )
        self.assertEqual(
            request["target"]["head_sha"],
            "c" * 40,
            "el target viene de la solicitud persistida, no del artifact",
        )
        metadata = {
            "repository": "o/r",
            "workflow": "ai-review-worker",
            "ref": "main",
            "sha": "f" * 40,
            "run_id": 11,
            "attempt": 1,
        }
        rechazado = review.authenticate_result(metadata, artifact, request)
        self.assertIsInstance(rechazado, review.Rejected)


class ResultAuthentication(unittest.TestCase):
    """T08: el SHA del workflow (código confiable) no es el HEAD del PR."""

    def _contexto(self):
        return {
            "id": 1,
            "target": TARGET.json(),
            "repository": "o/r",
            "workflow": "ai-review-worker",
            "ref": "refs/heads/main",
            "workflow_sha": "f" * 40,
        }

    def _metadata(self, **over):
        base = {
            "repository": "o/r",
            "workflow": "ai-review-worker",
            "ref": "refs/heads/main",
            "sha": "f" * 40,
            "run_id": 11,
            "attempt": 1,
        }
        base.update(over)
        return base

    def _artifact(self, **over):
        base = {
            "request_id": 1,
            "run_id": 11,
            "attempt": 1,
            "pr_head_sha": "c" * 40,
            "policy_digest": "d" * 64,
            "observaciones": (),
            "cobertura": domain.PARTIAL,
        }
        base.update(over)
        return base

    def test_workflow_sha_is_not_pr_sha(self):
        ok = review.authenticate_result(
            self._metadata(), self._artifact(), self._contexto()
        )
        self.assertIsInstance(ok, review.AuthenticatedResult)
        self.assertEqual(ok.observaciones, self._artifact()["observaciones"])

    def test_rechaza_cada_campo_incorrecto(self):
        casos = {
            "repo": ("repository", "otro/r"),
            "workflow": ("workflow", "otro-workflow"),
            "ref": ("ref", "refs/heads/otra"),
            "sha": ("sha", "1" * 40),
            "run": ("run_id", 999),
        }
        for nombre, (campo, valor) in casos.items():
            with self.subTest(caso=nombre):
                rechazado = review.authenticate_result(
                    self._metadata(**{campo: valor}),
                    self._artifact(),
                    self._contexto(),
                )
                self.assertIsInstance(rechazado, review.Rejected)

        with self.subTest(caso="attempt"):
            rechazado = review.authenticate_result(
                self._metadata(attempt=2),
                self._artifact(attempt=1),
                self._contexto(),
            )
            self.assertIsInstance(rechazado, review.Rejected)

        for nombre, over in {
            "request": {"request_id": 42},
            "pr_head": {"pr_head_sha": "1" * 40},
            "digest": {"policy_digest": "9" * 64},
        }.items():
            with self.subTest(caso=nombre):
                rechazado = review.authenticate_result(
                    self._metadata(), self._artifact(**over), self._contexto()
                )
                self.assertIsInstance(rechazado, review.Rejected)


class CoordinadorCli(unittest.TestCase):
    """T08 e2e: el comando reconcile con adaptadores falsos."""

    def _entorno(self, tmp, **over):
        base = {
            "REPO": "o/r",
            "PR_NUMBER": "1",
            "HEAD_SHA": "c" * 40,
            "BASE_SHA": "b" * 40,
            "WORKER_REF": "main",
            "BOT_LOGIN": "bot",
            "GITHUB_EVENT_NAME": "pull_request_target",
            "GITHUB_RUN_ID": "55",
        }
        base.update(over)
        return mock.patch.dict(os.environ, base, clear=False)

    def test_push_publica_y_despacha(self):
        estado = snapshot_base(generation=1)
        falso = _ComentarioFalso([{"id": 7, "body": cuerpo_v3(estado), "login": "bot"}])
        despachados = []
        with tempfile.TemporaryDirectory() as tmp:
            with self._entorno(tmp):
                with mock.patch.object(review, "ComentariosGh", return_value=falso):
                    with mock.patch.object(
                        review,
                        "despachar_worker",
                        side_effect=lambda s, **kw: despachados.append(s.id),
                    ):
                        review.cmd_reconcile(argparse.Namespace(work=tmp))
        self.assertEqual(len(falso.patches), 1)
        self.assertEqual(despachados, [1])

    def test_resultado_de_head_viejo_es_rechazado(self):
        estado = snapshot_base(generation=1)
        creada = decision_con_trabajo(estado)
        falso = _ComentarioFalso(
            [{"id": 7, "body": cuerpo_v3(creada.snapshot), "login": "bot"}]
        )
        payload = {
            "workflow_run": {
                "id": 77,
                "name": "ai-review-worker",
                "path": ".github/workflows/ai-review-worker.yml",
                "head_branch": "main",
                "head_sha": "f" * 40,
                "run_attempt": 1,
            }
        }
        artifact = {
            "request_id": 1,
            "run_id": 77,
            "attempt": 1,
            "pr_head_sha": "1" * 40,
            "policy_digest": "d" * 64,
            "target": {
                "head_sha": "1" * 40,
                "base_sha": "b" * 40,
                "policy_digest": "d" * 64,
            },
            "observaciones": [],
            "cobertura": "complete",
        }
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "result.json").write_text(json.dumps(artifact))
            payload_path = Path(tmp, "event.json")
            payload_path.write_text(json.dumps(payload))
            gh_confiable = mock.Mock(stdout=json.dumps({"sha": "f" * 40}))
            with self._entorno(
                tmp,
                GITHUB_EVENT_NAME="workflow_run",
                GITHUB_EVENT_PATH=str(payload_path),
            ):
                with mock.patch.object(review, "ComentariosGh", return_value=falso):
                    with mock.patch.object(review, "sh", return_value=gh_confiable):
                        with self.assertRaises(SystemExit) as ctx:
                            review.cmd_reconcile(argparse.Namespace(work=tmp))
        self.assertEqual(ctx.exception.code, 1)
        self.assertEqual(falso.patches, [], "rechazado: no escribe ni despacha")

    def test_run_desde_otra_ref_es_rechazado(self):
        estado = snapshot_base(generation=1)
        creada = decision_con_trabajo(estado)
        falso = _ComentarioFalso(
            [{"id": 7, "body": cuerpo_v3(creada.snapshot), "login": "bot"}]
        )
        payload = {
            "workflow_run": {
                "id": 77,
                "name": "ai-review-worker",
                "path": ".github/workflows/ai-review-worker.yml",
                "head_branch": "fork-de-atacante",
                "head_sha": "1" * 40,
                "run_attempt": 1,
            }
        }
        artifact = {
            "request_id": 1,
            "run_id": 77,
            "attempt": 1,
            "pr_head_sha": "c" * 40,
            "policy_digest": "d" * 64,
            "observaciones": [],
            "cobertura": "complete",
        }
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "result.json").write_text(json.dumps(artifact))
            payload_path = Path(tmp, "event.json")
            payload_path.write_text(json.dumps(payload))
            gh_confiable = mock.Mock(stdout=json.dumps({"sha": "f" * 40}))
            with self._entorno(
                tmp,
                GITHUB_EVENT_NAME="workflow_run",
                GITHUB_EVENT_PATH=str(payload_path),
            ):
                with mock.patch.object(review, "ComentariosGh", return_value=falso):
                    with mock.patch.object(review, "sh", return_value=gh_confiable):
                        with self.assertRaises(SystemExit) as ctx:
                            review.cmd_reconcile(argparse.Namespace(work=tmp))
        self.assertEqual(ctx.exception.code, 1)
        self.assertEqual(falso.patches, [])

    def test_resultado_vigente_se_acepta(self):
        estado = snapshot_base(generation=1)
        digest = review.digest_de_politica(review.politica_de_revision())
        target = domain.ReviewTarget(
            repository="o/r",
            pr_number=1,
            head_sha="c" * 40,
            base_sha="b" * 40,
            policy_digest=digest,
        )
        creada = domain.reconcile(
            estado, domain.RequestReview(origin=PUSH, target=target), hechos(), POLICY
        )
        falso = _ComentarioFalso(
            [{"id": 7, "body": cuerpo_v3(creada.snapshot), "login": "bot"}]
        )
        payload = {
            "workflow_run": {
                "id": 77,
                "name": "ai-review-worker",
                "path": ".github/workflows/ai-review-worker.yml",
                "head_branch": "main",
                "head_sha": "f" * 40,
                "run_attempt": 1,
            }
        }
        artifact = {
            "request_id": 1,
            "run_id": 77,
            "attempt": 1,
            "pr_head_sha": "c" * 40,
            "policy_digest": digest,
            "observaciones": [],
            "cobertura": "complete",
        }
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "result.json").write_text(json.dumps(artifact))
            payload_path = Path(tmp, "event.json")
            payload_path.write_text(json.dumps(payload))
            pr_api = mock.Mock(
                stdout=json.dumps(
                    {
                        "head": {"sha": "c" * 40},
                        "base": {"sha": "b" * 40},
                    }
                )
            )

            gh_confiable = mock.Mock(stdout=json.dumps({"sha": "f" * 40}))

            def falso_sh(*args, **kw):
                if any("commits/" in str(a) for a in args):
                    return gh_confiable
                assert any("pulls/1" in str(a) for a in args)
                return pr_api

            with self._entorno(
                tmp,
                GITHUB_EVENT_NAME="workflow_run",
                GITHUB_EVENT_PATH=str(payload_path),
            ):
                with mock.patch.object(review, "ComentariosGh", return_value=falso):
                    with mock.patch.object(review, "sh", side_effect=falso_sh):
                        review.cmd_reconcile(argparse.Namespace(work=tmp))
        self.assertEqual(len(falso.patches), 1)
        carga = domain.read_snapshot(falso.leer()[0]["body"])
        self.assertEqual(carga.snapshot.pending_requests, [])

    def _reconcile_workflow_run(self, workflow_run):
        estado = snapshot_base(generation=1)
        digest = review.digest_de_politica(review.politica_de_revision())
        target = domain.ReviewTarget(
            repository="o/r",
            pr_number=1,
            head_sha="c" * 40,
            base_sha="b" * 40,
            policy_digest=digest,
        )
        creada = domain.reconcile(
            estado, domain.RequestReview(origin=PUSH, target=target), hechos(), POLICY
        )
        falso = _ComentarioFalso(
            [{"id": 7, "body": cuerpo_v3(creada.snapshot), "login": "bot"}]
        )
        artifact = {
            "request_id": 1,
            "run_id": 77,
            "attempt": 1,
            "pr_head_sha": "c" * 40,
            "policy_digest": digest,
            "observaciones": [],
            "cobertura": "complete",
        }

        def falso_sh(*args, **kw):
            if any("commits/" in str(a) for a in args):
                return mock.Mock(stdout=json.dumps({"sha": "f" * 40}))
            return mock.Mock(
                stdout=json.dumps(
                    {"head": {"sha": "c" * 40}, "base": {"sha": "b" * 40}}
                )
            )

        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "result.json").write_text(json.dumps(artifact))
            payload_path = Path(tmp, "event.json")
            payload_path.write_text(json.dumps({"workflow_run": workflow_run}))
            with self._entorno(
                tmp,
                GITHUB_EVENT_NAME="workflow_run",
                GITHUB_EVENT_PATH=str(payload_path),
            ):
                with mock.patch.object(review, "ComentariosGh", return_value=falso):
                    with mock.patch.object(review, "sh", side_effect=falso_sh):
                        try:
                            review.cmd_reconcile(argparse.Namespace(work=tmp))
                        except SystemExit as exc:
                            return falso, exc.code
        return falso, None

    def test_workflow_run_con_run_name_identifica_el_worker_por_path(self):
        # Con run-name, "name" y "display_title" traen el título del run (el PR).
        falso, salida = self._reconcile_workflow_run(
            {
                "id": 77,
                "name": "1",
                "display_title": "1",
                "path": ".github/workflows/ai-review-worker.yml",
                "head_branch": "main",
                "head_sha": "f" * 40,
                "run_attempt": 1,
            }
        )
        self.assertIsNone(salida)
        self.assertEqual(len(falso.patches), 1)
        carga = domain.read_snapshot(falso.leer()[0]["body"])
        self.assertEqual(carga.snapshot.pending_requests, [])

    def test_workflow_run_que_imita_el_nombre_desde_otro_workflow_se_rechaza(self):
        falso, salida = self._reconcile_workflow_run(
            {
                "id": 77,
                "name": "ai-review-worker",
                "display_title": "ai-review-worker",
                "path": ".github/workflows/otro.yml",
                "head_branch": "main",
                "head_sha": "f" * 40,
                "run_attempt": 1,
            }
        )
        self.assertEqual(salida, 1)
        self.assertEqual(falso.patches, [])

    def test_sticky_legado_crea_solicitud_y_conserva_hallazgos(self):

        legado = domain.serialize_findings(
            {
                "findings": [
                    {
                        "id": "F1",
                        "file": "a.py",
                        "line": 1,
                        "severity": "High",
                        "title": "bug",
                        "state": "open",
                    }
                ],
                "next": 2,
                "seen": 4,
            }
        )
        cuerpo = f"{review.MARKER}\n{review.SHA_PREFIX}{'e' * 40} -->\n{legado}"
        falso = _ComentarioFalso([{"id": 7, "body": cuerpo, "user": "bot"}])
        despachados = []
        with tempfile.TemporaryDirectory() as tmp:
            with self._entorno(tmp):
                with mock.patch.object(review, "ComentariosGh", return_value=falso):
                    with mock.patch.object(
                        review,
                        "despachar_worker",
                        side_effect=lambda s, **kw: despachados.append(s.id),
                    ):
                        review.cmd_reconcile(argparse.Namespace(work=tmp))
        self.assertEqual(len(falso.patches), 1)
        carga = domain.read_snapshot(falso.leer()[0]["body"])
        self.assertIsInstance(carga, domain.Valid)
        self.assertEqual([r.id for r in carga.snapshot.pending_requests], [1])
        self.assertEqual(
            [f.title for f in carga.snapshot.findings],
            ["bug"],
            "los hallazgos del legado sobreviven a la migración",
        )
        self.assertEqual(carga.snapshot.command_cursor, 4)
        self.assertEqual(despachados, [1])

    def test_sticky_sin_bloque_admite_trabajo(self):
        """El banner de falla es ausencia real de memoria."""
        cuerpo = f"{review.MARKER}\n> [!CAUTION]\n> No se pudo revisar"
        falso = _ComentarioFalso([{"id": 7, "body": cuerpo, "user": "bot"}])
        despachados = []
        with tempfile.TemporaryDirectory() as tmp:
            with self._entorno(tmp):
                with mock.patch.object(review, "ComentariosGh", return_value=falso):
                    with mock.patch.object(
                        review,
                        "despachar_worker",
                        side_effect=lambda s, **kw: despachados.append(s.id),
                    ):
                        review.cmd_reconcile(argparse.Namespace(work=tmp))
        self.assertEqual(despachados, [1])

    def test_base_ausente_sin_revision_falla_alto(self):
        """Sin BASE_SHA y sin revisión en el checkpoint no hay nada que haga."""
        vacio = domain.Snapshot(
            schema=3,
            generation=1,
            revision=None,
            next_id=1,
            completion=domain.UNKNOWN,
            findings=[],
            command_cursor=0,
        )
        falso = _ComentarioFalso([{"id": 7, "body": cuerpo_v3(vacio)}])
        with tempfile.TemporaryDirectory() as tmp:
            with self._entorno(tmp, BASE_SHA=""):
                with mock.patch.object(review, "ComentariosGh", return_value=falso):
                    with self.assertRaises(SystemExit):
                        review.cmd_reconcile(argparse.Namespace(work=tmp))

    def test_base_ausente_con_revision_usa_la_persistida(self):
        """BASE_SHA vacío cae a la revisión persistida del checkpoint."""
        import review_domain as domain

        falso = _ComentarioFalso(
            [{"id": 7, "body": cuerpo_v3(snapshot_base()), "user": "bot"}]
        )
        despachados = []
        with tempfile.TemporaryDirectory() as tmp:
            with self._entorno(tmp, BASE_SHA=""):
                with mock.patch.object(review, "ComentariosGh", return_value=falso):
                    with mock.patch.object(
                        review,
                        "despachar_worker",
                        side_effect=lambda s, **kw: despachados.append(s.id),
                    ):
                        review.cmd_reconcile(argparse.Namespace(work=tmp))
        self.assertEqual(len(falso.patches), 1)
        carga = domain.read_snapshot(falso.leer()[0]["body"])
        self.assertEqual(
            carga.snapshot.revision.base_sha,
            "b" * 40,
            "la revisión persistida suple el BASE_SHA ausente",
        )


if __name__ == "__main__":
    unittest.main()
