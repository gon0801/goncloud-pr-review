import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import review_context  # noqa: E402
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

        with tempfile.TemporaryDirectory() as tmp:
            repo = hacer_repo(tmp)
            (repo / "readme.md").write_text("# proyecto\n")
            (repo / "x.py").write_text("x = 0\n")
            base = commit(repo, "base")
            git(repo, "checkout", "-q", "-b", "pr")
            (repo / "x.py").write_text("x = 1\n")
            prev = commit(repo, "prev")
            (repo / "x.py").write_text("x = 0\n")
            head = commit(repo, "head")
            git(repo, "checkout", "-q", "main")
            (repo / "x.py").write_text("x = avanzada\n")
            base_avanzada = commit(repo, "avanza base")
            self.assertEqual(git(repo, "merge-base", base_avanzada, head), base)
            with self.subTest("base avanzada"):
                policy = domain.ReviewPolicy(diff_mode="incremental")
                digest = digest_de_politica(policy)
                result = prepare_review(
                    GitRepository(repo),
                    solicitud(head, base_avanzada, digest),
                    memoria(prev, base_avanzada, digest),
                    policy,
                )
                self.assertEqual(result.modo, "incremental")
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

        with tempfile.TemporaryDirectory() as tmp:
            repo = hacer_repo(tmp)
            (repo / "readme.md").write_text("# proyecto\n")
            (repo / "g.py").write_text("".join(lineas(20, "v1_")))
            base = commit(repo, "base")
            (repo / "readme.md").write_text("# proyecto\n\nsegundo push\n")
            prev = commit(repo, "prev")
            (repo / "g.py").write_text("".join(lineas(5000, "v2_")))
            head = commit(repo, "head")
            with self.subTest("primer archivo mayor que todo el presupuesto"):
                policy = domain.ReviewPolicy(
                    diff_mode="incremental",
                    strict_budget=True,
                    diff_max_bytes=200,
                )
                digest = digest_de_politica(policy)
                result = prepare_review(
                    GitRepository(repo),
                    solicitud(head, base, digest),
                    memoria(prev, base, digest),
                    policy,
                )
                self.assertEqual(result.modo, "incremental")
                al_presupuesto = [
                    om for om in result.omisiones if om.causa == "presupuesto"
                ]
                self.assertEqual([om.ruta for om in al_presupuesto], ["g.py"])
                entregados = [p.text for p in result.entregados]
                self.assertNotIn("g.py", entregados)
                self.assertTrue(
                    any(ob.ruta == "g.py" for ob in result.plan.obligations)
                )
                self.assertEqual(result.cobertura, domain.PARTIAL)

    def test_open_findings_outside_delta_become_obligations(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = hacer_repo(tmp)
            (repo / "readme.md").write_text("# proyecto\n")
            (repo / "q.py").write_text("q = 1\n")
            (repo / "r.py").write_text("r = 1\n")
            (repo / "w.py").write_text("w = 1\n")
            base = commit(repo, "base")
            (repo / "readme.md").write_text("# proyecto\n\nsegundo push\n")
            prev = commit(repo, "prev")
            (repo / "readme.md").write_text("# proyecto\n\ntercer push\n")
            head = commit(repo, "head")
            policy = domain.ReviewPolicy(diff_mode="incremental")
            digest = digest_de_politica(policy)
            previo = domain.snapshot_a_v3(
                domain.Snapshot(
                    schema=2,
                    generation=1,
                    revision=domain.Revision(
                        base_sha=base, head_sha=prev, policy_digest=digest
                    ),
                    next_id=4,
                    completion=domain.COMPLETE_CLAIM,
                    findings=[
                        domain.Finding(
                            id="F1",
                            title="Abierto en q.py",
                            severity="High",
                            status=domain.StatusOpen(),
                            primary_anchor=domain.AnchorLegacy(path="q.py", line=1),
                        ),
                        domain.Finding(
                            id="F2",
                            title="Resuelto en r.py",
                            severity="Medium",
                            status=domain.StatusResolved(at_sha="b" * 40),
                            primary_anchor=domain.AnchorLegacy(path="r.py", line=1),
                        ),
                        domain.Finding(
                            id="F3",
                            title="Descartado en w.py",
                            severity="Low",
                            status=domain.StatusDismissed(command_id=7),
                            primary_anchor=domain.AnchorLegacy(path="w.py", line=1),
                        ),
                    ],
                    command_cursor=0,
                )
            )
            result = prepare_review(
                GitRepository(repo),
                solicitud(head, base, digest),
                previo,
                policy,
            )
            self.assertEqual(result.modo, "incremental")
            self.assertEqual(set(result.plan.changed_paths), {"readme.md"})
            rutas = [ob.ruta for ob in result.plan.obligations]
            self.assertIn("q.py", rutas)
            self.assertNotIn("w.py", rutas)
            self.assertEqual(set(rutas), {"readme.md", "q.py"})


def hacer_repo_selectivo(tmp):
    repo = hacer_repo(tmp)
    (repo / "readme.md").write_text("# proyecto\n")
    (repo / "app.py").write_text("total = calcular_total(x)\n")
    (repo / "docs").mkdir()
    (repo / "docs" / "nota.txt").write_text("usar calcular_total(x) para sumar\n")
    (repo / "mensajes.py").write_text(
        'manual = "ver calcular_total(...) en el manual"\n'
    )
    (repo / "tests").mkdir()
    (repo / "tests" / "test_s.py").write_text(
        "from s import calcular_total\n\n\ndef test_total():\n"
        "    assert calcular_total(2) == 2\n"
    )
    (repo / "viejo.py").write_text(
        "# antes se llamaba a calcular_total(x) aquí\nvalor = 1\n"
    )
    base = commit(repo, "base")
    (repo / "readme.md").write_text("# proyecto\n\nsegundo push\n")
    prev = commit(repo, "prev")
    (repo / "s.py").write_text(
        "def calcular_total(a):\n    return calcular_total(a) + a\n"
    )
    head = commit(repo, "head")
    return repo, base, prev, head


class SelectiveContext(unittest.TestCase):
    def test_prefers_direct_callers_and_tests(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, base, prev, head = hacer_repo_selectivo(tmp)
            policy = domain.ReviewPolicy(diff_mode="incremental")
            digest = digest_de_politica(policy)
            result = prepare_review(
                GitRepository(repo),
                solicitud(head, base, digest),
                memoria(prev, base, digest),
                policy,
                selectivo=True,
            )
            refs = list(result.plan.context_refs)
            self.assertTrue(refs)
            por_archivo = {ref.archivo: ref for ref in refs}
            self.assertEqual(por_archivo["app.py"].relacion, "sintactica")
            self.assertEqual(por_archivo["app.py"].papel, "consumidor")
            self.assertEqual(por_archivo["tests/test_s.py"].relacion, "sintactica")
            self.assertEqual(por_archivo["tests/test_s.py"].papel, "prueba")
            self.assertEqual(
                [(ref.archivo, ref.relacion, ref.papel) for ref in refs],
                [
                    ("tests/test_s.py", "sintactica", "prueba"),
                    ("app.py", "sintactica", "consumidor"),
                    ("docs/nota.txt", "textual", "consumidor"),
                    ("mensajes.py", "textual", "consumidor"),
                    ("viejo.py", "textual", "consumidor"),
                ],
            )
            self.assertEqual(result.avisos, ())
            ultima_sintactica = max(
                i for i, ref in enumerate(refs) if ref.relacion == "sintactica"
            )
            for i, ref in enumerate(refs):
                if ref.archivo == "docs/nota.txt":
                    self.assertEqual(ref.relacion, "textual")
                    self.assertGreater(i, ultima_sintactica)
            ajustado = prepare_review(
                GitRepository(repo),
                solicitud(head, base, digest),
                memoria(prev, base, digest),
                policy,
                selectivo=True,
                contexto_max_bytes=20,
            )
            self.assertEqual(ajustado.plan.context_refs, ())
            self.assertLess(len(ajustado.plan.context_refs), len(refs))
            self.assertIn("contexto truncado", ajustado.avisos)
            apagado = prepare_review(
                GitRepository(repo),
                solicitud(head, base, digest),
                memoria(prev, base, digest),
                policy,
            )
            self.assertEqual(apagado.plan.context_refs, ())
            self.assertEqual(apagado.avisos, ())

    def test_relation_keeps_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, base, prev, head = hacer_repo_selectivo(tmp)
            policy = domain.ReviewPolicy(diff_mode="incremental")
            digest = digest_de_politica(policy)
            result = prepare_review(
                GitRepository(repo),
                solicitud(head, base, digest),
                memoria(prev, base, digest),
                policy,
                selectivo=True,
            )
            refs = list(result.plan.context_refs)
            self.assertTrue(refs)
            por_archivo = {ref.archivo: ref for ref in refs}
            for prosa in ("docs/nota.txt", "viejo.py", "mensajes.py"):
                self.assertIn(prosa, por_archivo, prosa)
                self.assertEqual(por_archivo[prosa].relacion, "textual", prosa)
            for codigo in ("app.py", "tests/test_s.py"):
                self.assertEqual(por_archivo[codigo].relacion, "sintactica", codigo)
            self.assertTrue(any(ref.relacion == "sintactica" for ref in refs))
            self.assertTrue(any(ref.relacion == "textual" for ref in refs))
            for ref in refs:
                self.assertIn(ref.relacion, ("sintactica", "textual"))
                self.assertIn(ref.papel, ("consumidor", "prueba"))
                self.assertEqual(ref.simbolo, "calcular_total")
                self.assertEqual(ref.busqueda, "completa")

    def test_truncated_lead_and_missing_obligation(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = hacer_repo(tmp)
            (repo / "readme.md").write_text("# proyecto\n")
            (repo / "app.py").write_text("total = calcular_total(x)\n")
            (repo / "grande.py").write_text(
                "".join(f"calcular_total({i})\n" for i in range(4000))
            )
            base = commit(repo, "base")
            (repo / "readme.md").write_text("# proyecto\n\nsegundo push\n")
            prev = commit(repo, "prev")
            (repo / "s.py").write_text(
                "def calcular_total(a):\n    return calcular_total(a) + a\n"
            )
            head = commit(repo, "head")
            policy = domain.ReviewPolicy(diff_mode="incremental")
            digest = digest_de_politica(policy)
            result = prepare_review(
                GitRepository(repo),
                solicitud(head, base, digest),
                memoria(prev, base, digest),
                policy,
                selectivo=True,
                busqueda_max_bytes=10,
            )
            pistas = [a for a in result.avisos if a.startswith("pista truncada:")]
            self.assertTrue(pistas)
            self.assertTrue(
                any(a.startswith("pista truncada: calcular_total") for a in pistas)
            )
            self.assertTrue(any("techo de salida" in a for a in pistas))
            if result.plan.context_refs:
                self.assertTrue(
                    all(
                        ref.busqueda.startswith("truncada:")
                        for ref in result.plan.context_refs
                    )
                )
            with self.subTest("refs truncadas deterministas con búsqueda mockeada"):
                truncada = review_context.SearchTruncated(
                    ("app.py", "tests/test_s.py"),
                    "techo de salida (99 bytes examinados)",
                )

                def grep_truncada(*args, **kwargs):
                    return truncada

                with mock.patch.object(
                    review_context, "grep_files", side_effect=grep_truncada
                ):
                    determinista = prepare_review(
                        GitRepository(repo),
                        solicitud(head, base, digest),
                        memoria(prev, base, digest),
                        policy,
                        selectivo=True,
                    )
                for ref in determinista.plan.context_refs:
                    self.assertEqual(
                        ref.busqueda,
                        "truncada: techo de salida (99 bytes examinados)",
                    )
                self.assertEqual(
                    sorted(ref.archivo for ref in determinista.plan.context_refs),
                    ["app.py", "tests/test_s.py"],
                )
                self.assertIn(
                    "pista truncada: calcular_total "
                    "(techo de salida (99 bytes examinados))",
                    determinista.avisos,
                )

        with tempfile.TemporaryDirectory() as tmp:
            repo = hacer_repo(tmp)
            (repo / "readme.md").write_text("# proyecto\n")
            (repo / "f1.py").write_text("".join(lineas(3, "v1_")))
            (repo / "f2.py").write_text("".join(lineas(200, "v1_")))
            base = commit(repo, "base")
            (repo / "readme.md").write_text("# proyecto\n\nsegundo push\n")
            prev = commit(repo, "prev")
            (repo / "f1.py").write_text("".join(lineas(4, "v2_")))
            (repo / "f2.py").write_text("".join(lineas(4000, "v2_")))
            head = commit(repo, "head")
            policy = domain.ReviewPolicy(
                diff_mode="incremental",
                strict_budget=True,
                diff_max_bytes=200,
            )
            digest = digest_de_politica(policy)
            result = prepare_review(
                GitRepository(repo),
                solicitud(head, base, digest),
                memoria(prev, base, digest),
                policy,
                selectivo=True,
            )
            self.assertEqual(result.cobertura, domain.PARTIAL)
            al_presupuesto = [
                om for om in result.omisiones if om.causa == "presupuesto"
            ]
            self.assertEqual([om.ruta for om in al_presupuesto], ["f2.py"])
            self.assertTrue(any(ob.ruta == "f2.py" for ob in result.plan.obligations))

    def test_failed_search_leaves_aviso(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, base, prev, head = hacer_repo_selectivo(tmp)
            policy = domain.ReviewPolicy(diff_mode="incremental")
            digest = digest_de_politica(policy)
            with mock.patch.object(
                review_context,
                "grep_files",
                side_effect=[
                    review_context.SearchFailed("git grep murió"),
                    review_context.SearchFailed("git grep murió"),
                ],
            ):
                result = prepare_review(
                    GitRepository(repo),
                    solicitud(head, base, digest),
                    memoria(prev, base, digest),
                    policy,
                    selectivo=True,
                )
            self.assertIn("pista falló: calcular_total (git grep murió)", result.avisos)
