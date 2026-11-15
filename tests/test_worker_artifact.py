import argparse
import glob
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import review_config
import review_coordinator
import review_github

ROOT = Path(__file__).resolve().parent.parent


class WorkerArtifact(unittest.TestCase):
    def test_workflows_export_only_the_closed_result(self):
        token = "secret-" + "ficticio-" + "123456"
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp) / "ai-review"
            work.mkdir()
            (work / "request-package.json").write_text(
                json.dumps({"request_id": 7, "pr_head_sha": "a" * 40})
            )
            (work / "result.json").write_text(
                json.dumps({"result": token, review_config.ERROR_KEY: token})
            )
            for filename in ("repair.json", "cierre.json", "litellm.log", "context.txt"):
                (work / filename).write_text(token)
            with mock.patch.dict(os.environ, {"API_KEY": token}, clear=True):
                review_coordinator.cmd_close_result(argparse.Namespace(work=str(work)))
            result = (work / "result.json").read_text()
            self.assertNotIn(token, result)
            self.assertIn("[REDACTED]", result)

            for filename in (
                ".github/workflows/ai-review-worker.yml",
                "templates/ai-review-worker.yml",
            ):
                with self.subTest(workflow=filename):
                    source = (ROOT / filename).read_text()
                    _, step = source.split("- name: subir resultado como datos", 1)
                    step = step.split("\n      - name:", 1)[0]
                    path = re.search(r'(?m)^\s+path:\s*"([^"]+)"\s*$', step)
                    self.assertIsNotNone(path)
                    selected_path = path.group(1).replace("${{ runner.temp }}", temp)
                    selected = set()
                    for candidate in glob.glob(selected_path):
                        file = Path(candidate)
                        selected.update(file.rglob("*") if file.is_dir() else (file,))
                    self.assertEqual(selected, {work / "result.json"})
                    self.assertIn("if-no-files-found: error", step)
                    self.assertNotIn("always()", step)
                    self.assertLess(
                        source.index("- name: cerrar el resultado para el coordinador"),
                        source.index("- name: subir resultado como datos"),
                    )

    def test_reader_accepts_new_and_old_artifacts_and_rejects_duplicates(self):
        run = {"id": 31, "run_attempt": 2}
        name = "ai-review-result-request-7-run-31-attempt-2"
        result = {"request_id": 7, "result": "revisión"}

        for layout, count in (("root", 1), ("legacy", 1), ("duplicate", 2)):
            with self.subTest(layout=layout), tempfile.TemporaryDirectory() as temp:

                def fake_sh(*args, layout=layout, count=count):
                    if args[1] == "api":
                        return SimpleNamespace(
                            stdout=json.dumps(
                                {"artifacts": [{"name": name, "expired": False}]}
                            )
                        )
                    target = Path(args[args.index("--dir") + 1])
                    target.mkdir(parents=True, exist_ok=True)
                    folder = target if layout == "root" else target / "ai-review"
                    folder.mkdir(exist_ok=True)
                    (folder / "result.json").write_text(json.dumps(result))
                    (folder / "repair.json").write_text("resultado intermedio")
                    if count == 2:
                        another = target / "duplicate"
                        another.mkdir()
                        (another / "result.json").write_text(json.dumps(result))
                    return SimpleNamespace(stdout="")

                with mock.patch.object(review_github, "sh", side_effect=fake_sh):
                    if count == 2:
                        with self.assertRaisesRegex(RuntimeError, "2 result.json"):
                            review_coordinator.artefacto_del_run("o/r", run, 7, temp)
                    else:
                        self.assertEqual(
                            review_coordinator.artefacto_del_run("o/r", run, 7, temp),
                            result,
                        )
