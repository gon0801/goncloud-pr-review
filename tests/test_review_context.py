import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import review_domain as domain  # noqa: E402
from review_context import (  # noqa: E402
    GitRepository,
    PreparedReview,
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


def memoria(head, base, digest, completion=domain.COMPLETE_CLAIM):
    return domain.snapshot_a_v3(
        domain.Snapshot(
            schema=2,
            generation=1,
            revision=domain.Revision(
                base_sha=base, head_sha=head, policy_digest=digest
            ),
            next_id=1,
            completion=completion,
            findings=[],
            command_cursor=0,
        )
    )


def memoria_vacia():
    return domain.Snapshot(
        schema=3,
        generation=1,
        revision=None,
        next_id=1,
        completion=domain.UNKNOWN,
        findings=[],
        command_cursor=0,
    )


def solicitud(head, base, digest):
    return domain.WorkRequest(
        id=1,
        kind="review",
        origin="push",
        basis_generation=1,
        state="pending",
        target=domain.ReviewTarget(
            repository="o/r",
            pr_number=1,
            head_sha=head,
            base_sha=base,
            merge_base_sha=base,
            policy_digest=digest,
        ).json(),
        solicitante="",
    )


class IncrementalContext(unittest.TestCase):
    def test_second_push_uses_previous_head_delta(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = hacer_repo(tmp)
            git(repo, "commit", "-q", "--allow-empty", "-m", "raíz")
            raiz = git(repo, "rev-parse", "HEAD")
            (repo / "readme.md").write_text("# proyecto\n")
            (repo / "a.py").write_text("a = 1\n")
            commit(repo, "base")
            (repo / "a.py").write_text("a = 2\n")
            (repo / "b.py").write_text("b = 1\n")
            prev = commit(repo, "prev")
            (repo / "a.py").write_text("a = 3\n")
            (repo / "c.py").write_text("c = 1\n")
            head = commit(repo, "head")
            policy = domain.ReviewPolicy(diff_mode="incremental")
            digest = digest_de_politica(policy)
            result = prepare_review(
                GitRepository(repo),
                solicitud(head, raiz, digest),
                memoria(prev, raiz, digest),
                policy,
            )
            self.assertIsInstance(result, PreparedReview)
            self.assertEqual(result.modo, "incremental")
            self.assertEqual(set(result.plan.changed_paths), {"a.py", "c.py"})
            self.assertEqual(
                set(result.hechos.historical_paths),
                {"readme.md", "a.py", "b.py", "c.py"},
            )
            self.assertEqual(result.plan.previous_sha, prev)
            self.assertTrue(result.hechos.delta_calculado)
            self.assertEqual(set(result.hechos.changed_paths), {"a.py", "c.py"})

    def test_deleted_and_reverted_paths_survive_in_delta(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = hacer_repo(tmp)
            (repo / "x.py").write_text("x = 0\n")
            base = commit(repo, "base")
            (repo / "x.py").write_text("x = 1\n")
            (repo / "z.py").write_text("z = 1\n")
            prev = commit(repo, "prev")
            (repo / "x.py").write_text("x = 0\n")
            (repo / "z.py").unlink()
            head = commit(repo, "head")
            self.assertEqual(git(repo, "diff", "--name-only", base, head), "")
            self.assertEqual(
                set(git(repo, "diff", "--name-only", prev, head).splitlines()),
                {"x.py", "z.py"},
            )
            policy = domain.ReviewPolicy(diff_mode="incremental")
            digest = digest_de_politica(policy)
            result = prepare_review(
                GitRepository(repo),
                solicitud(head, base, digest),
                memoria(prev, base, digest),
                policy,
            )
            self.assertEqual(result.modo, "incremental")
            self.assertEqual(set(result.plan.changed_paths), {"x.py", "z.py"})
            self.assertTrue(result.hechos.delta_calculado)
            self.assertEqual(set(result.hechos.changed_paths), {"x.py", "z.py"})
            self.assertIn("x.py", result.hechos.reverted_paths)

    def test_rename_keeps_old_and_new_blobs_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = hacer_repo(tmp)
            (repo / "readme.md").write_text("# proyecto\n")
            base = commit(repo, "base")
            (repo / "k.py").write_text("".join(lineas(20, "k_")))
            prev = commit(repo, "prev")
            git(repo, "mv", "k.py", "m.py")
            reescrito = lineas(20, "k_")
            reescrito[5] = "k_cambiada = 99\n"
            reescrito.extend(["k_extra_a = 1\n", "k_extra_b = 2\n"])
            (repo / "m.py").write_text("".join(reescrito))
            head = commit(repo, "head")
            estado = git(repo, "diff", "--name-status", "-M", prev, head)
            self.assertEqual(len(estado.splitlines()), 1)
            self.assertTrue(estado.startswith("R"), estado)
            blob_prev = git(repo, "rev-parse", f"{prev}:k.py")
            blob_head = git(repo, "rev-parse", f"{head}:m.py")
            policy = domain.ReviewPolicy(diff_mode="incremental")
            digest = digest_de_politica(policy)
            result = prepare_review(
                GitRepository(repo),
                solicitud(head, base, digest),
                memoria(prev, base, digest),
                policy,
            )
            self.assertEqual(result.modo, "incremental")
            self.assertEqual(result.hechos.renames, (("m.py", "k.py"),))
            self.assertIn(("k.py", blob_prev), result.hechos.blobs)
            self.assertIn(("m.py", blob_head), result.hechos.blobs)
            self.assertEqual(set(result.plan.changed_paths), {"m.py"})
            self.assertEqual(set(result.hechos.changed_paths), {"m.py"})

    def test_full_mode_forces_and_keeps_real_delta(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = hacer_repo(tmp)
            (repo / "app.py").write_text("v1\n")
            base = commit(repo, "base")
            (repo / "app.py").write_text("v2\n")
            prev = commit(repo, "prev")
            (repo / "app.py").write_text("v3\n")
            (repo / "other.py").write_text("v1\n")
            head = commit(repo, "head")
            git(repo, "checkout", "-q", "--orphan", "huérfana")
            git(repo, "commit", "-qam", "otra historia")
            huerfana = git(repo, "rev-parse", "HEAD")
            git(repo, "checkout", "-q", "main")
            policy = domain.ReviewPolicy(diff_mode="incremental")
            digest = digest_de_politica(policy)
            casos = {
                "rebase": (memoria(huerfana, base, digest), "rebase", False),
                "base": (memoria(prev, prev, digest), "base", True),
                "política": (memoria(prev, base, "f" * 64), "política", True),
                "memoria": (memoria_vacia(), "memoria", False),
                "cobertura": (
                    memoria(prev, base, digest, completion=domain.PARTIAL),
                    "cobertura",
                    True,
                ),
            }
            for nombre, (actual, rastro, conserva_delta) in casos.items():
                with self.subTest(caso=nombre):
                    result = prepare_review(
                        GitRepository(repo),
                        solicitud(head, base, digest),
                        actual,
                        policy,
                    )
                    self.assertEqual(result.modo, "full")
                    self.assertIn(rastro, result.motivo)
                    if nombre == "base":
                        self.assertNotIn("rebase", result.motivo)
                    if conserva_delta:
                        self.assertTrue(result.hechos.delta_calculado)
                        self.assertEqual(
                            set(result.hechos.changed_paths),
                            {"app.py", "other.py"},
                        )

    def test_strict_budget_leaves_obligation_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = hacer_repo(tmp)
            (repo / "readme.md").write_text("# proyecto\n")
            (repo / "f1.py").write_text("".join(lineas(20, "v1_")))
            (repo / "f2.py").write_text("".join(lineas(100, "v1_")))
            base = commit(repo, "base")
            (repo / "readme.md").write_text("# proyecto\n\nsegundo push\n")
            prev = commit(repo, "prev")
            (repo / "f1.py").write_text("".join(lineas(40, "v2_")))
            (repo / "f2.py").write_text("".join(lineas(4000, "v2_")))
            head = commit(repo, "head")
            for estricto in (True, False):
                with self.subTest(strict_budget=estricto):
                    policy = domain.ReviewPolicy(
                        diff_mode="incremental",
                        strict_budget=estricto,
                        diff_max_bytes=20000 if estricto else 200,
                    )
                    digest = digest_de_politica(policy)
                    result = prepare_review(
                        GitRepository(repo),
                        solicitud(head, base, digest),
                        memoria(prev, base, digest),
                        policy,
                    )
                    self.assertEqual(result.modo, "incremental")
                    entregados = [p.text for p in result.entregados]
                    self.assertIn("f1.py", entregados)
                    if estricto:
                        al_presupuesto = [
                            om for om in result.omisiones if om.causa == "presupuesto"
                        ]
                        self.assertEqual([om.ruta for om in al_presupuesto], ["f2.py"])
                        self.assertNotIn("f2.py", entregados)
                        self.assertTrue(
                            any(ob.ruta == "f2.py" for ob in result.plan.obligations)
                        )
                        self.assertEqual(result.cobertura, domain.PARTIAL)
                    else:
                        self.assertTrue(
                            any(
                                "over_budget_bytes" in str(om)
                                for om in result.hechos.omissions
                            )
                        )
                        self.assertFalse(
                            any(om.causa == "presupuesto" for om in result.omisiones)
                        )
