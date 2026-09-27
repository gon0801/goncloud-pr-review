import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "run_test_shard.py"

FIXTURE_TRES = {
    "test_a_fixture.py": """
        import unittest


        class AFix(unittest.TestCase):
            def test_a1(self):
                pass

            def test_a2(self):
                pass
    """,
    "test_b_fixture.py": """
        import unittest


        class BFix(unittest.TestCase):
            def test_b1(self):
                pass
    """,
}

FIXTURE_UNO = {
    "test_solo.py": """
        import unittest


        class Solo(unittest.TestCase):
            def test_unico(self):
                pass
    """,
}

FIXTURE_FALLO = {
    "test_f_fixture.py": """
        import unittest


        class FFix(unittest.TestCase):
            def test_boom(self):
                self.fail("fallo sembrado para la prueba del shard")

            def test_ok(self):
                pass
    """,
}


def run_script(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=120,
    )


def escribe_fixture(tmp, archivos):
    for nombre, cuerpo in archivos.items():
        (Path(tmp) / nombre).write_text(textwrap.dedent(cuerpo))


def descubrir_ids(tests_dir):
    suite = unittest.TestLoader().discover(
        start_dir=str(tests_dir), top_level_dir=str(tests_dir)
    )

    def hojas(s):
        for item in s:
            if isinstance(item, unittest.TestSuite):
                yield from hojas(item)
            else:
                yield item.id()

    return sorted(hojas(suite))


class RechazoArgumentos(unittest.TestCase):
    def test_shard_malformado(self):
        r = run_script("--shard", "0")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--shard", r.stderr)

    def test_shard_fuera_de_rango(self):
        r = run_script("--shard", "2/2")
        self.assertNotEqual(r.returncode, 0)

    def test_shard_denominador_cero(self):
        r = run_script("--shard", "0/0")
        self.assertNotEqual(r.returncode, 0)

    def test_shard_negativo(self):
        r = run_script("--shard", "-1/2")
        self.assertNotEqual(r.returncode, 0)

    def test_sin_modo(self):
        r = run_script()
        self.assertNotEqual(r.returncode, 0)

    def test_dos_modos_a_la_vez(self):
        r = run_script("--shard", "0/2", "--verify-partition", "2")
        self.assertNotEqual(r.returncode, 0)


class Particiones(unittest.TestCase):
    def test_particion_vacia_se_rechaza(self):
        with tempfile.TemporaryDirectory() as tmp:
            escribe_fixture(tmp, FIXTURE_UNO)
            r = run_script("--shard", "3/4", "--tests-dir", tmp)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("quedaría vacío", r.stderr)
            r = run_script("--verify-partition", "4", "--tests-dir", tmp)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("particiones vacías", r.stderr)

    def test_cantidad_impar_reparte_en_dos_y_uno(self):
        with tempfile.TemporaryDirectory() as tmp:
            escribe_fixture(tmp, FIXTURE_TRES)
            r = run_script("--verify-partition", "2", "--tests-dir", tmp)
            self.assertEqual(r.returncode, 0, r.stderr)
            conteos = []
            for linea in r.stdout.splitlines():
                if linea.startswith("shard "):
                    conteos.append(int(linea.rsplit(":", 1)[1]))
            self.assertEqual(sorted(conteos, reverse=True), [2, 1])

    def test_union_de_manifiestos_igual_al_descubrimiento_completo(self):
        esperados = descubrir_ids(ROOT / "tests")
        vistos = []
        for i in range(2):
            r = run_script("--shard", f"{i}/2", "--manifest")
            self.assertEqual(r.returncode, 0, r.stderr)
            shard = r.stdout.split()
            self.assertEqual(shard, sorted(shard))
            vistos.extend(shard)
        self.assertEqual(len(vistos), len(set(vistos)))
        self.assertEqual(sorted(vistos), esperados)


class CorridaPorShard(unittest.TestCase):
    def test_corre_solo_las_pruebas_de_su_shard(self):
        with tempfile.TemporaryDirectory() as tmp:
            escribe_fixture(tmp, FIXTURE_TRES)
            r = run_script("--shard", "1/2", "--tests-dir", tmp)
            self.assertEqual(r.returncode, 0, r.stderr)
            salida = r.stdout + r.stderr
            self.assertIn("test_a_fixture.AFix.test_a2", salida)
            self.assertNotIn("test_a_fixture.AFix.test_a1", salida)
            self.assertNotIn("test_b_fixture.BFix.test_b1", salida)

    def test_fallo_dentro_del_shard_vuelve_distinto_de_cero(self):
        with tempfile.TemporaryDirectory() as tmp:
            escribe_fixture(tmp, FIXTURE_FALLO)
            r = run_script("--shard", "0/2", "--tests-dir", tmp)
            self.assertNotEqual(r.returncode, 0)
            r = run_script("--shard", "1/2", "--tests-dir", tmp)
            self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
