## Why

Corregir los siete defectos auditados del revisor de PR sin cambiar el esquema de memoria, los permisos ni los consumidores externos. El resultado queda en un único PR borrador para revisión de Codex. No se hace merge ni deploy.

## What changed

- C1 pasa `finding_identity` a `prepare`, `run` y `publish`.
- C2 rechaza metadatos inválidos del modelo antes de aceptar hallazgos.
- C3 aplica el mismo escape reversible al JSON oculto del estado, la vista y el libro.
- C4 distingue el historial completo de una búsqueda incierta de runs del worker.
- C5 transporta la aceptación como `Snapshot` tipado hasta el documento.
- C6 comparte la reducción de prosa sin cambiar el cuerpo ni el summary caracterizados.
- C7 mueve los consumidores al módulo dueño y retira los reexports sin uso de `review.py`.

Cada unidad tiene un commit separado. La [evidencia de C1–C7](https://github.com/gon0801/goncloud-pr-review/blob/fix/reviewer-tn1-tn7/docs/evidence/reviewer/tn-fixes.md) registra pruebas, decisiones y fallos corregidos.

## Follow-up de la revisión final

- C4 recoge el worker actual con historial legacy mezclado, sin aceptar runs
  ajenos ni declarar completo un listado parcial.
- C6 elimina las cinco ramas de capacidad inalcanzables restantes.
- C7 migra dos consumidores y hace que el codemod elija definiciones propias.
- La evidencia enlazada registra las pruebas de regresión y los commits nuevos.

## Scope

Se mantienen schema 3, los lectores v1 y v2, los IDs, los presupuestos y la confirmación de publicación. No se actualizan pins, consumidores externos, permisos ni credenciales.

## Blast Radius

Los cambios tocan el action, la publicación directa, la recuperación coordinada y el seed E1. La recuperación conserva solicitudes cuando la API no acredita un historial completo. El codec conserva los valores después de leer el JSON escapado.

## Verification

- `bash scripts/check.sh` pasó pre-commit, actionlint, partición y 854 pruebas.
- Las pruebas de E1 ejecutan el Python del workflow y comprueban IDs, modo y diff.
- La evidencia enlazada detalla los gates por unidad.

Aprobado por Codex en `dfbf47f8a846bd79d5e31f53c2b85f30d6c947b4`; véase [el registro de aprobación](tn-fixes-approval.md).
