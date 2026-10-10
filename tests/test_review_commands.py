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

PUSH = domain.Origin(kind="push")
TARGET = domain.ReviewTarget(
    repository="o/r",
    pr_number=1,
    head_sha="c" * 40,
    base_sha="b" * 40,
    policy_digest="d" * 64,
)
POLICY = domain.ReviewPolicy()


def comando(
    cid, action, arg="", login="jefe", creado="2026-10-06T10:00:00Z", editado=""
):
    return {
        "cid": cid,
        "login": login,
        "action": action,
        "arg": arg,
        "creado": creado,
        "editado": editado,
    }


def permisos_ok(login):
    return "write"


def hallazgos_base():
    return [
        domain.Finding(
            id="F1",
            title="Fuga A",
            severity="High",
            status=domain.StatusOpen(),
            primary_anchor=domain.AnchorLegacy(path="a.py", line=1),
        ),
        domain.Finding(
            id="F2",
            title="Fuga B",
            severity="Low",
            status=domain.StatusOpen(),
            primary_anchor=domain.AnchorLegacy(path="b.py", line=2),
        ),
    ]


def snapshot_base():
    return domain.Snapshot(
        schema=3,
        generation=2,
        revision=domain.Revision(
            base_sha="b" * 40, head_sha="c" * 40, policy_digest="d" * 64
        ),
        next_id=3,
        completion=domain.PARTIAL,
        findings=hallazgos_base(),
        command_cursor=0,
    )


def efectos(snapshot):
    return {r.command_id: r.effect for r in snapshot.receipts}


class CommandAdmission(unittest.TestCase):
    def test_permission_failure_holds_cursor(self):
        estado = snapshot_base()

        def permiso_roto(login):
            raise RuntimeError("API de permisos caída")

        retenido = domain.procesar_comandos(
            estado,
            (comando(5, "descartar", "F1"), comando(6, "descartar", "F2")),
            permiso_roto,
            TARGET,
        )
        self.assertEqual(retenido[0].command_cursor, 0, "el fallo no consume")
        self.assertEqual(
            [type(f.status).__name__ for f in retenido[0].findings],
            ["StatusOpen", "StatusOpen"],
        )
        self.assertEqual(retenido[0].receipts, ())

        aplicado = domain.procesar_comandos(
            estado,
            (comando(5, "descartar", "F1"), comando(6, "descartar", "F2")),
            permisos_ok,
            TARGET,
        )
        self.assertIsInstance(aplicado, tuple)
        self.assertEqual(aplicado[0].command_cursor, 6)
        self.assertEqual(
            [type(f.status).__name__ for f in aplicado[0].findings],
            ["StatusDismissed", "StatusDismissed"],
        )

    def test_permiso_denegado_rechazo_visible(self):
        def solo_lectura(login):
            return "read"

        out = domain.procesar_comandos(
            snapshot_base(),
            (comando(5, "descartar", "F1"),),
            solo_lectura,
            TARGET,
        )
        snapshot, work, rechazos = out
        self.assertEqual(snapshot.command_cursor, 5, "la denegación confirmada consume")
        self.assertEqual(
            [type(f.status).__name__ for f in snapshot.findings],
            ["StatusOpen", "StatusOpen"],
        )
        self.assertIn(5, efectos(snapshot))
        self.assertIn("permiso", efectos(snapshot)[5])
        self.assertEqual(len(rechazos), 1)
        self.assertEqual(rechazos[0][0], 5)

    def test_id_inexistente_rechazo_visible(self):
        out = domain.procesar_comandos(
            snapshot_base(),
            (comando(5, "descartar", "F99"),),
            permisos_ok,
            TARGET,
        )
        snapshot, work, rechazos = out
        self.assertEqual(snapshot.command_cursor, 5)
        self.assertEqual(
            [f.id for f in snapshot.findings],
            ["F1", "F2"],
            "F99 no existe: nada se descarta",
        )
        self.assertIn("F99", efectos(snapshot)[5])

    def test_editado_antes_de_admision_rechazado(self):
        out = domain.procesar_comandos(
            snapshot_base(),
            (
                comando(
                    5,
                    "descartar",
                    "F1",
                    creado="2026-10-06T10:00:00Z",
                    editado="2026-10-06T10:01:00Z",
                ),
            ),
            permisos_ok,
            TARGET,
        )
        snapshot, work, rechazos = out
        self.assertEqual(snapshot.command_cursor, 5)
        self.assertEqual(
            [type(f.status).__name__ for f in snapshot.findings],
            ["StatusOpen", "StatusOpen"],
        )
        self.assertIn("editado", efectos(snapshot)[5])

    def test_edicion_posterior_conserva_decision_original(self):
        primera = domain.procesar_comandos(
            snapshot_base(),
            (comando(5, "descartar", "F1"),),
            permisos_ok,
            TARGET,
        )
        snapshot, _, _ = primera
        self.assertEqual(
            [type(f.status).__name__ for f in snapshot.findings],
            ["StatusDismissed", "StatusOpen"],
        )
        recibos_originales = snapshot.receipts

        editado = (
            comando(
                5,
                "descartar",
                "F2",
                creado="2026-10-06T10:00:00Z",
                editado="2026-10-06T11:00:00Z",
            ),
        )
        reprocesado = domain.procesar_comandos(snapshot, editado, permisos_ok, TARGET)
        snapshot2, _, _ = reprocesado
        self.assertEqual(snapshot2.command_cursor, 5, "ya consumido: no se reprocesa")
        self.assertEqual(
            [type(f.status).__name__ for f in snapshot2.findings],
            ["StatusDismissed", "StatusOpen"],
            "la edición posterior no altera la decisión",
        )
        self.assertEqual(snapshot2.receipts, recibos_originales)

    def test_dismiss_all_captures_current_ids(self):
        primera = domain.procesar_comandos(
            snapshot_base(),
            (comando(5, "descartar", "todo"),),
            permisos_ok,
            TARGET,
        )
        snapshot, _, _ = primera
        self.assertEqual(
            [type(f.status).__name__ for f in snapshot.findings],
            ["StatusDismissed", "StatusDismissed"],
        )
        self.assertIn("descartar todo", efectos(snapshot)[5])

        # un resultado pendiente incorporado DESPUÉS llega con un hallazgo nuevo
        observacion = domain.Observation(
            title="Fuga C nueva",
            severity="Medium",
            primary_anchor=domain.AnchorLegacy(path="c.py", line=3),
            claim=domain.OPEN,
        )
        report = domain.validar_reporte(
            [observacion], domain.PARTIAL, domain.RepositoryFacts()
        )
        decision = domain.accept_report(
            snapshot,
            domain.ReviewPlan(policy=POLICY),
            report,
        )
        self.assertIsInstance(decision, domain.Replace)
        f3 = decision.snapshot.findings[-1]
        self.assertEqual(f3.title, "Fuga C nueva")
        self.assertIsInstance(
            f3.status,
            domain.StatusOpen,
            "descartar todo capturó los IDs actuales: el nuevo sigue abierto",
        )


class CommandExecution(unittest.TestCase):
    def test_reauthorize_before_model(self):
        """B2 r2: el permiso del solicitante se re-consulta antes de despachar."""
        out = domain.procesar_comandos(
            snapshot_base(),
            (comando(5, "revisar", login="jefe"),),
            permisos_ok,
            TARGET,
        )
        snapshot, work, _ = out
        self.assertEqual(work[0].solicitante, "jefe")

        def revocado(login):
            return "read"

        snapshot_ra, despachables, terminadas = domain.reautorizar(
            snapshot, work, revocado
        )
        self.assertEqual(
            despachables,
            (),
            "permiso revocado: cero despachos y cero llamadas al proveedor",
        )
        self.assertEqual(len(terminadas), 1)
        self.assertEqual(terminadas[0].state, "finished")
        self.assertIn("permiso revocado", terminadas[0].motivo)
        self.assertIn("jefe", terminadas[0].motivo, "rechazo terminal visible")
        finalizada = {r.id: r for r in snapshot_ra.pending_requests}[1]
        self.assertEqual(finalizada.state, "finished")
        self.assertEqual(finalizada.motivo, terminadas[0].motivo)

        # el modo real de fallo de collaborator_permission es devolver None
        def consulta_caída(login):
            return None

        snapshot_espera, despachables_espera, terminadas_espera = domain.reautorizar(
            snapshot, work, consulta_caída
        )
        self.assertIsNone(snapshot_espera, "sin confirmación: nada cambia")
        self.assertEqual(
            despachables_espera,
            (),
            "sin confirmación: la solicitud queda pendiente, no despachable",
        )
        self.assertEqual(terminadas_espera, ())

        # permiso vigente: pasa al despacho
        snapshot_ok, despachables_ok, terminadas_ok = domain.reautorizar(
            snapshot, work, permisos_ok
        )
        self.assertIsNone(snapshot_ok)
        self.assertEqual([s.id for s in despachables_ok], [1])
        self.assertEqual(terminadas_ok, ())

    def test_dismiss_all_con_abiertos_deja_un_solo_recibo(self):
        """B1: un recibo por comando; el checkpoint sigue legal."""
        out = domain.procesar_comandos(
            snapshot_base(),
            (comando(10, "descartar", "todo"),),
            permisos_ok,
            TARGET,
        )
        snapshot, _, _ = out
        self.assertEqual(
            [f.status.__class__.__name__ for f in snapshot.findings],
            ["StatusDismissed", "StatusDismissed"],
        )
        # lista exacta de recibos, no un dict por id (ocultaría duplicados)
        self.assertEqual(
            [(r.command_id, r.effect) for r in snapshot.receipts],
            [(10, "descartar todo: F1, F2")],
        )
        encoded = domain.encode_snapshot(snapshot, domain.StorageBudget())
        self.assertIsInstance(encoded, domain.EncodedCheckpoint)
        carga = domain.read_snapshot(encoded.block)
        self.assertIsInstance(carga, domain.Valid)
        self.assertEqual(
            [(r.command_id, r.effect) for r in carga.snapshot.receipts],
            [(10, "descartar todo: F1, F2")],
        )

    def test_explicacion_vigente_y_antigua(self):
        snapshot = snapshot_base()
        digest_f1 = domain.digest_de_hallazgo(snapshot.findings[0])
        out = domain.procesar_comandos(
            snapshot,
            (comando(5, "explicar", "F1"),),
            permisos_ok,
            TARGET,
        )
        nuevo, work, rechazos = out
        self.assertEqual(rechazos, ())
        self.assertEqual(len(work), 1)
        solicitud = work[0]
        self.assertEqual(solicitud.kind, "explain")
        self.assertEqual(solicitud.finding_id, "F1")
        self.assertEqual(solicitud.digest, digest_f1)
        self.assertEqual(solicitud.target["head_sha"], "c" * 40)

        # el resultado de la explicación no cambia hallazgos
        entregada = domain.reconcile(
            nuevo,
            domain.ReportReady(
                origin=domain.Origin(kind="re-run", run_id=11),
                request_id=1,
                run_id=11,
                attempt=1,
                observaciones=(),
                cobertura=domain.UNKNOWN,
            ),
            hechos_vigentes(),
            POLICY,
        )
        self.assertIsInstance(entregada, domain.Commit)
        self.assertEqual(
            [f.title for f in entregada.snapshot.findings],
            ["Fuga A", "Fuga B"],
            "la explicación no cambia hallazgos",
        )
        self.assertEqual(entregada.snapshot.pending_requests, [])

        # explicación antigua: el target quedó atrás; no acredita y la cola
        # queda con la explicación para el target vigente (identificable)
        con_vieja = domain.reconcile(
            snapshot_base(),
            domain.AuthorizedCommand(
                origin=domain.Origin(kind="comando", comment_id=7),
                target=TARGET,
                action="explicar",
                finding_id="F1",
            ),
            domain.RepositoryFacts(),
            POLICY,
        )
        self.assertEqual(con_vieja.snapshot.pending_requests[0].kind, "explain")
        revision_nueva = domain.Revision(
            base_sha="b" * 40, head_sha="e" * 40, policy_digest="d" * 64
        )
        salida = domain.reconcile(
            con_vieja.snapshot,
            domain.ReportReady(
                origin=domain.Origin(kind="re-run", run_id=21),
                request_id=1,
                run_id=21,
                attempt=1,
                observaciones=(),
                cobertura=domain.UNKNOWN,
            ),
            domain.RepositoryFacts(revision=revision_nueva),
            POLICY,
        )
        self.assertIsInstance(salida, domain.Commit)
        self.assertEqual(
            [f.title for f in salida.snapshot.findings],
            ["Fuga A", "Fuga B"],
            "la explicación antigua no acredita ni cambia hallazgos",
        )
        vigente = salida.snapshot.pending_requests[0]
        self.assertEqual(vigente.target["head_sha"], "e" * 40)


def hechos_vigentes():
    return domain.RepositoryFacts(
        revision=domain.Revision(
            base_sha="b" * 40, head_sha="c" * 40, policy_digest="d" * 64
        ),
        delta_calculado=True,
    )


class CoordinadorComandos(unittest.TestCase):
    def _entorno(self, **over):
        base = {
            "REPO": "o/r",
            "PR_NUMBER": "1",
            "HEAD_SHA": "c" * 40,
            "BASE_SHA": "b" * 40,
            "WORKER_REF": "main",
            "BOT_LOGIN": "bot",
            "GITHUB_EVENT_NAME": "issue_comment.created",
            "GITHUB_RUN_ID": "55",
        }
        base.update(over)
        return mock.patch.dict(os.environ, base, clear=False)

    def _falso(self, cuerpo_v3, comandos):
        comentarios = [{"id": 7, "body": cuerpo_v3, "user": "bot"}]
        for cid, body in comandos:
            comentarios.append(
                {
                    "id": cid,
                    "body": body,
                    "user": "jefe",
                    "created_at": "2026-10-06T10:00:00Z",
                    "updated_at": "2026-10-06T10:00:00Z",
                }
            )

        class Adaptador:
            def __init__(self):
                self.comentarios = comentarios
                self.patches = []

            def leer(self):
                return [dict(c) for c in self.comentarios]

            def parchar(self, cid, body):
                self.patches.append((cid, body))
                for c in self.comentarios:
                    if c["id"] == cid:
                        c["body"] = body

            def crear(self, body):
                self.comentarios.append({"id": 999, "body": body, "user": "bot"})

        return Adaptador()

    def test_evento_real_issue_comment_con_action_created(self):
        """CodeRabbit: GITHUB_EVENT_NAME es issue_comment; el tipo va en action."""
        estado = domain.Snapshot(
            schema=3,
            generation=2,
            revision=domain.Revision(
                base_sha="b" * 40, head_sha="c" * 40, policy_digest="d" * 64
            ),
            next_id=3,
            completion=domain.PARTIAL,
            findings=[
                domain.Finding(
                    id="F1",
                    title="Fuga A",
                    severity="High",
                    status=domain.StatusOpen(),
                    primary_anchor=domain.AnchorLegacy(path="a.py", line=1),
                )
            ],
            command_cursor=0,
        )
        falso = self._falso(
            f"{review.MARKER}\n{domain.encode_snapshot(estado)}",
            [(5, "ai-review: descartar F1")],
        )
        with tempfile.TemporaryDirectory() as tmp:
            payload_path = Path(tmp, "event.json")
            payload_path.write_text(json.dumps({"action": "created"}))
            with self._entorno(
                GITHUB_EVENT_NAME="issue_comment",
                GITHUB_EVENT_PATH=str(payload_path),
            ):
                pr_api = mock.Mock(
                    stdout=json.dumps(
                        {"head": {"sha": "c" * 40}, "base": {"sha": "b" * 40}}
                    )
                )
                with mock.patch.object(review, "ComentariosGh", return_value=falso):
                    with mock.patch.object(review, "sh", return_value=pr_api):
                        with mock.patch.object(
                            review,
                            "collaborator_permission",
                            return_value="write",
                        ):
                            review.cmd_reconcile(argparse.Namespace(work=tmp))
            self.assertEqual(
                len(falso.patches), 1, "el evento real de GitHub admite comandos"
            )

            # edited/deleted no admite comandos
            falso2 = self._falso(
                f"{review.MARKER}\n{domain.encode_snapshot(estado)}",
                [(5, "ai-review: descartar F1")],
            )
            payload_path2 = Path(tmp, "event2.json")
            payload_path2.write_text(json.dumps({"action": "deleted"}))
            pr_api = mock.Mock(
                stdout=json.dumps(
                    {"head": {"sha": "c" * 40}, "base": {"sha": "b" * 40}}
                )
            )
            with self._entorno(
                GITHUB_EVENT_NAME="issue_comment",
                GITHUB_EVENT_PATH=str(payload_path2),
            ):
                with mock.patch.object(review, "ComentariosGh", return_value=falso2):
                    with mock.patch.object(review, "sh", return_value=pr_api):
                        review.cmd_reconcile(argparse.Namespace(work=tmp))
            self.assertEqual(falso2.patches, [])

    def test_descartar_por_comentario_aplica_sin_push(self):
        estado = domain.Snapshot(
            schema=3,
            generation=2,
            revision=domain.Revision(
                base_sha="b" * 40, head_sha="c" * 40, policy_digest="d" * 64
            ),
            next_id=3,
            completion=domain.PARTIAL,
            findings=[
                domain.Finding(
                    id="F1",
                    title="Fuga A",
                    severity="High",
                    status=domain.StatusOpen(),
                    primary_anchor=domain.AnchorLegacy(path="a.py", line=1),
                )
            ],
            command_cursor=0,
        )
        falso = self._falso(
            f"{review.MARKER}\n{domain.encode_snapshot(estado)}",
            [(5, "ai-review: descartar F1")],
        )
        with tempfile.TemporaryDirectory() as tmp:
            payload_path = Path(tmp, "event.json")
            payload_path.write_text(json.dumps({"action": "created"}))
            with self._entorno(
                GITHUB_EVENT_NAME="issue_comment",
                GITHUB_EVENT_PATH=str(payload_path),
            ):
                pr_api = mock.Mock(
                    stdout=json.dumps(
                        {"head": {"sha": "c" * 40}, "base": {"sha": "b" * 40}}
                    )
                )
                with mock.patch.object(review, "ComentariosGh", return_value=falso):
                    with mock.patch.object(review, "sh", return_value=pr_api):
                        with mock.patch.object(
                            review,
                            "collaborator_permission",
                            return_value="write",
                        ):
                            review.cmd_reconcile(argparse.Namespace(work=tmp))
            self.assertEqual(len(falso.patches), 1, "el efecto queda guardado sin push")
        carga = domain.read_snapshot(falso.leer()[0]["body"])
        self.assertIsInstance(carga, domain.Valid)
        self.assertEqual(carga.snapshot.command_cursor, 5)
        self.assertIsInstance(carga.snapshot.findings[0].status, domain.StatusDismissed)

    REVISION_PREVIA = {
        "result": "Texto original del revisor.\nCOVERAGE: complete",
        "manifest": {"reviewed": ["a.py", "b.py"], "excluded": []},
        "review_provider": "opencode-go",
        "model_ok": True,
    }

    def _visible_previo(self, estado):
        # Comentario de la última revisión, en el SHA a…a; el head vivo es c…c.
        return review.revision_visible(self.REVISION_PREVIA, estado, set(), "a" * 40)(
            domain.encode_snapshot(estado).block, ""
        )

    def _pr_api(self, *args, **kw):
        if any("commits/" in str(a) for a in args):
            return mock.Mock(stdout=json.dumps({"sha": "f" * 40}))
        return mock.Mock(
            stdout=json.dumps({"head": {"sha": "c" * 40}, "base": {"sha": "b" * 40}})
        )

    def test_descartar_rerenderiza_el_veredicto_visible(self):
        falso = self._falso(
            self._visible_previo(snapshot_base()), [(5, "ai-review: descartar F1")]
        )
        with tempfile.TemporaryDirectory() as tmp:
            payload_path = Path(tmp, "event.json")
            payload_path.write_text(json.dumps({"action": "created"}))
            with self._entorno(
                GITHUB_EVENT_NAME="issue_comment", GITHUB_EVENT_PATH=str(payload_path)
            ):
                with mock.patch.object(review, "ComentariosGh", return_value=falso):
                    with mock.patch.object(review, "sh", side_effect=self._pr_api):
                        with mock.patch.object(
                            review, "collaborator_permission", return_value="write"
                        ):
                            review.cmd_reconcile(argparse.Namespace(work=tmp))
        cuerpo = falso.leer()[0]["body"]
        self.assertIn(f"<!-- ai-review:sha={'a' * 40} -->", cuerpo)
        self.assertIn("**Veredicto:** 1 Low abierto (1 descartado).", cuerpo)
        self.assertEqual(cuerpo.count("**Veredicto:**"), 1)
        self.assertIn(
            "## Nuevos en este push\n\n- ⚪ Low · `b.py:2` · Fuga B · F2\n\n"
            "## Siguen abiertos\n\nNinguno.\n\n"
            "<details><summary>Descartados (1)</summary>\n\n"
            "- 🟠 High · `a.py:1` · Fuga A · F1",
            cuerpo,
        )
        self.assertIn("## Detalle del revisor\n\nTexto original del revisor.", cuerpo)

    def test_rerender_sin_cambios_reproduce_el_comentario(self):
        estado = snapshot_base()
        original = self._visible_previo(estado)
        despues = domain.replace(estado, command_cursor=9)
        decision = domain.Commit(snapshot=despues)
        falso = self._falso(original, [])
        review.publish_checkpoint(
            decision,
            {"id": 7, "body": original},
            falso,
            visible=review.estado_visible(despues),
        )
        self.assertEqual(
            falso.patches,
            [
                (
                    7,
                    original.replace(
                        domain.encode_snapshot(estado).block,
                        review._cuerpo_con_checkpoint(decision),
                    ),
                )
            ],
        )

    def test_explicacion_vigente_se_muestra_sin_acreditar_el_sha(self):
        digest = review.digest_de_politica(review.politica_de_revision())
        target = domain.ReviewTarget(
            repository="o/r",
            pr_number=1,
            head_sha="c" * 40,
            base_sha="b" * 40,
            policy_digest=digest,
        )
        con_pedido, work, _ = domain.procesar_comandos(
            snapshot_base(), (comando(5, "explicar", "F1"),), permisos_ok, target
        )
        falso = self._falso(self._visible_previo(con_pedido), [])
        artifact = {
            "request_id": work[0].id,
            "kind": "explain",
            "finding_id": "F1",
            "run_id": 77,
            "attempt": 1,
            "pr_head_sha": "c" * 40,
            "policy_digest": digest,
            "observaciones": [],
            "cobertura": domain.UNKNOWN,
            "result": "La fuga ocurre porque el token se registra.\n"
            "## Detalle del revisor\nimitado\nCOVERAGE: complete",
        }
        evento = {
            "workflow_run": {
                "id": 77,
                "name": "1",
                "path": ".github/workflows/ai-review-worker.yml",
                "head_branch": "main",
                "head_sha": "f" * 40,
                "run_attempt": 1,
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "result.json").write_text(json.dumps(artifact))
            payload_path = Path(tmp, "event.json")
            payload_path.write_text(json.dumps(evento))
            with self._entorno(
                GITHUB_EVENT_NAME="workflow_run", GITHUB_EVENT_PATH=str(payload_path)
            ):
                with mock.patch.object(review, "ComentariosGh", return_value=falso):
                    with mock.patch.object(review, "sh", side_effect=self._pr_api):
                        review.cmd_reconcile(argparse.Namespace(work=tmp))
        cuerpo = falso.leer()[0]["body"]
        self.assertIn(
            "## Explicación de F1 · Fuga A\n\n"
            "La fuga ocurre porque el token se registra.\n#### Detalle del revisor\nimitado"
            "\n\n## Detalle del revisor\n\nTexto original del revisor.",
            cuerpo,
        )
        self.assertIn(f"<!-- ai-review:sha={'a' * 40} -->", cuerpo)
        self.assertEqual(
            [
                linea
                for linea in cuerpo.split("\n")
                if linea == "## Detalle del revisor"
            ],
            ["## Detalle del revisor"],
        )
        carga = domain.read_snapshot(cuerpo)
        self.assertEqual(carga.snapshot.pending_requests, [])

    def test_permiso_que_falla_retiene_sin_escribir(self):
        estado = domain.Snapshot(
            schema=3,
            generation=2,
            revision=domain.Revision(
                base_sha="b" * 40, head_sha="c" * 40, policy_digest="d" * 64
            ),
            next_id=3,
            completion=domain.PARTIAL,
            findings=[
                domain.Finding(
                    id="F1",
                    title="Fuga A",
                    severity="High",
                    status=domain.StatusOpen(),
                    primary_anchor=domain.AnchorLegacy(path="a.py", line=1),
                )
            ],
            command_cursor=0,
        )
        falso = self._falso(
            f"{review.MARKER}\n{domain.encode_snapshot(estado)}",
            [(5, "ai-review: descartar F1")],
        )
        with tempfile.TemporaryDirectory() as tmp:
            payload_path = Path(tmp, "event.json")
            payload_path.write_text(json.dumps({"action": "created"}))
            pr_api = mock.Mock(
                stdout=json.dumps(
                    {"head": {"sha": "c" * 40}, "base": {"sha": "b" * 40}}
                )
            )
            with self._entorno(
                GITHUB_EVENT_NAME="issue_comment",
                GITHUB_EVENT_PATH=str(payload_path),
            ):
                with mock.patch.object(review, "ComentariosGh", return_value=falso):
                    with mock.patch.object(review, "sh", return_value=pr_api):
                        with mock.patch.object(
                            review,
                            "collaborator_permission",
                            side_effect=RuntimeError("API caída"),
                        ):
                            review.cmd_reconcile(argparse.Namespace(work=tmp))
            self.assertEqual(falso.patches, [], "retención: nada se escribe")
        carga = domain.read_snapshot(falso.leer()[0]["body"])
        self.assertEqual(carga.snapshot.command_cursor, 0)


class EndurecimientoFrontera(unittest.TestCase):
    """T09 r2: la frontera de comandos no confía en el contenido del sticky."""

    def test_sticky_del_bot_no_se_ejecuta_como_comando(self):
        cuerpo = (
            "ai-review: descartar todo\n"
            f"{review.MARKER}\n"
            "La prosa del modelo cita: ai-review: descartar F1\n"
        )
        estado = snapshot_base()
        comentarios = [
            {"id": 7, "body": cuerpo, "user": "bot"},
            {
                "id": 5,
                "body": "ai-review: descartar F1",
                "user": "jefe",
                "created_at": "t",
                "updated_at": "t",
            },
            {
                "id": 6,
                "body": "por favor ai-review: descartar F2",
                "user": "jefe",
                "created_at": "t",
                "updated_at": "t",
            },
        ]
        out = domain.procesar_comandos(
            estado,
            review._comandos_de_comentarios(comentarios, login="bot", cursor=0),
            permisos_ok,
            TARGET,
        )
        snapshot, _, _ = out
        self.assertEqual(
            [f.status.__class__.__name__ for f in snapshot.findings],
            ["StatusDismissed", "StatusOpen"],
            "sólo el comando del humano aplica; el del bot (inyectado en la prosa) no",
        )

    def test_resultado_de_explicacion_no_cambia_hallazgos(self):
        estado = snapshot_base()
        out = domain.procesar_comandos(
            estado,
            (comando(5, "explicar", "F1"),),
            permisos_ok,
            TARGET,
        )
        snapshot, work, _ = out
        self.assertEqual(work[0].kind, "explain")
        inyectada = domain.Observation(
            title="Fuga inyectada via explain",
            severity="Critical",
            primary_anchor=domain.AnchorLegacy(path="x.py", line=9),
            claim=domain.OPEN,
        )
        entregada = domain.reconcile(
            snapshot,
            domain.ReportReady(
                origin=domain.Origin(kind="re-run", run_id=11),
                request_id=1,
                run_id=11,
                attempt=1,
                observaciones=(inyectada,),
                cobertura=domain.COMPLETE_CLAIM,
            ),
            hechos_vigentes(),
            POLICY,
        )
        self.assertIsInstance(entregada, domain.Commit)
        self.assertEqual(
            [f.title for f in entregada.snapshot.findings],
            ["Fuga A", "Fuga B"],
            "una explicación jamás cambia hallazgos",
        )
        self.assertEqual(entregada.snapshot.completion, snapshot.completion)

    def test_descartar_todo_sin_abiertos_deja_recibo(self):
        vacio = domain.replace(snapshot_base(), findings=[])
        out = domain.procesar_comandos(
            vacio,
            (comando(5, "descartar", "todo"),),
            permisos_ok,
            TARGET,
        )
        snapshot, _, _ = out
        self.assertEqual(snapshot.command_cursor, 5)
        self.assertIn("sin hallazgos abiertos", efectos(snapshot)[5])

    def test_descartar_sin_efecto_deja_recibo(self):
        ya_descartado = domain.replace(
            snapshot_base(),
            findings=[
                domain.replace(
                    hallazgos_base()[0], status=domain.StatusDismissed(command_id=None)
                ),
                hallazgos_base()[1],
            ],
        )
        out = domain.procesar_comandos(
            ya_descartado,
            (comando(5, "descartar", "F1"),),
            permisos_ok,
            TARGET,
        )
        snapshot, _, _ = out
        self.assertEqual(snapshot.command_cursor, 5)
        self.assertIn("sin efecto", efectos(snapshot)[5])

    def test_digest_de_located_distingue_rangos(self):
        a = domain.Finding(
            id="F1",
            title="igual",
            severity="Low",
            status=domain.StatusOpen(),
            primary_anchor=domain.AnchorLocated(
                path="a.py", blob_sha="a" * 40, range=(1, 2), excerpt_digest="d1"
            ),
        )
        b = domain.replace(
            a,
            primary_anchor=domain.AnchorLocated(
                path="a.py", blob_sha="a" * 40, range=(5, 6), excerpt_digest="d2"
            ),
        )
        self.assertNotEqual(
            domain.digest_de_hallazgo(a),
            domain.digest_de_hallazgo(b),
            "dos ubicaciones distintas no comparten digest",
        )

    def test_fetch_all_comments_trae_timestamps(self):
        filas = [
            json.dumps(
                {
                    "id": 5,
                    "user": "jefe",
                    "body": "ai-review: descartar F1",
                    "created_at": "2026-10-06T10:00:00Z",
                    "updated_at": "2026-10-06T10:05:00Z",
                }
            )
        ]
        proc = mock.Mock(stdout="\n".join(filas))
        capturada = {}

        def falso_sh(*args, **kw):
            capturada["jq"] = args[args.index("--jq") + 1]
            return proc

        with mock.patch.object(review, "sh", side_effect=falso_sh):
            comentarios = review.fetch_all_comments("o/r", 1)
        self.assertIn("created_at", capturada["jq"])
        self.assertIn("updated_at", capturada["jq"])
        self.assertEqual(comentarios[0]["created_at"], "2026-10-06T10:00:00Z")
        self.assertEqual(comentarios[0]["updated_at"], "2026-10-06T10:05:00Z")


if __name__ == "__main__":
    unittest.main()
