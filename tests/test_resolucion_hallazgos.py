import argparse
import contextlib
import io
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

BOT = "github-actions[bot]"


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


def texto_del_modelo(*hallazgos):
    bloque = json.dumps({"findings": list(hallazgos)})
    return (
        f"Detalle.\n\n{review.FINDINGS_PREFIX}{bloque}{review.FINDINGS_SUFFIX}\n"
        "COVERAGE: complete\n"
    )


def preparar(repo, work, base, head, previo):
    work.mkdir(parents=True, exist_ok=True)
    (work / "prev.json").write_text(json.dumps(previo))
    evento = work / "event.json"
    evento.write_text(json.dumps({"pull_request": {"title": "t", "body": None}}))
    subprocess.run(
        [sys.executable, str(ROOT / "review.py"), "prepare", "--work", str(work)],
        cwd=repo,
        env=dict(
            os.environ,
            HEAD_SHA=head,
            BASE_SHA=base,
            EXTRA_EXCLUDES="",
            MAX_DIFF_BYTES="1500000",
            GITHUB_EVENT_PATH=str(evento),
        ),
        check=True,
        capture_output=True,
    )
    return json.loads((work / "manifest.json").read_text())


def publicar_directo(repo, work, head, texto, cuerpo_previo=None):
    """cmd_publish del modo directo con el repo bajo revisión como checkout."""
    (work / "result.json").write_text(json.dumps({"result": texto}))
    comentarios = (
        [{"id": 1, "user": BOT, "body": cuerpo_previo}] if cuerpo_previo else []
    )
    sh_real = review.sh

    def correr(*args, **kwargs):
        if args[0] == "git":
            return sh_real(*args, cwd=repo, **kwargs)
        return mock.Mock(returncode=0)

    with (
        mock.patch.dict(
            os.environ,
            {
                "REPO": "o/r",
                "PR_NUMBER": "1",
                "HEAD_SHA": head,
                "GITHUB_STEP_SUMMARY": "",
            },
        ),
        mock.patch.object(review, "fetch_all_comments", return_value=comentarios),
        mock.patch.object(review, "sh", side_effect=correr),
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        review.cmd_publish(argparse.Namespace(work=str(work)))
    return json.loads((work / "comment.json").read_text())["body"]


def estados(cuerpo):
    return [
        (f["id"], f["state"])
        for f in domain.estado_legado_de_memoria(cuerpo)["findings"]
    ]


class ReversionSoloDeLoQueElPrCambio(unittest.TestCase):
    def test_merge_de_la_base_no_resuelve_un_hallazgo_en_un_archivo_que_el_pr_no_toca(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            repo = hacer_repo(tmp)
            (repo / "api.py").write_text("def pay(amount):\n    return amount\n")
            (repo / "caller.py").write_text(
                "from api import pay\n\ndef run():\n    return pay(10)\n"
            )
            m0 = commit(repo, "base")
            git(repo, "checkout", "-q", "-b", "pr")
            (repo / "api.py").write_text(
                "def pay(amount, currency):\n    return amount, currency\n"
            )
            c1 = commit(repo, "pay pide moneda")
            hallazgo = {
                "file": "caller.py",
                "line": 4,
                "files": ["api.py"],
                "severity": "High",
                "title": "caller.py sigue llamando pay() con un argumento",
            }
            work1 = Path(tmp, "work1")
            preparar(repo, work1, m0, c1, {"sha": None, "state": None})
            cuerpo = publicar_directo(repo, work1, c1, texto_del_modelo(hallazgo))
            self.assertEqual(estados(cuerpo), [("F1", "open")])

            git(repo, "checkout", "-q", "main")
            (repo / "caller.py").write_text(
                "from api import pay\n\n# entrada de cobro\ndef run():\n"
                "    return pay(10)\n"
            )
            m1 = commit(repo, "comentario en main")
            git(repo, "checkout", "-q", "pr")
            git(repo, "merge", "-q", "--no-edit", "main")
            c2 = git(repo, "rev-parse", "HEAD")

            work2 = Path(tmp, "work2")
            manifest = preparar(repo, work2, m1, c2, review.prev_de_memoria(cuerpo))
            self.assertEqual(manifest["changed_files"], ["caller.py"])
            cuerpo = publicar_directo(
                repo,
                work2,
                c2,
                texto_del_modelo(dict(hallazgo, id="F1", line=5, state="open")),
                cuerpo,
            )
            self.assertEqual(
                estados(cuerpo),
                [("F1", "open")],
                "caller.py vuelve a ser igual a la base sin que el PR lo haya "
                "cambiado nunca: no es una reversión",
            )


def coordinador_revisa(repo, work, actual, base, head, texto):
    """Push al coordinador, prepare del worker y aceptación del resultado."""
    policy = review.politica_de_revision()
    digest = review.digest_de_politica(policy)
    facts = domain.RepositoryFacts(
        revision=domain.Revision(base_sha=base, head_sha=head, policy_digest=digest)
    )
    pedido = domain.reconcile(
        actual,
        domain.RequestReview(
            origin=domain.Origin(kind="push", run_id=1),
            target=domain.ReviewTarget(
                repository="o/r",
                pr_number=1,
                head_sha=head,
                base_sha=base,
                policy_digest=digest,
            ),
        ),
        facts,
        policy,
    )
    revisado = (
        [f"{review.SHA_PREFIX}{actual.revision.head_sha} -->"]
        if actual.revision
        else []
    )
    cuerpo = "\n".join(
        [review.MARKER, *revisado, domain.encode_snapshot(pedido.snapshot).block]
    )
    manifest = preparar(repo, work, base, head, review.prev_de_memoria(cuerpo))
    artifact = {
        "manifest": {k: manifest[k] for k in review.CAMPOS_DEL_ALCANCE if k in manifest}
    }
    with contextlib.redirect_stderr(io.StringIO()):
        facts = review.hechos_con_delta_del_worker(facts, artifact, pedido.snapshot)
    modelo = review.parse_model_findings(texto)
    decision = domain.reconcile(
        pedido.snapshot,
        domain.ReportReady(
            origin=domain.Origin(kind="re-run", run_id=7),
            request_id=pedido.work_after_commit[0].id,
            run_id=7,
            attempt=1,
            observaciones=tuple(
                domain.observation_de_entrada(e) for e in modelo["findings"]
            ),
            cobertura=review.cobertura_declarada(texto, modelo),
        ),
        facts,
        policy,
    )
    return decision.snapshot


def estados_v3(snapshot):
    return [
        (f.id, type(f.status).__name__.removeprefix("Status"))
        for f in snapshot.findings
    ]


class ForcePushEnModoCoordinado(unittest.TestCase):
    def test_tras_un_force_push_el_coordinador_resuelve_y_reabre(self):
        bug = "def f(x):\n    return eval(x)\n"
        arreglo = "def f(x):\n    return int(x)\n"
        hallazgo = {"file": "a.py", "line": 2, "severity": "High", "title": "eval"}
        with tempfile.TemporaryDirectory() as tmp:
            repo = hacer_repo(tmp)
            (repo / "a.py").write_text("def f(x):\n    return x\n")
            (repo / "b.py").write_text("print(1)\n")
            m0 = commit(repo, "base")
            git(repo, "checkout", "-q", "-b", "pr")
            (repo / "a.py").write_text(bug)
            c1 = commit(repo, "feature")
            vacio = domain.Snapshot(
                schema=3,
                generation=1,
                revision=None,
                next_id=1,
                completion=domain.UNKNOWN,
                findings=[],
                command_cursor=0,
            )
            actual = coordinador_revisa(
                repo, Path(tmp, "w1"), vacio, m0, c1, texto_del_modelo(hallazgo)
            )
            self.assertEqual(estados_v3(actual), [("F1", "Open")])

            (repo / "a.py").write_text(arreglo)
            git(repo, "commit", "-qa", "--amend", "-m", "feature (arreglada)")
            c2 = git(repo, "rev-parse", "HEAD")
            actual = coordinador_revisa(
                repo,
                Path(tmp, "w2"),
                actual,
                m0,
                c2,
                texto_del_modelo(dict(hallazgo, id="F1", state="resolved")),
            )
            self.assertEqual(
                estados_v3(actual),
                [("F1", "Resolved")],
                "el amend cambió a.py: el resuelto del modelo se acredita",
            )

            git(repo, "reset", "-q", "--hard", m0)
            (repo / "a.py").write_text(bug)
            (repo / "b.py").write_text("print(2)\n")
            c3 = commit(repo, "feature sin el arreglo")
            actual = coordinador_revisa(
                repo,
                Path(tmp, "w3"),
                actual,
                m0,
                c3,
                texto_del_modelo(dict(hallazgo, id="F1", state="open")),
            )
            self.assertEqual(
                estados_v3(actual),
                [("F1", "Open")],
                "el force-push devolvió el bug a a.py: el hallazgo se reabre",
            )


if __name__ == "__main__":
    unittest.main()
