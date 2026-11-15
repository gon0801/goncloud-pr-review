# Evidencia del diseño TN1–TN7

Fecha: 2026-11-15. Base inspeccionada: `d1858c0c1dd67b5a726f9ec09318e88a778a6e08`.
Resultado: diseño y plan documental; implementación y revisión del código futuro pendientes.

## Artefactos

- [Diseño, contratos y siete criterios de cierre](../../reviewer-tn-fixes-design.md).
- [Plan continuo de Devin y revisión final de Codex](../../plans/2026-11-15-reviewer-tn-fixes.md).
- [Auditoría consolidada](../../reviews/2026-11-15-thermo-post-deploy.md).

## Trabajo de arquitectura

Se aplicaron `pstack:architect`, `pstack:arena`, `pstack:why` y `autopilot-runbook`.
Se leyó la implementación de las fronteras afectadas, sus consumidores y el
historial local de extracción, identidad y documento tipado. El diseño conserva
el estado en el sticky, schema 3, presupuestos y escritor confirmado existentes.

Dos agentes independientes produjeron alternativas completas:

- A: terminar contratos en dueños existentes y aceptación directa tipada.
- B: nueva operación profunda `prepare_report` dentro del documento.

Codex leyó ambos y eligió A, incorporando de B el refresco de run/attempt y
precisión de fases de capacidad. Se rechazó ampliar `EncodedCheckpoint` porque
la lectura de frontera existente ya entrega memoria efectiva al render. La
comparación y los motivos están en el diseño. Modelos externos de la
configuración local no disponibles; candidatos del modelo anfitrión, sin juez
entre proveedores. No se presenta esto como diversidad de modelos.

Borradores de esta sesión: `/tmp/reviewer-fixes-design/a/candidate.md` y
`/tmp/reviewer-fixes-design/b/candidate.md`. Son antecedentes, no dependencias
para ejecutar: todas las decisiones necesarias están en los documentos finales.

## Verificación independiente

Un lector nuevo actuó como Devin y comprobó los mecanismos del runbook. Detectó:

1. Importación de `test_capacidad_pr20` al cargar gates focalizados: se fija
   `PYTHONPATH=tests:.` en los siete comandos, sin modificar tests productivos.
2. Orden de copia: se crea el worktree, se cambia a su raíz y después se copia
   el paquete explícito desde la ruta fuente conservada.
3. Arranque sin red: base local verificable y frescura remota desconocida,
   sin inventar rama ni dar por actualizado el remoto.

También se aclararon el cuerpo base del PR, el SHA final remoto sin commit
autorreferente y la lista de documentos permitidos. La relectura de esas
correcciones terminó en **APPROVE del plan, sin bloqueadores pendientes**.

Otro lector nuevo revisó los contratos contra el código: **APPROVE del diseño,
cero bloqueadores de arquitectura**. Se corrigió su única precisión menor:
el techo de 1.000 se refiere a consultas con filtros, no a la URL sin filtros
propuesta. Este veredicto no aprueba implementación ni merge.

Informes de sesión: `/tmp/reviewer-fixes-design/executor-check.md` y
`/tmp/reviewer-fixes-design/reviewer-check.md`.

## Comprobaciones realizadas

- HEAD y referencia local origin/main coinciden con el SHA auditado.
- Selectores de los siete gates: módulos/clases existentes; carga verificada
  con `PYTHONPATH=tests:.`. No se ejecutaron esas suites de nuevo para un cambio
  exclusivamente documental.
- Herramientas locales Python 3.14, gh, actionlint, pre-commit y ruff presentes;
  `gh api --help` confirma paginate/slurp.
- Ruff F del código actual: correcto en la verificación del lector.
- Links locales de diseño/plan y sintaxis de todos sus bloques shell: correctos.
- `git diff --exit-code`: sin cambios en archivos versionados; HEAD intacto.
- No se implementó, creó rama/PR, hizo push, desplegó ni llamó al modelo.

Se reutiliza la evidencia de auditoría de **841 pruebas en 103,171 s**,
`check: OK`, secretos/conflictos/actionlint: el código no cambió. No se afirma
haber ejecutado otra batería completa ni haber verificado credenciales remotas.
Devin deberá comprobar código nuevo y CI de su PR; Codex revisará el SHA final
antes de mergear. El plan no necesita los archivos temporales de esta sesión.
