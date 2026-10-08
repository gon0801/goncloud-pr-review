import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "e1-measure.yml"
REVIEW_PY = ROOT / "review.py"


def claves_providers():
    arbol = ast.parse(REVIEW_PY.read_text())
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Assign) and any(
            isinstance(destino, ast.Name) and destino.id == "PROVIDERS"
            for destino in nodo.targets
        ):
            return sorted(ast.literal_eval(nodo.value))
    raise AssertionError("PROVIDERS no encontrado en review.py")


class WorkflowE1Measure(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.texto = WORKFLOW.read_text()

    def bloque_paso(self, nombre):
        inicio = self.texto.index(f"- name: {nombre}")
        fin = self.texto.find("\n      - name: ", inicio + 1)
        if fin == -1:
            fin = len(self.texto)
        return self.texto[inicio:fin]

    def bloque_input_proveedor(self):
        inicio = self.texto.index("      proveedor:")
        fin = self.texto.index("permissions:")
        return self.texto[inicio:fin]

    def test_declara_workflow_dispatch_con_inputs(self):
        self.assertIn("name: E1 measure", self.texto)
        self.assertIn("workflow_dispatch:", self.texto)
        for entrada in ("pr:", "head:", "base:", "caso:", "proveedor:"):
            self.assertIn(entrada, self.texto)

    def test_permisos_solo_lectura(self):
        self.assertIn("permissions:", self.texto)
        self.assertIn("contents: read", self.texto)
        self.assertNotIn("pull-requests", self.texto)
        self.assertNotIn(": write", self.texto)

    def test_no_publica_nada(self):
        self.assertNotIn("publish", self.texto)

    def test_input_proveedor_es_choice_con_claves_de_providers(self):
        bloque = self.bloque_input_proveedor()
        self.assertIn("type: choice", bloque)
        self.assertIn("default: opencode-go", bloque)
        opciones = [
            linea.strip()[2:]
            for linea in bloque.splitlines()
            if linea.strip().startswith("- ")
        ]
        self.assertEqual(
            sorted(opciones),
            claves_providers(),
            "las opciones del choice son exactamente las claves de PROVIDERS",
        )

    def test_valida_proveedor_antes_de_instalar_o_correr(self):
        bloque = self.bloque_paso("Validar proveedor y ruta de secreto")
        self.assertIn("id: proveedor", bloque)
        self.assertIn("PROVEEDOR_RAW: ${{ inputs.proveedor }}", bloque)
        self.assertIn("tr '[:upper:]' '[:lower:]'", bloque, "canoniza el input")
        self.assertIn('case "$proveedor" in', bloque)
        self.assertIn("*)", bloque)
        self.assertGreaterEqual(
            bloque.count("exit 1"),
            3,
            "rechaza fuera de allowlist y secreto ausente antes de seguir",
        )
        self.assertIn("nombre=$proveedor", bloque)
        self.assertLess(
            self.texto.index("Validar proveedor"),
            self.texto.index("actions/checkout@"),
            "la validación es el primer paso: falla antes de tocar el resto",
        )
        self.assertLess(
            self.texto.index("Validar proveedor"),
            self.texto.index("name: Install"),
        )
        self.assertNotIn("PROVIDER: ${{ inputs.proveedor }}", self.texto)

    def test_propaga_proveedor_validado_y_rutas_excluyentes(self):
        self.assertEqual(
            self.texto.count("PROVIDER: ${{ steps.proveedor.outputs.nombre }}"),
            1,
            "install usa el proveedor validado",
        )
        opengo = self.bloque_paso("Run (medición, opencode-go)")
        deepseek = self.bloque_paso("Run (medición, deepseek)")
        self.assertEqual(
            opengo.count("if: steps.proveedor.outputs.nombre == 'opencode-go'"), 1
        )
        self.assertEqual(
            deepseek.count("if: steps.proveedor.outputs.nombre == 'deepseek'"), 1
        )
        self.assertEqual(opengo.count("PROVIDER: opencode-go"), 1)
        self.assertEqual(deepseek.count("PROVIDER: deepseek"), 1)
        self.assertEqual(self.texto.count('review.py" run'), 2)
        self.assertEqual(
            self.texto.count('ATTEMPTS: "2"'),
            2,
            "las dos rutas miden con ATTEMPTS=2",
        )

    def test_secreto_por_ruta_sin_fallback_cruzado(self):
        nombres = set(re.findall(r"secrets\.([A-Z_]+)", self.texto))
        self.assertEqual(nombres, {"AI_REVIEW_API_KEY", "DEEPSEEK_API_KEY"})
        self.assertEqual(self.texto.count("secrets.AI_REVIEW_API_KEY"), 2)
        self.assertEqual(self.texto.count("secrets.DEEPSEEK_API_KEY"), 2)
        self.assertIn(
            "HAY_AI_REVIEW: ${{ secrets.AI_REVIEW_API_KEY != '' }}", self.texto
        )
        self.assertIn("HAY_DEEPSEEK: ${{ secrets.DEEPSEEK_API_KEY != '' }}", self.texto)
        opengo = self.bloque_paso("Run (medición, opencode-go)")
        deepseek = self.bloque_paso("Run (medición, deepseek)")
        self.assertIn("API_KEY: ${{ secrets.AI_REVIEW_API_KEY }}", opengo)
        self.assertNotIn("DEEPSEEK_API_KEY", opengo)
        self.assertIn("API_KEY: ${{ secrets.DEEPSEEK_API_KEY }}", deepseek)
        self.assertNotIn("AI_REVIEW_API_KEY", deepseek)
        self.assertNotIn("|| secrets.", self.texto, "sin fallback cruzado entre rutas")

    def test_mide_el_arbol_del_head_con_revisor_fuera(self):
        self.assertIn('git checkout --detach "${{ inputs.head }}"', self.texto)
        self.assertIn(
            "cp review.py review_domain.py review_context.py prompt.md", self.texto
        )
        self.assertIn("$RUNNER_TEMP/reviewer", self.texto)
        self.assertLess(
            self.texto.index(
                "cp review.py review_domain.py review_context.py prompt.md"
            ),
            self.texto.index("git checkout --detach"),
            "el revisor se copia del arbol de main antes de mover el arbol al head",
        )
        self.assertLess(
            self.texto.index("git checkout --detach"),
            self.texto.index('review.py" prepare'),
            "el arbol ya es el head cuando prepare calcula el diff",
        )
        self.assertGreaterEqual(
            self.texto.count('"$RUNNER_TEMP/reviewer/review.py"'),
            4,
            "prepare, install y las dos rutas de run invocan el revisor de fuera",
        )
        self.assertEqual(
            self.texto.count('--prompt "$RUNNER_TEMP/reviewer/prompt.md"'),
            3,
            "prepare y las dos rutas de run usan el prompt del revisor fijo",
        )
        self.assertNotIn("run: python3 review.py", self.texto)

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
