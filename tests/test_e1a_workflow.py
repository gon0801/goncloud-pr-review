import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "e1-measure.yml"


class WorkflowE1Measure(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.texto = WORKFLOW.read_text()

    def test_declara_workflow_dispatch_con_inputs(self):
        self.assertIn("name: E1 measure", self.texto)
        self.assertIn("workflow_dispatch:", self.texto)
        for entrada in ("pr:", "head:", "base:", "caso:"):
            self.assertIn(entrada, self.texto)

    def test_permisos_solo_lectura(self):
        self.assertIn("permissions:", self.texto)
        self.assertIn("contents: read", self.texto)
        self.assertNotIn("pull-requests", self.texto)
        self.assertNotIn(": write", self.texto)

    def test_no_publica_nada(self):
        self.assertNotIn("publish", self.texto)

    def test_secreto_solo_como_env_de_run(self):
        lineas = self.texto.splitlines()
        con_secreto = [
            linea for linea in lineas if "secrets.AI_REVIEW_API_KEY" in linea
        ]
        self.assertEqual(len(con_secreto), 1, "el secreto se referencia una sola vez")
        self.assertIn("API_KEY:", con_secreto[0])
        self.assertNotIn("echo", con_secreto[0])

    def test_proveedor_fijo_opencode_go(self):
        self.assertIn("PROVIDER: opencode-go", self.texto)

    def test_concurrency_no_cancela_mediciones(self):
        self.assertIn("e1-measure-${{ inputs.caso }}", self.texto)
        self.assertIn("cancel-in-progress: false", self.texto)

    def test_subir_salidas_del_corpus(self):
        self.assertIn("actions/upload-artifact@v4", self.texto)
        self.assertIn("e1-salida-", self.texto)
        self.assertIn("result.json", self.texto)
        self.assertIn("manifest.json", self.texto)


if __name__ == "__main__":
    unittest.main()
