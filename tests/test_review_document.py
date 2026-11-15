import argparse
import json
import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

import test_capacidad_pr20

import review
import review_coordinator
import review_domain as domain
import review_github
from review_document import (
    CoverageBook,
    DocumentBlocked,
    ExplanationView,
    FailureView,
    Notice,
    PreparedPublication,
    ReviewDocument,
    ReviewedHeader,
    ReviewView,
    ReviewWarning,
    SafeProse,
    ScopeUsage,
    ScopeView,
    document_from_result,
    has_document_marker,
    prepare_publication,
    read_document,
    safe_prose,
    update_presentation,
)
from review_github import (
    PublishReceipt,
    Unconfirmed,
    publish_capacity_notice,
    publish_checkpoint,
)


def document():
    return ReviewDocument(
        memory=domain.Snapshot(3, 1, None, 1, domain.UNKNOWN, [], 0),
        reviewed=ReviewedHeader("a" * 40, "partial"),
        covered=None,
        review=ReviewView(
            head_sha="a" * 40,
            provider="opencode-go",
            new_ids=(),
            scope=ScopeView(reviewed_count=1, excluded_count=0, diff_bytes=12, mode="full"),
            warnings=(),
            prose=SafeProse("niña 漢字\n\nDos párrafos."),
        ),
        explanations=(),
        notices=(),
        failure=None,
        historical=None,
    )


class CodecDocumento(unittest.TestCase):
    def test_three_notices_do_not_block_optional_book_omission(self):
        notices = tuple(Notice("no-entendido", comment_id=cid) for cid in (1, 2, 3))
        book = CoverageBook(
            "a" * 40,
            tuple((f"path{i}.py", "b" * 40) for i in range(401)),
        )
        current = replace(document(), covered=book, notices=notices)
        prepared = prepare_publication(current)
        self.assertIsInstance(prepared, PreparedPublication)
        decoded = read_document(prepared.body)
        self.assertIsInstance(decoded, ReviewDocument)
        self.assertIsNone(decoded.covered)
        self.assertEqual(decoded.notices, notices)
        self.assertIn(
            ReviewWarning("book-omitted"),
            decoded.review.warnings,
        )
        self.assertEqual(decoded.memory, current.memory)

    def test_three_notices_allow_prose_and_explanation_reduction(self):
        original = document()
        finding = domain.Finding(
            id="F1", title="Error", severity="High",
            status=domain.StatusOpen(),
            primary_anchor=domain.AnchorLegacy(path="a.py", line=1),
        )
        current = replace(
            original,
            memory=replace(original.memory, findings=[finding], next_id=2),
            review=replace(original.review, prose=SafeProse("x" * 80000)),
            explanations=(ExplanationView("F1", SafeProse("y" * 8001)),),
            notices=tuple(Notice("no-entendido", comment_id=cid) for cid in (1, 2, 3)),
        )
        prepared = prepare_publication(current)
        self.assertIsInstance(prepared, PreparedPublication)
        decoded = read_document(prepared.body)
        self.assertIsInstance(decoded, ReviewDocument)
        self.assertEqual(decoded.notices, current.notices)
        self.assertEqual(decoded.memory, current.memory)
        self.assertIn(ReviewWarning("explanation-truncated"), decoded.review.warnings)
        self.assertIn(ReviewWarning("prose-truncated"), decoded.review.warnings)

    def test_three_notices_without_review_keep_latest_when_failure_is_trimmed(self):
        original = document()
        current = replace(
            original,
            review=None,
            failure=FailureView("b" * 40, "worker-failed", SafeProse("x" * 70000)),
            notices=tuple(Notice("no-entendido", comment_id=cid) for cid in (1, 2, 3)),
        )
        prepared = prepare_publication(current)
        self.assertIsInstance(prepared, PreparedPublication)
        decoded = read_document(prepared.body)
        self.assertIsInstance(decoded, ReviewDocument)
        self.assertEqual(
            decoded.notices,
            current.notices[-2:] + (Notice("failure-truncated"),),
        )
        self.assertEqual(decoded.memory, original.memory)

    def test_unusable_report_keeps_imported_history(self):
        original = document()
        history = "Previous useful review and explanation"
        legacy = "\n".join((
            "<!-- ai-review:sticky -->",
            f"<!-- ai-review:sha={'a' * 40} -->",
            f"<!-- ai-review:completion={'a' * 40}:partial -->",
            domain.encode_snapshot(original.memory).block,
            history,
        ))
        imported = read_document(legacy)
        self.assertIsInstance(imported, ReviewDocument)
        self.assertIsNotNone(imported.historical)
        manifest = {"reviewed": ["a.py"], "excluded": []}
        invalid = document_from_result(
            imported, imported.memory, {"result": "garbage", "model_ok": False},
            manifest, "opencode-go", "a" * 40, [],
        )
        prepared = prepare_publication(invalid)
        self.assertIsInstance(prepared, PreparedPublication)
        self.assertIn(history, prepared.body)
        restarted = read_document(prepared.body)
        self.assertEqual(restarted.historical.text, imported.historical.text)
        valid = document_from_result(
            restarted, restarted.memory, {"result": "Revisión válida", "model_ok": True},
            manifest, "opencode-go", "a" * 40, [],
        )
        self.assertNotIn(history, prepare_publication(valid).body)

    def test_view_json_cannot_end_its_html_comment(self):
        current = document()
        scope = replace(
            current.review.scope,
            excluded_count=1,
            exclusions=("`a--!><h1>Injected title</h1>` (filter)",),
        )
        prepared = prepare_publication(
            replace(current, review=replace(current.review, scope=scope)),
        )
        self.assertIsInstance(prepared, PreparedPublication)
        view_line = next(
            line for line in prepared.body.splitlines() if line.startswith("<!-- ai-review:view=")
        )

        class Visible(HTMLParser):
            def __init__(self):
                super().__init__()
                self.tags = []
                self.text = []

            def handle_starttag(self, tag, attrs):
                self.tags.append(tag)

            def handle_data(self, data):
                if data.strip():
                    self.text.append(data)

        parser = Visible()
        parser.feed(view_line)
        self.assertEqual((parser.tags, parser.text), ([], []))
        decoded = read_document(prepared.body)
        self.assertIsInstance(decoded, ReviewDocument)
        self.assertEqual(decoded.review.scope.exclusions, scope.exclusions)

    def test_known_secret_not_published_in_coverage(self):
        secret = "FAKE_AUDIT_" + "SECRET_123456789"
        current = replace(
            document(),
            covered=CoverageBook("a" * 40, ((f"src/{secret}.py", "b" * 40),)),
        )
        prepared = prepare_publication(current, secrets=(secret,))
        self.assertIsInstance(prepared, PreparedPublication)
        self.assertNotIn(secret, prepared.body)
        decoded = read_document(prepared.body)
        self.assertIsInstance(decoded, ReviewDocument)
        self.assertIsNone(decoded.covered)
        self.assertEqual(decoded.memory, current.memory)

    def test_verificador_rechaza_vista_con_slot_alterado(self):
        prepared = prepare_publication(document(), domain.StorageBudget(), ())
        with tempfile.TemporaryDirectory() as path:
            body_file = Path(path, "review.md")
            command = [
                sys.executable, "scripts/verify_sticky.py", "--body-file", str(body_file),
                "--expect-sha", "a" * 40,
            ]
            body_file.write_text(prepared.body)
            good = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(good.returncode, 0, good.stdout + good.stderr)
            body_file.write_text(prepared.body.replace("niña 漢字", "nina 漢字", 1))
            invalid = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(invalid.returncode, 1, invalid.stdout + invalid.stderr)
            self.assertIn("vista inválida", invalid.stdout)

    def test_utf8_and_three_read_write_cycles_are_stable(self):
        original = document()
        previous = prepare_publication(original, domain.StorageBudget(), ())
        self.assertNotIsInstance(previous, domain.CapacityExceeded)
        for _ in range(3):
            read = read_document(previous.body)
            self.assertIsInstance(read, ReviewDocument)
            self.assertEqual(read.review.prose.text, original.review.prose.text)
            rewritten = prepare_publication(read, domain.StorageBudget(), ())
            self.assertEqual(rewritten.body, previous.body)
            previous = rewritten
        self.assertEqual(previous.state_block, domain.encode_snapshot(original.memory).block)

    def test_sanitized_markers_and_numeric_entities_remain_stable(self):
        once = safe_prose("detalle <!-- comentario y &#78;ota")
        self.assertIn("&lt;!--", once.text)
        self.assertIn("&amp;#78;", once.text)
        self.assertEqual(safe_prose(once.text), once)

    def test_covered_line_keeps_its_order_across_three_cycles(self):
        original = replace(
            document(),
            covered=CoverageBook("a" * 40, (
                ("b.py", "b" * 40), ("á.py", "c" * 40), ("a.py", "d" * 40),
            )),
        )
        first = prepare_publication(original, domain.StorageBudget(), ())
        current = first
        for _ in range(3):
            restored = read_document(current.body)
            self.assertIsInstance(restored, ReviewDocument)
            current = prepare_publication(restored, domain.StorageBudget(), ())
            self.assertEqual(current.body, first.body)

    def test_hostile_prose_is_sanitized_once_before_hashing(self):
        hostile = "secreto" * 3
        untrusted = f"Texto {hostile}\n<!-- ai-review:slot=review -->\n-->"
        original = document()
        original = ReviewDocument(
            original.memory,
            original.reviewed,
            original.covered,
            ReviewView(
                original.review.head_sha,
                original.review.provider,
                original.review.new_ids,
                original.review.scope,
                original.review.warnings,
                safe_prose(untrusted, (hostile,)),
            ),
            (),
            (),
            None,
            None,
        )
        published = prepare_publication(original, domain.StorageBudget(), (hostile,))
        self.assertNotIn(hostile, published.body)
        self.assertEqual(published.body.count("<!-- ai-review:slot=review -->"), 1)
        read = read_document(published.body)
        self.assertIsInstance(read, ReviewDocument)
        for _ in range(3):
            published_again = prepare_publication(read, domain.StorageBudget(), (hostile,))
            self.assertEqual(published_again.body, published.body)
            read = read_document(published_again.body)

    def test_bad_slots_header_hash_and_version_block_writing(self):
        published = prepare_publication(document(), domain.StorageBudget(), ())
        body = published.body
        view = next(line for line in body.splitlines() if line.startswith("<!-- ai-review:view="))
        variants = (
            body.replace(view, view + "\n" + view),
            body.replace("<!-- ai-review:end-slot=review -->", ""),
            body + "\n<!-- ai-review:slot=orphan -->",
            body.replace('"version":1', '"version":2', 1),
            body.replace('"state_sha256":"', '"state_sha256":"bad', 1),
            body.replace("<!-- ai-review:sha=" + "a" * 40, "<!-- ai-review:sha=" + "b" * 40),
        )
        for adulterated in variants:
            with self.subTest(adulterated=adulterated[-100:]):
                self.assertIsInstance(read_document(adulterated), DocumentBlocked)
        self.assertTrue(has_document_marker("<!-- ai-review:end-slot"))
        self.assertFalse(has_document_marker("&lt;!-- ai-review:slot=review -->"))

    def test_capacity_rejects_whole_document(self):
        result = prepare_publication(
            document(),
            domain.StorageBudget(max_bytes=80, comment_max_bytes=80, comment_max_chars=80),
            (),
        )
        self.assertIsInstance(result, domain.CapacityExceeded)

    def test_large_prose_book_and_three_multibyte_explanations_fit_without_losing_state(self):
        original = document()
        findings = [
            domain.Finding(
                f"F{i}", f"Hallazgo {i} " + "x" * 90, "High",
                domain.StatusOpen(), domain.AnchorLegacy("src/app.py", i),
            ) for i in range(1, 61)
        ]
        current = replace(
            original,
            memory=replace(original.memory, findings=findings, next_id=61),
            covered=CoverageBook("a" * 40, tuple(
                (f"src/{i:03}.py", "b" * 40) for i in range(70)
            )),
            review=replace(original.review, prose=SafeProse("m" * 35000)),
            explanations=tuple(
                ExplanationView(f"F{i}", SafeProse("漢" * 8000))
                for i in (1, 2, 3)
            ),
        )
        result = prepare_publication(current, domain.StorageBudget(), ())
        self.assertNotIsInstance(result, domain.CapacityExceeded)
        loaded = read_document(result.body)
        self.assertIsInstance(loaded, ReviewDocument)
        self.assertEqual(len(loaded.memory.findings), 60)
        self.assertEqual([e.finding_id for e in loaded.explanations], ["F1", "F2", "F3"])
        self.assertLessEqual(len(result.body.encode("utf-8")), 60000)
        self.assertLessEqual(len(result.body), 60000)

    def test_book_omission_with_three_notices_publishes_without_losing_them(self):
        original = document()
        current = replace(
            original,
            covered=CoverageBook(
                "a" * 40,
                tuple((f"src/{i}.py", "b" * 40) for i in range(500)),
            ),
            notices=(Notice("uno"), Notice("dos"), Notice("tres")),
        )
        result = prepare_publication(current, domain.StorageBudget(), ())
        self.assertIsInstance(result, PreparedPublication)
        restored = read_document(result.body)
        self.assertEqual(restored.notices, current.notices)
        self.assertEqual(restored.memory, original.memory)
        self.assertIsNone(restored.covered)
        self.assertIn(ReviewWarning("book-omitted"), restored.review.warnings)

    def test_large_book_is_omitted_with_notice_without_losing_state(self):
        original = document()
        current = replace(
            original,
            covered=CoverageBook(
                "a" * 40,
                tuple((f"src/{i}.py", "b" * 40) for i in range(500)),
            ),
        )
        result = prepare_publication(current, domain.StorageBudget(), ())
        restored = read_document(result.body)
        self.assertIsInstance(restored, ReviewDocument)
        self.assertIsNone(restored.covered)
        self.assertIn(ReviewWarning("book-omitted"), restored.review.warnings)
        self.assertEqual(restored.memory, original.memory)

    def test_generated_title_is_not_used_as_a_presentation_parser(self):
        published = prepare_publication(document(), domain.StorageBudget(), ())
        changed_title = published.body.replace(
            "### Revisión automática · DeepSeek V4.1 Flash · OpenCode Go · aaaaaaa",
            "## Otro título libre",
            1,
        )
        restored = read_document(changed_title)
        self.assertIsInstance(restored, ReviewDocument)
        self.assertEqual(restored.review.prose.text, document().review.prose.text)
        self.assertEqual(
            prepare_publication(restored, domain.StorageBudget(), ()).body,
            published.body,
        )

    def test_legacy_import_does_not_elevate_unknown_coverage(self):
        block = domain.encode_snapshot(document().memory).block
        old = f"<!-- ai-review:sticky -->\n<!-- ai-review:sha={'a' * 40} -->\n{block}\nTexto anterior"
        imported = read_document(old)
        self.assertIsInstance(imported, ReviewDocument)
        self.assertIsNone(imported.reviewed)
        self.assertIsNone(imported.review)
        self.assertIn("Texto anterior", imported.historical.text)

    def test_pr20_import_keeps_state_and_the_frozen_consumer_reads_the_new_document(self):
        old = test_capacidad_pr20.sticky_pr20()["body"]
        imported = read_document(old)
        self.assertIsInstance(imported, ReviewDocument)
        self.assertIsNotNone(imported.historical)
        result = prepare_publication(imported, domain.StorageBudget(), ())
        self.assertNotIsInstance(result, domain.CapacityExceeded)
        self.assertIn("Revisión anterior importada", result.body)
        restored = read_document(result.body)
        self.assertIsInstance(restored, ReviewDocument)
        self.assertEqual(
            [finding.id for finding in restored.memory.findings],
            [finding.id for finding in imported.memory.findings],
        )
        parsed = test_capacidad_pr20.ElParserDeGleeElStickyNuevo()._merge().parsear_bot(result.body)
        self.assertTrue(parsed.bloque_valido)
        self.assertEqual(len(parsed.hallazgos), len(imported.memory.findings))

    def test_metadata_and_state_secrets_are_redacted_before_hashing(self):
        secret = "falso-" + "secreto-" + "1234567890"
        original = document()
        finding = domain.Finding(
            "F1", f"Título {secret}", "Medium",
            domain.StatusOpen(), domain.AnchorLegacy("src/a.py", 12),
        )
        original = replace(
            original,
            memory=replace(original.memory, findings=[finding], next_id=2),
            review=replace(original.review, provider=f"proveedor {secret}"),
        )
        result = prepare_publication(original, domain.StorageBudget(), (secret,))
        self.assertNotIn(secret, result.body)
        self.assertIsInstance(read_document(result.body), ReviewDocument)

    def test_tampered_slot_hash_and_corrupt_memory_block_writes(self):
        published = prepare_publication(document(), domain.StorageBudget(), ())
        self.assertIsInstance(
            read_document(published.body.replace("niña", "nina", 1)),
            DocumentBlocked,
        )
        self.assertIsInstance(
            read_document(published.body.replace('"schema":3', '"schema":99', 1)),
            DocumentBlocked,
        )

    def test_tampered_typed_scope_is_not_accepted_as_valid_view(self):
        published = prepare_publication(document(), domain.StorageBudget(), ())
        tampered = published.body.replace('"reviewed_count":1', '"reviewed_count":"uno"', 1)
        self.assertIsInstance(read_document(tampered), DocumentBlocked)
        wrong_id = published.body.replace('"new_ids":[]', '"new_ids":[123]', 1)
        self.assertIsInstance(read_document(wrong_id), DocumentBlocked)
        extra_slot_field = published.body.replace(
            '"id":"review","sha256":', '"id":"review","future":1,"sha256":', 1
        )
        self.assertIsInstance(read_document(extra_slot_field), DocumentBlocked)

    def test_writer_never_emits_a_header_its_reader_rejects(self):
        original = document()
        invalid = replace(
            original,
            reviewed=ReviewedHeader("A" * 40, "partial"),
            review=replace(original.review, head_sha="A" * 40),
        )
        self.assertIsInstance(
            prepare_publication(invalid, domain.StorageBudget(), ()),
            DocumentBlocked,
        )

    def test_failure_does_not_advance_reviewed_header_or_duplicate_prose(self):
        original = document()
        failed = replace(
            original, review=None,
            failure=FailureView("b" * 40, "worker-failed", SafeProse("Fallo temporal de red")),
        )
        result = prepare_publication(failed, domain.StorageBudget(), ())
        self.assertEqual(result.body.count("Fallo temporal de red"), 1)
        header = next(line for line in result.body.splitlines() if line.startswith("<!-- ai-review:view="))
        self.assertNotIn("Fallo temporal de red", header)
        restored = read_document(result.body)
        self.assertIsInstance(restored, ReviewDocument)
        self.assertEqual(restored.reviewed, original.reviewed)
        self.assertEqual(restored.failure.prose.text, "Fallo temporal de red")

    def test_large_failure_reason_is_trimmed_without_advancing_reviewed(self):
        original = document()
        failed = replace(
            original, review=None,
            failure=FailureView("b" * 40, "worker-failed", SafeProse("x" * 70000)),
        )
        result = prepare_publication(failed, domain.StorageBudget(), ())
        self.assertNotIsInstance(result, domain.CapacityExceeded)
        restored = read_document(result.body)
        self.assertIsInstance(restored, ReviewDocument)
        self.assertEqual(restored.reviewed, original.reviewed)
        self.assertEqual(restored.failure.head_sha, "b" * 40)
        self.assertIn(Notice("failure-truncated"), restored.notices)


class RetornoAlPuente(unittest.TestCase):
    def test_reconcile_refuses_a_displaced_marker_before_dispatch(self):
        block = domain.encode_snapshot(document().memory).block
        sticky = {
            "id": 5,
            "user": "github-actions[bot]",
            "body": "<!-- ai-review:sticky -->\n" + block + "\n<!-- ai-review:end-slot",
        }
        with (
            mock.patch.dict(os.environ, {
                "REPO": "o/r", "PR_NUMBER": "1", "WORKER_REF": "main",
            }),
            mock.patch.object(review_coordinator, "ComentariosGh") as factory,
            mock.patch.object(review_github, "sh") as shell,
        ):
            factory.return_value.leer.return_value = [sticky]
            review_coordinator.cmd_reconcile(argparse.Namespace())
            factory.return_value.parchar.assert_not_called()
            factory.return_value.crear.assert_not_called()
            shell.assert_not_called()

    def test_direct_publish_and_gate_leave_future_view_untouched(self):
        block = domain.encode_snapshot(document().memory).block
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "result.json").write_text(json.dumps({"result": "COVERAGE: complete"}))
            (work / "manifest.json").write_text(json.dumps({"mode": "full", "reviewed": ["a.py"], "excluded": []}))
            bodies = [
                "<!-- ai-review:sticky -->\n" + block + "\n" + marker
                for marker in (
                "<!-- ai-review:view",
                "<!-- ai-review:slot=review -->",
                "<!-- ai-review:end-slot=review -->",
                )
            ]
            for number, body in enumerate(bodies):
                with self.subTest(case=number):
                    sticky = {
                        "id": 5,
                        "user": "github-actions[bot]",
                        "body": body,
                    }
                    with (
                        mock.patch.dict(os.environ, {
                            "REPO": "o/r", "PR_NUMBER": "1", "HEAD_SHA": "a" * 40,
                        }),
                        mock.patch.object(review, "get_provider", return_value=("opencode-go", {})),
                        mock.patch.object(review_github, "fetch_all_comments", return_value=[sticky]),
                        mock.patch.object(review_github, "sh") as shell,
                        mock.patch.object(review_github, "informar_revision") as output,
                        mock.patch.object(review_github, "set_output") as gate,
                    ):
                        review.cmd_gate(argparse.Namespace(work=tmp))
                        self.assertEqual(gate.call_args.args, ("skip", "true"))
                        review.cmd_publish(argparse.Namespace(work=tmp))
                        shell.assert_not_called()
                        output.assert_called_once_with(False)

    def test_el_publicador_coordinado_no_parcha_marcadores_invalidos(self):
        block = domain.encode_snapshot(document().memory).block
        bodies = [
            "<!-- ai-review:sticky -->\n" + block + "\n" + marker
            for marker in (
            "<!-- ai-review:view",
            "<!-- ai-review:slot=review -->",
            "<!-- ai-review:end-slot=review -->",
            )
        ]
        prepared = prepare_publication(document(), domain.StorageBudget(), ())
        for number, body in enumerate(bodies):
            with self.subTest(case=number):
                sticky = {
                    "id": 5,
                    "body": body,
                }
                calls = []

                class FakeAdapter:
                    def leer(self, observed=sticky):
                        return [observed]

                    def parchar(self, *_, writes=calls):
                        writes.append("PATCH")

                result = publish_checkpoint(
                    prepared,
                    sticky,
                    FakeAdapter(),
                )
                self.assertIsInstance(result, Unconfirmed)
                self.assertEqual(calls, [])


class PublicacionCompleta(unittest.TestCase):
    class Comments:
        def __init__(self, body):
            self.comments = [{"id": 7, "user": "bot", "body": body}]
            self.writes = []
            self.lose_reply = False

        def leer(self):
            return [dict(comment) for comment in self.comments]

        def parchar(self, comment_id, body):
            self.writes.append(("PATCH", comment_id))
            self.comments[0]["body"] = body
            if self.lose_reply:
                raise OSError("respuesta perdida")

        def crear(self, body):
            self.writes.append(("POST", None))
            self.comments = [{"id": 8, "user": "bot", "body": body}]

    def test_same_state_with_new_prose_requires_patch_and_then_is_idempotent(self):
        first = prepare_publication(document(), domain.StorageBudget(), ())
        new = replace(
            document(),
            review=replace(document().review, prose=SafeProse("Otra explicación")),
        )
        second = prepare_publication(new, domain.StorageBudget(), ())
        self.assertEqual(first.state_block, second.state_block)
        adapter = self.Comments(first.body)
        receipt = publish_checkpoint(second, adapter.leer()[0], adapter, login="bot")
        self.assertEqual(adapter.writes, [("PATCH", 7)])
        self.assertEqual(receipt.generacion, 1)
        receipt = publish_checkpoint(second, adapter.leer()[0], adapter, login="bot")
        self.assertEqual(adapter.writes, [("PATCH", 7)])
        self.assertTrue(receipt.recibo["ya_aplicado"])

    def test_repeated_capacity_notice_does_not_stack(self):
        current = prepare_publication(document(), domain.StorageBudget(), ())
        adapter = self.Comments(current.body)
        for _ in range(2):
            status = publish_capacity_notice(adapter.leer()[0], adapter, login="bot")
            self.assertIsInstance(status, Unconfirmed)
        updated = read_document(adapter.leer()[0]["body"])
        self.assertEqual(
            [notice.kind for notice in updated.notices],
            ["capacity-exceeded"],
        )
        self.assertEqual(adapter.writes, [("PATCH", 7)])

    def test_usable_review_removes_capacity_notice_and_keeps_command_notices(self):
        command_notice = Notice("no-entendido", comment_id=12)
        current = prepare_publication(replace(document(), notices=(command_notice,)))
        adapter = self.Comments(current.body)
        publish_capacity_notice(adapter.leer()[0], adapter, login="bot")
        previous = read_document(adapter.leer()[0]["body"])
        self.assertIn(Notice("capacity-exceeded"), previous.notices)
        manifest = {"reviewed": ["a.py"], "excluded": []}
        for model_ok in (False, True):
            candidate = document_from_result(
                previous, previous.memory,
                {"result": "Revisión nueva", "model_ok": model_ok},
                manifest, "opencode-go", "b" * 40, [],
            )
            receipt = publish_checkpoint(
                prepare_publication(candidate), adapter.leer()[0], adapter, login="bot",
            )
            self.assertIsInstance(receipt, PublishReceipt)
            previous = read_document(adapter.leer()[0]["body"])
            self.assertIn(command_notice, previous.notices)
            if model_ok:
                self.assertNotIn(Notice("capacity-exceeded"), previous.notices)
                self.assertNotIn("se conserva el checkpoint previo", adapter.leer()[0]["body"])
            else:
                self.assertIn(Notice("capacity-exceeded"), previous.notices)

    def test_review_dismiss_explain_notice_failure_and_restart_keep_the_view(self):
        prior = domain.Snapshot(3, 1, None, 1, domain.UNKNOWN, [], 0)
        findings = [
            domain.Finding(
                id=f"F{i}", title=f"Fuga {i}", severity="High",
                status=domain.StatusOpen(),
                primary_anchor=domain.AnchorLegacy(path="a.py", line=i),
            )
            for i in range(1, 4)
        ]
        memory = domain.Snapshot(
            3, 2, domain.Revision("b" * 40, "a" * 40, "d" * 64),
            4, domain.COMPLETE_CLAIM, findings, 0,
        )
        adapter = self.Comments(f"<!-- ai-review:sticky -->\n{domain.encode_snapshot(prior).block}")

        def publish(value):
            prepared = prepare_publication(value, domain.StorageBudget(), ())
            self.assertIsInstance(prepared, PreparedPublication)
            receipt = publish_checkpoint(prepared, adapter.leer()[0], adapter, login="bot")
            self.assertIsInstance(receipt, PublishReceipt)
            return read_document(adapter.leer()[0]["body"])

        current = publish(document_from_result(
            read_document(adapter.leer()[0]["body"]), memory,
            {"result": "Revisión inicial.\nCOVERAGE: complete", "model_ok": True},
            {"reviewed": ["a.py"], "excluded": [], "mode": "full"},
            "opencode-go", "a" * 40, ("F1", "F2", "F3"),
            {"a.py": "a" * 40},
        ))
        dismissed = replace(
            memory, generation=3, command_cursor=7,
            findings=[replace(findings[0], status=domain.StatusDismissed(command_id=7)), *findings[1:]],
        )
        current = publish(update_presentation(current, dismissed))
        for i in range(1, 4):
            current = publish(update_presentation(
                current, dismissed, explanations=((f"F{i}", f"Causa {i}"),),
            ))
        current = publish(update_presentation(
            current, dismissed,
            notices=(
                Notice("no-entendido", comment_id=10),
                Notice("extra", comment_id=11),
                Notice("inexistente", ("F99",), 12),
            ),
        ))
        current = publish(replace(
            current,
            failure=FailureView("c" * 40, "worker-failed", SafeProse("el worker no terminó")),
        ))
        body = adapter.leer()[0]["body"]
        self.assertEqual(current.memory.generation, 3)
        self.assertEqual(current.reviewed, ReviewedHeader("a" * 40, "complete"))
        self.assertEqual(current.covered, CoverageBook("a" * 40, (("a.py", "a" * 40),)))
        self.assertEqual([item.finding_id for item in current.explanations], ["F1", "F2", "F3"])
        self.assertEqual([item.comment_id for item in current.notices], [10, 11, 12])
        self.assertEqual(body.count("[!CAUTION]"), 1)
        changed = body.replace("### Revisión automática", "### Encabezado distinto", 1)
        changed = changed.replace("## Detalle del revisor", "## Título distinto", 1)
        restarted = read_document(changed)
        self.assertEqual(restarted.explanations, current.explanations)
        self.assertEqual(restarted.notices, current.notices)
        self.assertEqual(
            prepare_publication(restarted, domain.StorageBudget(), ()).body, body,
        )

    def test_stale_base_and_lost_patch_reply_do_not_confirm_work(self):
        first = prepare_publication(document(), domain.StorageBudget(), ())
        newer = replace(
            document(),
            review=replace(document().review, prose=SafeProse("Texto nuevo")),
        )
        next_body = prepare_publication(newer, domain.StorageBudget(), ())
        adapter = self.Comments(first.body)
        adapter.comments[0]["body"] = next_body.body
        stale = publish_checkpoint(next_body, {"id": 7, "body": first.body}, adapter, login="bot")
        self.assertFalse(hasattr(stale, "recibo"))
        self.assertEqual(adapter.writes, [])

        adapter.comments[0]["body"] = first.body
        adapter.lose_reply = True
        uncertain = publish_checkpoint(next_body, adapter.leer()[0], adapter, login="bot")
        self.assertFalse(hasattr(uncertain, "recibo"))
        self.assertEqual(adapter.writes, [("PATCH", 7)])
        adapter.lose_reply = False
        recovered = publish_checkpoint(next_body, adapter.leer()[0], adapter, login="bot")
        self.assertTrue(recovered.recibo["ya_aplicado"])
        self.assertEqual(adapter.writes, [("PATCH", 7)])

    def test_only_live_persisted_work_is_returned_after_confirmation(self):
        original = document()
        target = domain.ReviewTarget(
            repository="o/r", pr_number=1, head_sha="a" * 40,
            base_sha="b" * 40, policy_digest="d" * 64,
        )
        request = domain.WorkRequest(
            id=1, kind="review", origin="admission",
            basis_generation=1, state="pending", target=target.json(),
        )
        current = replace(
            original,
            memory=replace(
                original.memory, pending_requests=[request], request_count=1,
            ),
        )
        prepared = prepare_publication(current, domain.StorageBudget(), ())
        adapter = self.Comments(prepared.body)
        missing = publish_checkpoint(
            prepared, adapter.leer()[0], adapter, login="bot",
            work_after_commit=(replace(request, id=2),),
        )
        self.assertFalse(hasattr(missing, "recibo"))
        self.assertEqual(adapter.writes, [])
        good = publish_checkpoint(
            prepared, adapter.leer()[0], adapter, login="bot",
            work_after_commit=(request,),
        )
        self.assertEqual(good.trabajo, (request,))

    def test_same_request_id_with_different_content_never_confirms_or_writes(self):
        original = document()
        request = domain.WorkRequest(
            id=1, kind="review", origin="admission", basis_generation=1,
            target=domain.ReviewTarget(
                repository="o/r", pr_number=1, head_sha="a" * 40,
                base_sha="b" * 40, policy_digest="d" * 64,
            ).json(),
        )
        current = replace(original, memory=replace(
            original.memory, pending_requests=[request], request_count=1,
        ))
        prepared = prepare_publication(current)
        variants = (
            replace(request, target={**request.target, "head_sha": "c" * 40}),
            replace(request, target={**request.target, "policy_digest": "e" * 64}),
            replace(request, basis_generation=2),
            replace(request, solicitante="another-user"),
            replace(request, state=domain.FALLIDA, motivo="retry"),
        )
        for prior_body in (prepare_publication(original).body, prepared.body):
            for variant in variants:
                with self.subTest(prior_matches=prior_body == prepared.body, work=variant):
                    adapter = self.Comments(prior_body)
                    outcome = publish_checkpoint(
                        prepared, adapter.leer()[0], adapter, login="bot",
                        work_after_commit=(variant,),
                    )
                    self.assertIsInstance(outcome, Unconfirmed)
                    self.assertEqual(adapter.writes, [])
                    self.assertEqual(adapter.leer()[0]["body"], prior_body)


class PublicacionDirecta(unittest.TestCase):
    def test_lost_patch_reply_does_not_confirm_direct_publication(self):
        prior = prepare_publication(document(), domain.StorageBudget(), ())
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "result.json").write_text(json.dumps({
                review.ERROR_KEY: "worker no terminó",
            }))
            (work / "manifest.json").write_text(json.dumps({
                "mode": "full", "reviewed": ["a.py"], "excluded": [],
            }))
            sticky = [{"id": 5, "user": "github-actions[bot]", "body": prior.body}]
            with (
                mock.patch.dict(os.environ, {
                    "REPO": "o/r", "PR_NUMBER": "1", "HEAD_SHA": "b" * 40,
                }),
                mock.patch.object(review, "get_provider", return_value=("opencode-go", {})),
                mock.patch.object(review_github, "fetch_all_comments", return_value=sticky),
                mock.patch.object(review_github, "sh", side_effect=OSError("respuesta perdida")),
                mock.patch.object(review_github, "informar_revision") as outcome,
            ):
                with self.assertRaises(SystemExit) as caught:
                    review.cmd_publish(argparse.Namespace(work=tmp))
                self.assertEqual(caught.exception.code, 1)
                outcome.assert_called_once_with(False)
            self.assertEqual(sticky[0]["body"], prior.body)

    def test_summary_uses_the_same_presentation_without_state_or_slot_markers(self):
        current = prepare_publication(document(), domain.StorageBudget(), ())
        self.assertIn("## Detalle del revisor", current.summary)
        self.assertIn("niña 漢字", current.summary)
        self.assertNotIn("<!-- ai-review:", current.summary)

    def test_incremental_scope_and_usage_survive_read_write(self):
        original = document()
        scope = replace(
            original.review.scope,
            mode="incremental", prev_sha="b" * 40, changed_count=2,
            usage=ScopeUsage(63, 60, 1234, 100, 44, 0.004),
        )
        current = replace(
            original, review=replace(original.review, scope=scope),
        )
        first = prepare_publication(current, domain.StorageBudget(), ())
        self.assertIn("incremental desde bbbbbbb", first.body)
        self.assertIn("- Modo: incremental desde bbbbbbb (2 archivo(s) cambiaron)", first.body)
        self.assertIn("- Turnos: 63/60", first.body)
        self.assertIn("costo aprox $0.004", first.body)
        restored = read_document(first.body)
        self.assertEqual(restored.review.scope, scope)
        self.assertEqual(
            prepare_publication(restored, domain.StorageBudget(), ()).body,
            first.body,
        )

    def test_command_notices_keep_typed_comment_ids_across_render_cycles(self):
        current = replace(
            document(),
            notices=(
                Notice("no-entendido", comment_id=31),
                Notice("extra", comment_id=32),
                Notice("inexistente", ("F99",), comment_id=33),
            ),
        )
        first = prepare_publication(current, domain.StorageBudget(), ())
        self.assertIn("Comando no entendido", first.body)
        self.assertIn("el comentario 31", first.body)
        self.assertIn("el comentario 32", first.body)
        self.assertIn("el comentario 33 nombra el hallazgo F99", first.body)
        restored = read_document(first.body)
        self.assertEqual(restored.notices, current.notices)
        self.assertEqual(
            prepare_publication(restored, domain.StorageBudget(), ()).body,
            first.body,
        )

    def test_document_renders_findings_and_scope_from_typed_state(self):
        original = document()
        finding = domain.Finding(
            "F1", "Error real", "High",
            domain.StatusOpen(), domain.AnchorLegacy("src/a.py", 12),
        )
        updated = replace(
            original,
            memory=replace(original.memory, findings=[finding], next_id=2),
            review=replace(original.review, new_ids=("F1",)),
        )
        body = prepare_publication(updated, domain.StorageBudget(), ()).body
        self.assertIn("## Nuevos en este push", body)
        self.assertIn("## Siguen abiertos", body)
        self.assertIn("## Detalle del revisor", body)
        self.assertIn("- Revisados: 1 archivo(s)", body)
        self.assertIn("F1", body)

    def test_publishes_new_and_existing_reviews_from_typed_documents(self):
        existing = prepare_publication(document(), domain.StorageBudget(), ()).body
        model = (
            "**Veredicto:** sin problemas abiertos.\nNuevo análisis\n"
            + domain.FINDINGS_PREFIX
            + json.dumps({"findings": [], "next": 1})
            + domain.FINDINGS_SUFFIX
            + "\nCOVERAGE: partial"
        )
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "result.json").write_text(json.dumps({"result": model}))
            (work / "manifest.json").write_text(json.dumps({
                "mode": "full", "reviewed": ["src/a.py"], "excluded": [],
            }))
            for before in (None, existing):
                with self.subTest(before=bool(before)):
                    sticky = (
                        [{"id": 5, "user": "github-actions[bot]", "body": before}]
                        if before else []
                    )

                    def persist(*args, sticky=sticky, **_):
                        body = json.loads(Path(args[args.index("--input") + 1]).read_text())["body"]
                        if args[3] == "PATCH":
                            sticky[0]["body"] = body
                        else:
                            sticky.append({"id": 5, "user": "github-actions[bot]", "body": body})
                        return mock.Mock(stdout="")

                    with (
                        mock.patch.dict(os.environ, {
                            "REPO": "o/r", "PR_NUMBER": "1", "HEAD_SHA": "a" * 40,
                        }),
                        mock.patch.object(review, "get_provider", return_value=("opencode-go", {})),
                        mock.patch.object(review_github, "fetch_all_comments", return_value=sticky),
                        mock.patch.object(review_github, "sh", side_effect=persist) as shell,
                        mock.patch.object(review_github, "informar_revision"),
                    ):
                        review.cmd_publish(argparse.Namespace(work=tmp))
                        self.assertEqual(
                            shell.call_args.args[3],
                            "PATCH" if before else "POST",
                        )
                    body = json.loads((work / "comment.json").read_text())["body"]
                    loaded = read_document(body)
                    self.assertIsInstance(loaded, ReviewDocument)
                    self.assertIn("Nuevo análisis", loaded.review.prose.text)
                    self.assertTrue(
                        test_capacidad_pr20.ElParserDeGleeElStickyNuevo()
                        ._merge().parsear_bot(body).bloque_valido
                    )
