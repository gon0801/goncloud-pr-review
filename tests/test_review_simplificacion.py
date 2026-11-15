import json
import unittest

import review
import review_domain as domain


def bloque(findings, *, next_id=2, seen=0):
    return (
        domain.FINDINGS_PREFIX
        + json.dumps({"findings": findings, "next": next_id, "seen": seen})
        + domain.FINDINGS_SUFFIX
    )


def previo(estado=domain.RESOLVED):
    return {
        "id": "F1",
        "file": "src/a.py",
        "line": 12,
        "severity": "High",
        "title": "Error al abrir",
        "state": estado,
    }


class MemoriaOperativa(unittest.TestCase):
    def test_missing_and_valid_memories_are_ready_as_v3(self):
        missing = domain.memoria_operativa(domain.Missing())
        self.assertIsInstance(missing, domain.MemoryReady)
        self.assertEqual((missing.snapshot.schema, missing.snapshot.next_id), (3, 1))
        self.assertEqual(missing.snapshot.generation, 1)

        legacy = domain.memoria_operativa(
            domain.read_snapshot(bloque([previo()], next_id=5, seen=17))
        )
        self.assertIsInstance(legacy, domain.MemoryReady)
        self.assertEqual(legacy.snapshot.schema, 3)
        self.assertEqual(legacy.snapshot.findings[0].id, "F1")
        self.assertIsInstance(legacy.snapshot.findings[0].status, domain.StatusResolved)
        self.assertEqual((legacy.snapshot.next_id, legacy.snapshot.command_cursor), (5, 17))

        v2 = domain.Snapshot(2, 1, None, 2, domain.UNKNOWN, [], 0)
        encoded = domain.encode_snapshot(v2, domain.StorageBudget())
        ready = domain.memoria_operativa(domain.read_snapshot(encoded))
        self.assertIsInstance(ready, domain.MemoryReady)
        self.assertEqual(ready.snapshot.schema, 3)

        v3 = domain.snapshot_a_v3(v2)
        encoded = domain.encode_snapshot(v3, domain.StorageBudget())
        ready = domain.memoria_operativa(domain.read_snapshot(encoded.block))
        self.assertIsInstance(ready, domain.MemoryReady)
        self.assertEqual(ready.snapshot, v3)

    def test_legacy_loss_or_duplicate_id_blocks_the_original(self):
        cases = [
            [dict(previo(), title="Título " + "x" * 200)],
            [dict(previo(), title="Título\ud800")],
            [dict(previo(), file="src/a.py\notra.py")],
            [dict(previo(), files=["src/a.py"] + [f"src/{n}.py" for n in range(6)])],
            [previo(), dict(previo(), file="src/b.py")],
            [previo(), dict(previo(), id="F01", file="src/b.py")],
            [dict(previo(), line=True)],
        ]
        for findings in cases:
            with self.subTest(findings=findings):
                original = bloque(findings, next_id=4)
                blocked = domain.memoria_operativa(domain.read_snapshot(original))
                self.assertIsInstance(blocked, domain.MemoryBlocked)
                self.assertEqual(blocked.original_block, original)
        original = bloque([], next_id=True)
        self.assertIsInstance(
            domain.memoria_operativa(domain.read_snapshot(original)),
            domain.MemoryBlocked,
        )

    def test_invalid_and_future_memory_block(self):
        for load in (domain.Invalid("no legible", "original"), domain.Future(4, "original")):
            with self.subTest(load=load):
                blocked = domain.memoria_operativa(load)
                self.assertIsInstance(blocked, domain.MemoryBlocked)
                self.assertEqual(blocked.original_block, "original")


class AceptacionUnica(unittest.TestCase):
    def publicar(self, original, observations, changed=()):
        output = review.build_findings(
            {"result": bloque(observations)},
            {
                "mode": "incremental",
                "changed_files": list(changed),
                "reviewed": list(changed),
            },
            {"body": original},
            "o/r",
            "1",
            "bot",
            [],
            policy=domain.ReviewPolicy(),
        )
        self.assertNotIn("keep", output)
        load = domain.read_snapshot(output["block"])
        self.assertIsInstance(load, domain.Valid)
        return load.snapshot

    def test_resolved_finding_does_not_reopen_without_delta_for_any_memory(self):
        finding = domain.Finding(
            id="F1",
            title="Error al abrir",
            severity="High",
            status=domain.StatusResolved(),
            primary_anchor=domain.AnchorLegacy(path="src/a.py", line=12),
        )
        memory = (
            bloque([previo()]),
            domain.encode_snapshot(
                domain.Snapshot(2, 1, None, 2, domain.UNKNOWN, [finding], 0)
            ),
        )
        v3 = domain.snapshot_a_v3(
            domain.Snapshot(2, 1, None, 2, domain.UNKNOWN, [finding], 0)
        )
        memory += (domain.encode_snapshot(v3).block,)
        model = bloque([previo(domain.OPEN)])
        manifest = {
            "mode": "incremental",
            "reason": "same-sha",
            "changed_files": [],
            "reviewed": [],
        }
        for original in memory:
            with self.subTest(original=original[:60]):
                output = review.build_findings(
                    {"result": model},
                    manifest,
                    {"body": original},
                    "o/r",
                    "1",
                    "bot",
                    [],
                    policy=domain.ReviewPolicy(),
                )
                self.assertNotIn("keep", output)
                self.assertEqual(output["merged"][0]["id"], "F1")
                self.assertEqual(output["merged"][0]["state"], domain.RESOLVED)
                self.assertEqual(domain.read_snapshot(output["block"]).snapshot.schema, 3)

    def test_new_resolved_claim_starts_open_in_legacy_memory(self):
        model = bloque([dict(previo(domain.RESOLVED), id="F-new")])
        output = review.build_findings(
            {"result": model},
            {"reason": "same-sha", "changed_files": [], "reviewed": []},
            {"body": bloque([], next_id=1)},
            "o/r",
            "1",
            "bot",
            [],
            policy=domain.ReviewPolicy(),
        )
        self.assertEqual([(f["id"], f["state"]) for f in output["merged"]], [("F1", domain.OPEN)])

    def test_relevant_change_reopens_same_id_and_shifts_legacy_line(self):
        model = [dict(previo(domain.OPEN), line=27)]
        for schema in (1, 2, 3):
            with self.subTest(schema=schema):
                if schema == 1:
                    original = bloque([previo()])
                else:
                    snapshot = domain.Snapshot(
                        2, 1, None, 2, domain.UNKNOWN,
                        [domain.Finding(
                            "F1", "Error al abrir", "High", domain.StatusResolved(),
                            domain.AnchorLegacy("src/a.py", 12),
                        )],
                        0,
                    )
                    if schema == 3:
                        snapshot = domain.snapshot_a_v3(snapshot)
                    encoded = domain.encode_snapshot(snapshot)
                    original = encoded.block if schema == 3 else encoded
                updated = self.publicar(original, model, changed=("src/a.py",))
                finding = updated.findings[0]
                self.assertEqual(finding.id, "F1")
                self.assertIsInstance(finding.status, domain.StatusOpen)
                self.assertEqual(finding.primary_anchor.line, 27)
                self.assertEqual((updated.next_id, updated.schema), (2, 3))

    def test_dismissal_persists_and_moved_id_gets_a_new_identity(self):
        kept = self.publicar(
            bloque([previo(domain.DISMISSED)]),
            [dict(previo(domain.OPEN), id="F-new")],
        )
        self.assertEqual(len(kept.findings), 1)
        self.assertIsInstance(kept.findings[0].status, domain.StatusDismissed)

        moved = self.publicar(
            bloque([previo()]),
            [dict(previo(domain.OPEN), file="src/b.py", title="Otro error")],
            changed=("src/b.py",),
        )
        self.assertEqual([f.id for f in moved.findings], ["F1", "F2"])
        self.assertEqual([f.primary_anchor.path for f in moved.findings], ["src/a.py", "src/b.py"])
        self.assertEqual(moved.next_id, 3)

    def test_duplicate_previous_id_gets_a_separate_finding(self):
        model = [
            dict(previo(domain.OPEN), title="Primera vez"),
            dict(previo(domain.OPEN), title="Segunda vez"),
        ]
        updated = self.publicar(bloque([previo(domain.OPEN)]), model)
        self.assertEqual([(f.id, f.title) for f in updated.findings], [
            ("F1", "Primera vez"), ("F2", "Segunda vez"),
        ])
        self.assertIn("posible duplicado de F1", updated.findings[1].cause_hint)

    def test_reversion_does_not_resolve_a_finding_born_in_this_report(self):
        current = domain.memoria_operativa(domain.Missing()).snapshot
        report = domain.validar_reporte(
            [domain.observation_de_entrada(dict(previo(), id="F-new", state=domain.OPEN))],
            domain.UNKNOWN,
            domain.RepositoryFacts(
                changed_paths=("src/a.py",),
                reverted_paths=("src/a.py",),
                delta_calculado=True,
            ),
        )
        decision = domain.accept_report(
            current,
            domain.ReviewPlan(
                changed_paths=("src/a.py",), policy=domain.ReviewPolicy()
            ),
            report,
        )
        self.assertIsInstance(decision.snapshot.findings[0].status, domain.StatusOpen)
