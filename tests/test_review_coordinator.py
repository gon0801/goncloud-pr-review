import argparse
import json
import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import review  # noqa: E402
import review_coordinator
import review_document
import review_github
import review_sticky
import review_config
import review_domain as domain  # noqa: E402


def snapshot_base(generation=1, findings=(), completion=domain.UNKNOWN, schema=3):
    """Memoria de una revisión anterior (head a…a); el PR ya va en c…c."""
    return domain.Snapshot(
        schema=schema,
        generation=generation,
        revision=domain.Revision(
            base_sha="b" * 40, head_sha="a" * 40, policy_digest="d" * 64
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


class RunHistory(unittest.TestCase):
    def _run(self, run_id, pr=1, request=1, attempt=1, status="in_progress"):
        return {
            "id": run_id, "run_attempt": attempt,
            "display_title": f"PR {pr} solicitud {request}",
            "event": "workflow_dispatch",
            "path": ".github/workflows/ai-review-worker.yml",
            "head_branch": "main", "head_sha": "f" * 40,
            "status": status,
        }

    def test_mas_de_mil_runs_no_ocultan_un_worker_en_curso(self):
        other = [self._run(i + 100, pr=99) for i in range(1100)]
        wanted = self._run(10)
        pages = [
            {"total_count": 1101, "workflow_runs": (other + [wanted])[i:i + 100]}
            for i in range(0, 1101, 100)
        ]
        calls = []

        def sh(*args, **kwargs):
            calls.append((args, kwargs))
            if "/workflows/" in args[2]:
                return mock.Mock(stdout=json.dumps(pages))
            if "/actions/runs/10" in args[2]:
                return mock.Mock(stdout=json.dumps(wanted))
            return mock.Mock(stdout=json.dumps({"sha": "f" * 40}))

        request = domain.WorkRequest(id=1, kind="review", origin="push")
        state = snapshot_base()
        state.pending_requests = [request]
        state.request_count = 1
        with mock.patch.object(review_github, "sh", side_effect=sh), \
                mock.patch.object(review_coordinator, "artefacto_del_run", return_value=None):
            found = review_coordinator.cosechar(state, "o/r", 1, "main", "/tmp", None)
        self.assertEqual(found, ([], [], False))
        self.assertIn("--paginate", calls[0][0])
        self.assertIn("--slurp", calls[0][0])
        self.assertNotIn("event=", calls[0][0][2])
        self.assertEqual(calls[0][1]["timeout"], 60)
        self.assertEqual(calls[1][1]["timeout"], 15)

    def test_historial_incierto_nunca_demuestra_ausencia(self):
        run = self._run(10)
        cases = (
            "[",
            json.dumps([{"total_count": 2, "workflow_runs": [run]}]),
            json.dumps([
                {"total_count": 1, "workflow_runs": [run]},
                {"total_count": 2, "workflow_runs": []},
            ]),
            json.dumps([{"total_count": 2, "workflow_runs": [run, run]}]),
        )
        for body in cases:
            with self.subTest(body=body[:40]), \
                    mock.patch.object(review_github, "sh", return_value=mock.Mock(stdout=body)):
                history = review_coordinator.corridas_por_solicitud("o/r", 1)
                self.assertIsInstance(history, review_coordinator.RunSearchUnknown)
        with mock.patch.object(review_github, "sh", side_effect=subprocess.TimeoutExpired("gh", 60)):
            self.assertIsInstance(
                review_coordinator.corridas_por_solicitud("o/r", 1),
                review_coordinator.RunSearchUnknown,
            )
        with mock.patch.object(review_github, "sh", return_value=mock.Mock(
            stdout=json.dumps([{"total_count": 0, "workflow_runs": []}])
        )):
            self.assertEqual(
                review_coordinator.corridas_por_solicitud("o/r", 1).by_request, {}
            )

    def test_historial_desconocido_mantiene_la_solicitud_pendiente(self):
        state = snapshot_base()
        request = domain.WorkRequest(id=1, kind="review", origin="push")
        state.pending_requests = [request]
        state.request_count = 1
        with mock.patch.object(review_github, "sh", side_effect=subprocess.TimeoutExpired("gh", 60)):
            self.assertEqual(
                review_coordinator.cosechar(state, "o/r", 1, "main", "/tmp", None),
                ([], [], False),
            )

    def test_callback_viejo_no_cosecha_el_run_nuevo_en_curso(self):
        older = self._run(10, status="completed")
        newer = self._run(11, attempt=2)
        pages = [{"total_count": 2, "workflow_runs": [newer, older]}]
        calls = []

        def sh(*args, **kwargs):
            calls.append(args[2])
            if "/workflows/" in args[2]:
                return mock.Mock(stdout=json.dumps(pages))
            if args[2].endswith("/actions/runs/10"):
                return mock.Mock(stdout=json.dumps(older))
            if args[2].endswith("/actions/runs/11"):
                return mock.Mock(stdout=json.dumps(newer))
            return mock.Mock(stdout=json.dumps({"sha": "f" * 40}))

        state = snapshot_base()
        state.pending_requests = [domain.WorkRequest(id=1, kind="review", origin="push")]
        state.request_count = 1
        with mock.patch.object(review_github, "sh", side_effect=sh), \
                mock.patch.object(review_coordinator, "artefacto_del_run", return_value=None):
            found = review_coordinator.cosechar(state, "o/r", 1, "main", "/tmp", (10, 1))
        self.assertEqual(found, ([], [], False))
        self.assertTrue(any(path.endswith("/actions/runs/11") for path in calls))


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


def comando_revisar(cid):
    return {
        "cid": cid,
        "login": "jefe",
        "action": "revisar",
        "arg": "",
        "creado": "2026-10-06T10:00:00Z",
        "editado": "",
    }


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
            repetida.work_after_commit, (), "repetir el evento no despacha otra vez"
        )

    def test_comando_explicito_mismo_sha_crea_nueva_solicitud(self):
        estado = snapshot_base()
        automatica = domain.reconcile(
            estado, domain.RequestReview(origin=PUSH, target=TARGET), hechos(), POLICY
        )
        comando, trabajo, rechazos = domain.procesar_comandos(
            automatica.snapshot,
            (comando_revisar(9),),
            lambda _: "write",
            TARGET,
        )
        self.assertIsInstance(comando, domain.Snapshot)
        self.assertEqual(rechazos, ())
        self.assertEqual(
            [r.id for r in comando.pending_requests],
            [2],
            "una revisión viva por PR: la explícita reemplaza a la automática",
        )
        self.assertEqual(comando.request_count, 2)
        self.assertEqual(len(trabajo), 1)
        self.assertEqual(trabajo[0].id, 2)
        self.assertEqual(trabajo[0].origin, "comando")

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
            domain.RequestReview(
                origin=PUSH, target=domain.replace(TARGET, head_sha="e" * 40)
            ),
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

    def test_resultado_vencido_no_acredita_cobertura_ni_crea_trabajo(self):
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
        self.assertEqual(vencido.snapshot.pending_requests, [])
        self.assertEqual(
            vencido.work_after_commit, (), "el head vivo lo admite el coordinador"
        )

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

    def test_vencido_con_viva_del_target_nuevo_no_vuelve_a_despacharla(self):
        """El resultado de la revisión que reemplazó un `revisar` ya no
        encuentra solicitud: no re-despacha la viva, que ya corre."""
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
        segunda, trabajo, rechazos = domain.procesar_comandos(
            primera.snapshot,
            (comando_revisar(9),),
            lambda _: "write",
            target_nuevo,
        )
        self.assertEqual(rechazos, ())
        self.assertEqual([r.id for r in segunda.pending_requests], [2])
        self.assertEqual([r.id for r in trabajo], [2])
        revision_nueva = domain.Revision(
            base_sha="b" * 40, head_sha="e" * 40, policy_digest="d" * 64
        )
        vencido = domain.reconcile(
            segunda,
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
        self.assertIsInstance(vencido, domain.Keep)

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
        acreditados, cobertura unknown y la solicitud sale sin crear trabajo."""
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
                self.assertEqual(vencido.snapshot.pending_requests, [])
                self.assertEqual(vencido.work_after_commit, ())

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
            domain.PARTIAL,
            "un bloque con formas inválidas no acredita cobertura completa",
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


class _ComentarioFalso:
    """GitHub falso: aplica la escritura y puede perder la respuesta."""

    def __init__(self, comentarios):
        self.comentarios = list(comentarios)
        self.patches = []
        self.creaciones = []
        self.perder_patch = False
        self.perder_creacion = False

    def leer(self):
        return [
            {**c, "user": c.get("user", c.get("login"))}
            for c in self.comentarios
        ]

    def parchar(self, cid, body):
        self.patches.append((cid, body))
        for c in self.comentarios:
            if c["id"] == cid:
                c["body"] = body
        if self.perder_patch:
            raise OSError("respuesta de PATCH perdida")

    def crear(self, body):
        self.creaciones.append(body)
        cid = 100 + len(self.creaciones)
        self.comentarios.append({"id": cid, "body": body, "user": "bot"})
        if self.perder_creacion:
            raise OSError("respuesta de POST perdida")


def cuerpo_v3(snapshot):
    return f"{review_sticky.MARKER}\n{domain.encode_snapshot(snapshot)}"


def decision_con_trabajo(estado):
    creada = domain.reconcile(
        estado, domain.RequestReview(origin=PUSH, target=TARGET), hechos(), POLICY
    )
    return creada


def preparar_decision(decision, observado=None):
    previous = review_document.read_document(observado["body"] if observado else "")
    return review_document.prepare_publication(
        review_document.update_presentation(previous, decision.snapshot),
        domain.StorageBudget(), (),
    )


class CheckpointConservaLaCabecera(unittest.TestCase):
    """Ronda 3 del piloto (#91): el checkpoint de admisión escribía el bloque
    antes de sha=/completion=; reviewed_completion exige esas líneas tras el
    marcador y el worker leía la revisión previa como incompleta."""

    HEAD = "e" * 40

    def _con_revision(self, estado):
        return (
            f"{review_sticky.MARKER}\n{review_sticky.SHA_PREFIX}{self.HEAD} -->\n"
            f"{review_sticky.COMPLETION_PREFIX}{self.HEAD}:complete -->\n"
            f"{domain.encode_snapshot(estado).block}\n\n"
            "### Revisión automática · DeepSeek V4.1 Flash · OpenCode Go · eeeeeee"
        )

    def test_la_admision_conserva_sha_y_completion_tras_el_marcador(self):
        estado = snapshot_base(generation=1)
        falso = _ComentarioFalso([{"id": 7, "body": self._con_revision(estado)}])
        decision = decision_con_trabajo(estado)
        document = review_document.read_document(falso.leer()[0]["body"])
        prepared = review_document.prepare_publication(
            review_document.update_presentation(document, decision.snapshot),
            domain.StorageBudget(), (),
        )
        recibo = review_github.publish_checkpoint(
            prepared, falso.leer()[0], falso, work_after_commit=decision.work_after_commit,
        )
        self.assertIsInstance(recibo, review_github.PublishReceipt)
        cuerpo = falso.patches[-1][1]
        self.assertEqual(
            cuerpo.split("\n")[:3],
            [
                review_sticky.MARKER,
                f"{review_sticky.SHA_PREFIX}{self.HEAD} -->",
                f"{review_sticky.COMPLETION_PREFIX}{self.HEAD}:complete -->",
            ],
        )
        self.assertEqual(
            review_sticky.prev_de_memoria(cuerpo)["completion"],
            "complete",
            "el worker del siguiente push no ve la revisión previa como incompleta",
        )
        recarga = domain.read_snapshot(cuerpo)
        self.assertIsInstance(recarga, domain.Valid)
        self.assertEqual(recarga.snapshot.generation, decision.snapshot.generation)
        self.assertIn("OpenCode Go · eeeeeee", review_document.read_document(cuerpo).historical.text)

    def test_el_aviso_de_fallo_conserva_sha_y_completion_tras_el_marcador(self):
        estado = snapshot_base(generation=1)
        bloque = domain.encode_snapshot(estado).block
        previous = review_document.read_document(self._con_revision(estado))
        document = replace(
            previous,
            failure=review_document.FailureView(
                "f" * 40, "worker-failed",
                review_document.SafeProse("el proveedor no respondió"),
            ),
        )
        cuerpo = review_document.prepare_publication(
            document, domain.StorageBudget(), (),
        ).body
        self.assertEqual(
            cuerpo.split("\n")[:4],
            [
                review_sticky.MARKER,
                f"{review_sticky.SHA_PREFIX}{self.HEAD} -->",
                f"{review_sticky.COMPLETION_PREFIX}{self.HEAD}:complete -->",
                bloque,
            ],
        )
        self.assertIn("No se pudo revisar el commit fffffff", cuerpo)
        self.assertEqual(review_sticky.reviewed_completion(cuerpo), "complete")


class CheckpointRecovery(unittest.TestCase):
    """T08: recuperar escrituras inciertas sin repetir efectos."""

    def test_patch_response_lost(self):
        estado = snapshot_base(generation=1)
        falso = _ComentarioFalso([{"id": 7, "body": cuerpo_v3(estado)}])
        decision = decision_con_trabajo(estado)
        falso.perder_patch = True
        preparado = preparar_decision(decision, falso.leer()[0])
        resultado = review_github.publish_checkpoint(
            preparado, falso.leer()[0], falso,
            work_after_commit=decision.work_after_commit,
        )
        self.assertIsInstance(resultado, review_github.Unconfirmed)

        observado = falso.leer()[0]
        confirmado = review_github.publish_checkpoint(
            preparado, observado, falso,
            work_after_commit=decision.work_after_commit,
        )
        self.assertIsInstance(confirmado, review_github.PublishReceipt)
        self.assertEqual(len(falso.patches), 1, "no reaplica el PATCH ya aplicado")
        self.assertEqual(confirmado.generacion, decision.snapshot.generation)
        self.assertEqual(len(confirmado.trabajo), 1)

    def test_caida_antes_de_dispatch_y_recuperacion(self):
        estado = snapshot_base(generation=1)
        falso = _ComentarioFalso([{"id": 7, "body": cuerpo_v3(estado)}])
        decision = decision_con_trabajo(estado)
        recibo = review_github.publish_checkpoint(
            preparar_decision(decision, falso.leer()[0]), falso.leer()[0], falso,
            work_after_commit=decision.work_after_commit,
        )
        self.assertIsInstance(recibo, review_github.PublishReceipt)

        # caída antes de despachar: el próximo evento relee y recupera el trabajo
        despachados = []
        review_coordinator.dispatch_confirmed(recibo, despachar=despachados.append)
        self.assertEqual([s.id for s in despachados], [1])

    def test_dispatch_incierto_y_artifact_vencido(self):
        estado = snapshot_base(generation=1)
        falso = _ComentarioFalso([{"id": 7, "body": cuerpo_v3(estado)}])
        decision = decision_con_trabajo(estado)
        recibo = review_github.publish_checkpoint(
            preparar_decision(decision, falso.leer()[0]), falso.leer()[0], falso,
            work_after_commit=decision.work_after_commit,
        )

        intentos = []

        def despacho_incierto(solicitud):
            intentos.append(solicitud.id)
            if len(intentos) == 1:
                raise RuntimeError("dispatch incierto")

        with self.assertRaises(RuntimeError):
            review_coordinator.dispatch_confirmed(recibo, despachar=despacho_incierto)
        review_coordinator.dispatch_confirmed(recibo, despachar=despacho_incierto)
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
        resultado = review_coordinator.authenticate_result(vencido, artifact, request)
        self.assertIsInstance(resultado, review_coordinator.Rejected)

    def test_post_incierto_y_duplicados(self):
        estado = snapshot_base(generation=1)
        falso = _ComentarioFalso([])
        decision = decision_con_trabajo(estado)
        falso.perder_creacion = True
        preparado = preparar_decision(decision)
        resultado = review_github.publish_checkpoint(
            preparado, None, falso, login="bot",
            work_after_commit=decision.work_after_commit,
        )
        self.assertIsInstance(resultado, review_github.Unconfirmed)
        confirmado = review_github.publish_checkpoint(
            preparado, falso.leer()[0], falso, login="bot",
            work_after_commit=decision.work_after_commit,
        )
        self.assertIsInstance(confirmado, review_github.PublishReceipt)
        self.assertEqual(len(falso.creaciones), 1, "no crea otro comentario")

        duplicados = _ComentarioFalso(
            [
                {"id": 1, "body": cuerpo_v3(estado)},
                {"id": 2, "body": cuerpo_v3(estado)},
            ]
        )
        detenido = review_github.publish_checkpoint(
            preparar_decision(decision, duplicados.leer()[0]),
            duplicados.leer()[0], duplicados,
            work_after_commit=decision.work_after_commit,
        )
        self.assertIsInstance(detenido, review_github.Unconfirmed)
        self.assertEqual(duplicados.patches, [])
        self.assertEqual(duplicados.creaciones, [])

    def test_publish_reemplaza_el_primer_bloque_del_sticky(self):
        estado = snapshot_base(generation=1)
        decision = decision_con_trabajo(estado)
        citado = cuerpo_v3(snapshot_base(generation=9))
        cuerpo = f"{cuerpo_v3(estado)}\n\nLa prosa cita un bloque del PR:\n{citado}\n"
        falso = _ComentarioFalso([{"id": 7, "body": cuerpo}])
        resultado = review_github.publish_checkpoint(
            preparar_decision(decision, falso.leer()[0]),
            falso.leer()[0], falso,
            work_after_commit=decision.work_after_commit,
        )
        self.assertIsInstance(resultado, review_github.PublishReceipt)
        self.assertEqual(len(falso.patches), 1)
        cuerpo_nuevo = falso.leer()[0]["body"]
        carga = review_document.read_document(cuerpo_nuevo)
        self.assertEqual(
            carga.memory.generation,
            decision.snapshot.generation,
            "el checkpoint nuevo gobierna (primer bloque)",
        )
        self.assertIn("La prosa cita un bloque del PR", carga.historical.text)
        self.assertIn("&lt;!-- ai-review:findings=", carga.historical.text)

    def test_forma_real_de_comentarios_no_crashea(self):
        """CodeRabbit r1: fetch_all_comments trae user como texto."""
        estado = snapshot_base(generation=1)
        decision = decision_con_trabajo(estado)
        falso = _ComentarioFalso([{"id": 7, "body": cuerpo_v3(estado), "user": "bot"}])
        resultado = review_github.publish_checkpoint(
            preparar_decision(decision, falso.leer()[0]),
            falso.leer()[0], falso, login="bot",
            work_after_commit=decision.work_after_commit,
        )
        self.assertIsInstance(resultado, review_github.PublishReceipt)

    def test_otros_logins_no_cuentan_como_duplicados(self):
        estado = snapshot_base(generation=1)
        decision = decision_con_trabajo(estado)
        falso = _ComentarioFalso(
            [
                {"id": 1, "body": cuerpo_v3(estado), "login": "bot"},
                {"id": 2, "body": cuerpo_v3(estado), "login": "intruso"},
            ]
        )
        resultado = review_github.publish_checkpoint(
            preparar_decision(decision, falso.leer()[0]),
            falso.leer()[0], falso, login="bot",
            work_after_commit=decision.work_after_commit,
        )
        self.assertIsInstance(resultado, review_github.PublishReceipt)

        dos_del_bot = _ComentarioFalso(
            [
                {"id": 1, "body": cuerpo_v3(estado), "login": "bot"},
                {"id": 2, "body": cuerpo_v3(estado), "login": "bot"},
            ]
        )
        detenido = review_github.publish_checkpoint(
            preparar_decision(decision, dos_del_bot.leer()[0]),
            dos_del_bot.leer()[0], dos_del_bot, login="bot",
            work_after_commit=decision.work_after_commit,
        )
        self.assertIsInstance(detenido, review_github.Unconfirmed)
        self.assertEqual(dos_del_bot.patches, [])

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
        request = review_coordinator.request_de_solicitud(
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
        rechazado = review_coordinator.authenticate_result(metadata, artifact, request)
        self.assertIsInstance(rechazado, review_coordinator.Rejected)


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
        ok = review_coordinator.authenticate_result(
            self._metadata(), self._artifact(), self._contexto()
        )
        self.assertIsInstance(ok, review_coordinator.AuthenticatedResult)
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
                rechazado = review_coordinator.authenticate_result(
                    self._metadata(**{campo: valor}),
                    self._artifact(),
                    self._contexto(),
                )
                self.assertIsInstance(rechazado, review_coordinator.Rejected)

        with self.subTest(caso="attempt"):
            rechazado = review_coordinator.authenticate_result(
                self._metadata(attempt=2),
                self._artifact(attempt=1),
                self._contexto(),
            )
            self.assertIsInstance(rechazado, review_coordinator.Rejected)

        for nombre, over in {
            "request": {"request_id": 42},
            "pr_head": {"pr_head_sha": "1" * 40},
            "digest": {"policy_digest": "9" * 64},
        }.items():
            with self.subTest(caso=nombre):
                rechazado = review_coordinator.authenticate_result(
                    self._metadata(), self._artifact(**over), self._contexto()
                )
                self.assertIsInstance(rechazado, review_coordinator.Rejected)


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
                with mock.patch.object(review_coordinator, "ComentariosGh", return_value=falso):
                    with mock.patch.object(
                        review_coordinator,
                        "despachar_worker",
                        side_effect=lambda s, **kw: despachados.append(s.id),
                    ):
                        review_coordinator.cmd_reconcile(argparse.Namespace(work=tmp))
        self.assertEqual(len(falso.patches), 1)
        self.assertEqual(despachados, [1])

    def _reconciliar_aviso(self, falso, run, artifact):
        """El worker `run` avisó y dejó `artifact`; devuelve el código de salida."""
        corrida = {
            "display_title": f"PR 1 solicitud {artifact['request_id']}",
            "event": "workflow_dispatch",
            "path": ".github/workflows/ai-review-worker.yml",
            "head_branch": "main",
            "head_sha": "f" * 40,
            "run_attempt": 1,
            "status": "completed",
            **run,
        }
        nombre = (
            f"ai-review-result-request-{artifact['request_id']}"
            f"-run-{corrida['id']}-attempt-1"
        )

        def falso_sh(*args, **kw):
            if args[:3] == ("gh", "run", "download"):
                destino = Path(args[args.index("--dir") + 1])
                destino.mkdir(parents=True)
                (destino / "result.json").write_text(json.dumps(artifact))
                return mock.Mock(stdout="")
            ruta = args[2]
            if "commits/" in ruta:
                return mock.Mock(stdout=json.dumps({"sha": "f" * 40}))
            if "/compare/" in ruta:
                return mock.Mock(stdout=json.dumps({"status": "diverged"}))
            if "/actions/workflows/ai-review-worker.yml/runs" in ruta:
                return mock.Mock(stdout=json.dumps([{"total_count": 1, "workflow_runs": [corrida]}]))
            if "/actions/runs/" in ruta and "/artifacts" not in ruta:
                return mock.Mock(stdout=json.dumps(corrida))
            if "/artifacts" in ruta:
                return mock.Mock(stdout=json.dumps({"artifacts": [{"name": nombre}]}))
            assert ruta.endswith("pulls/1"), args
            vivo = {"head": {"sha": "c" * 40}, "base": {"sha": "b" * 40}}
            return mock.Mock(stdout=json.dumps(vivo))

        with tempfile.TemporaryDirectory() as tmp:
            with self._entorno(
                tmp,
                GITHUB_EVENT_NAME="workflow_dispatch",
                WORKER_RUN_ID=str(corrida["id"]),
                WORKER_ATTEMPT="1",
            ):
                with mock.patch.object(review_coordinator, "ComentariosGh", return_value=falso):
                    with mock.patch.object(review_github, "sh", side_effect=falso_sh):
                        try:
                            review_coordinator.cmd_reconcile(argparse.Namespace(work=tmp))
                        except SystemExit as exc:
                            return exc.code
        return None

    def _pendiente_vigente(self):
        digest = review.digest_de_politica(review_config.politica_de_revision())
        target = domain.ReviewTarget(
            repository="o/r",
            pr_number=1,
            head_sha="c" * 40,
            base_sha="b" * 40,
            policy_digest=digest,
        )
        creada = domain.reconcile(
            snapshot_base(generation=1),
            domain.RequestReview(origin=PUSH, target=target),
            hechos(),
            POLICY,
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
        return falso, artifact

    def test_resultado_de_head_viejo_es_rechazado(self):
        falso, artifact = self._pendiente_vigente()
        artifact["pr_head_sha"] = "1" * 40
        salida = self._reconciliar_aviso(falso, {"id": 77}, artifact)
        self.assertEqual(salida, 1)
        self.assertEqual(falso.patches, [], "rechazado: no escribe ni despacha")

    def test_run_desde_otra_ref_es_rechazado(self):
        falso, artifact = self._pendiente_vigente()
        run = {"id": 77, "head_branch": "fork-de-atacante", "head_sha": "1" * 40}
        self.assertEqual(self._reconciliar_aviso(falso, run, artifact), 1)
        self.assertEqual(falso.patches, [])

    def test_resultado_vigente_se_acepta(self):
        falso, artifact = self._pendiente_vigente()
        self.assertIsNone(self._reconciliar_aviso(falso, {"id": 77}, artifact))
        self.assertEqual(len(falso.patches), 1)
        carga = domain.read_snapshot(falso.leer()[0]["body"])
        self.assertEqual(carga.snapshot.pending_requests, [])

    def test_el_worker_se_identifica_por_el_path_y_no_por_el_nombre(self):
        falso, artifact = self._pendiente_vigente()
        imitado = {
            "id": 77,
            "name": "ai-review-worker",
            "path": ".github/workflows/otro.yml",
        }
        self.assertEqual(self._reconciliar_aviso(falso, imitado, artifact), 1)
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
        cuerpo = f"{review_sticky.MARKER}\n{review_sticky.SHA_PREFIX}{'e' * 40} -->\n{legado}"
        falso = _ComentarioFalso([{"id": 7, "body": cuerpo, "user": "bot"}])
        despachados = []
        with tempfile.TemporaryDirectory() as tmp:
            with self._entorno(tmp):
                with mock.patch.object(review_coordinator, "ComentariosGh", return_value=falso):
                    with mock.patch.object(
                        review_coordinator,
                        "despachar_worker",
                        side_effect=lambda s, **kw: despachados.append(s.id),
                    ):
                        review_coordinator.cmd_reconcile(argparse.Namespace(work=tmp))
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
        cuerpo = f"{review_sticky.MARKER}\n> [!CAUTION]\n> No se pudo revisar"
        falso = _ComentarioFalso([{"id": 7, "body": cuerpo, "user": "bot"}])
        despachados = []
        with tempfile.TemporaryDirectory() as tmp:
            with self._entorno(tmp):
                with mock.patch.object(review_coordinator, "ComentariosGh", return_value=falso):
                    with mock.patch.object(
                        review_coordinator,
                        "despachar_worker",
                        side_effect=lambda s, **kw: despachados.append(s.id),
                    ):
                        review_coordinator.cmd_reconcile(argparse.Namespace(work=tmp))
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
                with mock.patch.object(review_coordinator, "ComentariosGh", return_value=falso):
                    with self.assertRaises(SystemExit):
                        review_coordinator.cmd_reconcile(argparse.Namespace(work=tmp))

    def test_base_ausente_con_revision_usa_la_persistida(self):
        """BASE_SHA vacío cae a la revisión persistida del checkpoint."""
        import review_domain as domain

        falso = _ComentarioFalso(
            [{"id": 7, "body": cuerpo_v3(snapshot_base()), "user": "bot"}]
        )
        despachados = []
        with tempfile.TemporaryDirectory() as tmp:
            with self._entorno(tmp, BASE_SHA=""):
                with mock.patch.object(review_coordinator, "ComentariosGh", return_value=falso):
                    with mock.patch.object(
                        review_coordinator,
                        "despachar_worker",
                        side_effect=lambda s, **kw: despachados.append(s.id),
                    ):
                        review_coordinator.cmd_reconcile(argparse.Namespace(work=tmp))
        self.assertEqual(len(falso.patches), 1)
        carga = domain.read_snapshot(falso.leer()[0]["body"])
        self.assertEqual(
            carga.snapshot.revision.base_sha,
            "b" * 40,
            "la revisión persistida suple el BASE_SHA ausente",
        )


if __name__ == "__main__":
    unittest.main()
