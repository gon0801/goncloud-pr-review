import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import review  # noqa: E402
import review_domain as domain  # noqa: E402
from review_context import (  # noqa: E402
    GitRepository,
    digest_de_politica,
    prepare_review,
)


def git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, text=True, capture_output=True
    ).stdout.strip()


def hacer_repo(tmp):
    repo = Path(tmp, "repo")
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    return repo


def commit(repo, mensaje):
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", mensaje)
    return git(repo, "rev-parse", "HEAD")


def lineas(n, prefijo):
    return [f"{prefijo}{i} = {i}\n" for i in range(n)]


PRESUPUESTO = 2000


def politica():
    return domain.ReviewPolicy(strict_budget=False, diff_max_bytes=PRESUPUESTO)


def estado_nuevo():
    return domain.Snapshot(
        schema=3,
        generation=1,
        revision=None,
        next_id=1,
        completion=domain.UNKNOWN,
        findings=[],
        command_cursor=0,
    )


def manifest_del_worker(preparacion, base, head):
    return {
        "base": base,
        "head": head,
        "reviewed": [p.text for p in preparacion.entregados],
        "excluded": [
            {"path": om.ruta, "reason": om.causa} for om in preparacion.omisiones
        ],
    }


def coordinador_acepta(creada, facts, artifact, policy):
    facts = review.hechos_con_delta_del_worker(facts, artifact, creada.snapshot)
    facts = review.hechos_con_recortes_del_worker(facts, artifact)
    resultado = domain.ReportReady(
        origin=domain.Origin(kind="push", run_id=11),
        request_id=1,
        run_id=11,
        attempt=1,
        observaciones=(),
        cobertura=artifact["cobertura"],
    )
    return domain.reconcile(creada.snapshot, resultado, facts, policy)


def comentario_publico(artifact, decision, head):
    return review.compose(
        artifact,
        artifact["manifest"],
        sha=head,
        provider="opencode-go",
        findings={
            "merged": domain.hallazgos_legacy(decision.snapshot),
            "new_ids": [],
            "block": artifact["block"],
            "model_ok": True,
            "completion": decision.snapshot.completion,
        },
    )


class RecorteBudgetDegradaLaMarca(unittest.TestCase):
    """H3 (SUA-05 4.3): un archivo excluido por `budget` baja la cobertura a
    PARTIAL por la ruta de producción coordinada, el bloque schema 3 y la
    marca pública; el aviso visible se queda."""

    def preparar(self, repo, base, head, policy):
        digest = digest_de_politica(policy)
        facts = domain.RepositoryFacts(
            revision=domain.Revision(base_sha=base, head_sha=head, policy_digest=digest)
        )
        target = domain.ReviewTarget(
            repository="o/r",
            pr_number=1,
            head_sha=head,
            base_sha=base,
            merge_base_sha=base,
            policy_digest=digest,
        )
        creada = domain.reconcile(
            estado_nuevo(),
            domain.RequestReview(origin=domain.Origin(kind="push"), target=target),
            facts,
            policy,
        )
        self.assertIsInstance(creada, domain.Commit)
        solicitud = next(r for r in creada.snapshot.pending_requests if r.id == 1)
        preparacion = prepare_review(
            GitRepository(repo), solicitud, creada.snapshot, policy
        )
        return facts, creada, preparacion

    def test_dos_archivos_el_segundo_no_cabe_da_partial(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = hacer_repo(tmp)
            (repo / "chico.py").write_text("".join(lineas(5, "v1_")))
            (repo / "grande.py").write_text("".join(lineas(5, "w1_")))
            base = commit(repo, "base")
            (repo / "chico.py").write_text("".join(lineas(10, "v2_")))
            (repo / "grande.py").write_text("".join(lineas(2000, "w2_")))
            head = commit(repo, "head")
            policy = politica()

            facts, creada, preparacion = self.preparar(repo, base, head, policy)
            excluido = [om for om in preparacion.omisiones if om.causa == "budget"]
            self.assertEqual([om.ruta for om in excluido], ["grande.py"])
            self.assertEqual(preparacion.cobertura, domain.PARTIAL)

            artifact = {
                "request_id": 1,
                "cobertura": domain.COMPLETE_CLAIM,
                "result": "**Veredicto:** sin problemas.\nCOVERAGE: complete",
                "manifest": manifest_del_worker(preparacion, base, head),
            }
            decision = coordinador_acepta(creada, facts, artifact, policy)
            self.assertIsInstance(decision, domain.Commit)
            self.assertEqual(decision.snapshot.completion, domain.PARTIAL)

            encoded = domain.encode_snapshot(decision.snapshot, domain.StorageBudget())
            self.assertIsInstance(encoded, domain.EncodedCheckpoint)
            leida = domain.read_snapshot(encoded.block)
            self.assertIsInstance(leida, domain.Valid)
            self.assertEqual(leida.snapshot.completion, domain.PARTIAL)

            artifact["block"] = encoded.block
            cuerpo = comentario_publico(artifact, decision, head)
            self.assertIn(f"<!-- ai-review:completion={head}:partial -->", cuerpo)
            self.assertIn("1 archivo(s) quedaron fuera por tamaño del diff", cuerpo)

    def test_diff_completo_sigue_completo(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = hacer_repo(tmp)
            (repo / "a.py").write_text("".join(lineas(5, "v1_")))
            base = commit(repo, "base")
            (repo / "a.py").write_text("".join(lineas(10, "v2_")))
            head = commit(repo, "head")
            policy = politica()

            facts, creada, preparacion = self.preparar(repo, base, head, policy)
            self.assertEqual(preparacion.cobertura, domain.COMPLETE_CLAIM)
            self.assertFalse(
                [om for om in preparacion.omisiones if om.causa == "budget"]
            )

            artifact = {
                "request_id": 1,
                "cobertura": domain.COMPLETE_CLAIM,
                "result": "**Veredicto:** sin problemas.\nCOVERAGE: complete",
                "manifest": manifest_del_worker(preparacion, base, head),
            }
            decision = coordinador_acepta(creada, facts, artifact, policy)
            self.assertIsInstance(decision, domain.Commit)
            self.assertEqual(decision.snapshot.completion, domain.COMPLETE_CLAIM)

            encoded = domain.encode_snapshot(decision.snapshot, domain.StorageBudget())
            leida = domain.read_snapshot(encoded.block)
            self.assertIsInstance(leida, domain.Valid)
            self.assertEqual(leida.snapshot.completion, domain.COMPLETE_CLAIM)

            artifact["block"] = encoded.block
            cuerpo = comentario_publico(artifact, decision, head)
            self.assertIn(f"<!-- ai-review:completion={head}:complete -->", cuerpo)
            self.assertNotIn("quedaron fuera por tamaño del diff", cuerpo)

    def test_un_solo_archivo_sobre_presupuesto_se_entrega_y_queda_completo(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = hacer_repo(tmp)
            (repo / "g.py").write_text("".join(lineas(20, "v1_")))
            base = commit(repo, "base")
            (repo / "g.py").write_text("".join(lineas(5000, "v2_")))
            head = commit(repo, "head")
            policy = politica()

            facts, creada, preparacion = self.preparar(repo, base, head, policy)
            self.assertIn("g.py", [p.text for p in preparacion.entregados])
            self.assertGreater(preparacion.over_budget_bytes, 0)
            self.assertFalse(
                [om for om in preparacion.omisiones if om.causa == "budget"]
            )
            self.assertEqual(preparacion.cobertura, domain.COMPLETE_CLAIM)

            artifact = {
                "request_id": 1,
                "cobertura": domain.COMPLETE_CLAIM,
                "result": "**Veredicto:** sin problemas.\nCOVERAGE: complete",
                "manifest": manifest_del_worker(preparacion, base, head),
            }
            decision = coordinador_acepta(creada, facts, artifact, policy)
            self.assertIsInstance(decision, domain.Commit)
            self.assertEqual(decision.snapshot.completion, domain.COMPLETE_CLAIM)

            encoded = domain.encode_snapshot(decision.snapshot, domain.StorageBudget())
            leida = domain.read_snapshot(encoded.block)
            self.assertIsInstance(leida, domain.Valid)
            self.assertEqual(leida.snapshot.completion, domain.COMPLETE_CLAIM)

            artifact["block"] = encoded.block
            cuerpo = comentario_publico(artifact, decision, head)
            self.assertIn(f"<!-- ai-review:completion={head}:complete -->", cuerpo)
            self.assertNotIn("quedaron fuera por tamaño del diff", cuerpo)


if __name__ == "__main__":
    unittest.main()
