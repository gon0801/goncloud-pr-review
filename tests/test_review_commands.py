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
        estado = snapshot_base()
        creada = domain.reconcile(
            estado,
            domain.RequestReview(
                origin=domain.Origin(kind="comando", comment_id=5), target=TARGET
            ),
            domain.RepositoryFacts(),
            POLICY,
        )
        # permiso revocado antes de ejecutar el modelo: rechazo terminal
        fallo = domain.reconcile(
            creada.snapshot,
            domain.ReportFailed(
                request_id=1,
                run_id=11,
                attempt=1,
                motivo="permiso revocado antes del modelo",
                retryable=False,
            ),
            domain.RepositoryFacts(),
            POLICY,
        )
        self.assertIsInstance(fallo, domain.Commit)
        self.assertEqual(
            fallo.work_after_commit, (), "sin trabajo: cero llamadas al proveedor"
        )
        finalizada = fallo.snapshot.pending_requests[0]
        self.assertEqual(finalizada.state, "finished")
        self.assertEqual(finalizada.motivo, "permiso revocado antes del modelo")

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
        target_viejo = domain.ReviewTarget(
            repository="o/r",
            pr_number=1,
            head_sha="c" * 40,
            base_sha="b" * 40,
            policy_digest="d" * 64,
        )
        con_vieja = domain.reconcile(
            snapshot_base(),
            domain.AuthorizedCommand(
                origin=domain.Origin(kind="comando", comment_id=7),
                target=target_viejo,
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
    def _entorno(self, tmp, **over):
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
        import review_domain as domain

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
                tmp,
                GITHUB_EVENT_NAME="issue_comment",
                GITHUB_EVENT_PATH=str(payload_path),
            ):
                with mock.patch.object(review, "ComentariosGh", return_value=falso):
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
            with self._entorno(
                tmp,
                GITHUB_EVENT_NAME="issue_comment",
                GITHUB_EVENT_PATH=str(payload_path2),
            ):
                with mock.patch.object(review, "ComentariosGh", return_value=falso2):
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
                tmp,
                GITHUB_EVENT_NAME="issue_comment",
                GITHUB_EVENT_PATH=str(payload_path),
            ):
                with mock.patch.object(review, "ComentariosGh", return_value=falso):
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
            with self._entorno(
                tmp,
                GITHUB_EVENT_NAME="issue_comment",
                GITHUB_EVENT_PATH=str(payload_path),
            ):
                with mock.patch.object(review, "ComentariosGh", return_value=falso):
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
        self.assertEqual(snapshot.command_cursor, 5)
        self.assertEqual(
            [f.status.__class__.__name__ for f in snapshot.findings][1],
            "StatusOpen",
            "el comando citado en mitad de un texto no es comando",
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
