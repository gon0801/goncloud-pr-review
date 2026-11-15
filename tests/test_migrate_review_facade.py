import tempfile
import unittest
from pathlib import Path

from scripts import migrate_review_facade as migration


class FacadeOwners(unittest.TestCase):
    def test_owner_is_the_defining_module(self):
        for name, module in (
            ("ERROR_KEY", "review_config"),
            ("PARTIAL", "review_domain"),
            ("OPEN", "review_domain"),
            ("LIBRO_BORRADO", "review_context"),
            ("review_domain", None),
        ):
            with self.subTest(name=name):
                self.assertEqual(migration.owner(name), module)

    def test_migration_uses_direct_definitions_and_is_repeatable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "consumer.py")
            path.write_text("import review\nvalue = (review.ERROR_KEY, review.PARTIAL)\n")
            self.assertEqual(
                migration.migrate(path), ("review_config", "review_domain"),
            )
            self.assertEqual(
                path.read_text(),
                "import review\nimport review_config\nimport review_domain\n"
                "value = (review_config.ERROR_KEY, review_domain.PARTIAL)\n",
            )
            self.assertEqual(migration.migrate(path), ())
