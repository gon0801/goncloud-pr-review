# Segunda revisión de TN1–TN7 — PR #173

**Veredicto: APPROVE para el SHA `dfbf47f8a846bd79d5e31f53c2b85f30d6c947b4`.**

PR: [gon0801/goncloud-pr-review#173](https://github.com/gon0801/goncloud-pr-review/pull/173).
Base: `d1858c0c1dd67b5a726f9ec09318e88a778a6e08`.
Skill aplicado: `pstack:thermo-nuclear-code-quality-review`.

R1, R2 y R3 de la [revisión anterior](2026-11-15-tn-fixes-final.md) están
cerrados. No encontré nuevos hallazgos que requieran cambios en este delta.
El PR remoto sigue abierto, en borrador, sin merge y apuntando al SHA revisado.
Esta aprobación es el veredicto local de Codex; no se envió una review a GitHub.

## Cierre de los hallazgos

| Hallazgo | Corrección y comprobación independiente |
| --- | --- |
| R1 / C4: historial legacy bloqueaba toda cosecha | El título numérico identifica el PR histórico. Los runs ajenos y los propios terminados no invalidan una búsqueda íntegra; un run antiguo propio todavía activo o un título desconocido conserva la incertidumbre. Reproduje la respuesta real guardada en la revisión anterior: 201 runs, tres páginas. Ahora produce `RunSearchComplete` para los PR 12, 155 y 173. |
| R2 / C6: cinco ramas imposibles | Se retiraron las cinco comprobaciones de `CapacityExceeded` tras `_with_notice`. Permanecen los controles reales de codificación y presupuesto. La matriz de 48 casos produce exactamente los mismos cuerpos, summaries y resultados que el SHA anterior. |
| R3 / C7: propietarios transitivos | Los dos consumidores usan `review_config.ERROR_KEY` y `review_domain.PARTIAL`. El codemod reconoce definiciones de primer nivel mediante AST y deja de usar `hasattr` sobre exports accidentales. Las pruebas cubren dueño e idempotencia. |

Para R1 ejecuté además `cmd_reconcile` real con efectos de GitHub simulados,
tanto por callback como por evento posterior, con un run antiguo ajeno activo
y un run antiguo propio. Si el propio está terminado, se publica el resultado
moderno y se vacían los pendientes; repetir el evento conserva ese resultado.
Si sigue activo, dos eventos conservan el pendiente; marcarlo terminado permite
recuperarlo en el siguiente evento. No se eliminaron validaciones de paginación,
duplicados, callback, autenticación, SHA, ref ni attempt.

El inventario de consumidores no muestra otros imports transitivos introducidos
por esta migración. Los 27 usos de `review_domain.replace` ya existen idénticos
en la base del PR; no son parte de R3 ni una regresión de estas correcciones.

## Evidencia ejecutada por Codex

- `bash scripts/check.sh`: **854 pruebas, 116,358 s, `check: OK`** sobre el
  SHA final; incluye revisión de secretos, conflictos, workflows y partición.
  Log de sesión: `/tmp/reviewer-pr173-rereview-check.log`.
- `ruff check --select F review*.py scripts/*.py` y
  `git diff --check d1858c0..HEAD`: correctos. Worktree candidato limpio.
- [CI](https://github.com/gon0801/goncloud-pr-review/actions/runs/43776779128)
  y [Quality](https://github.com/gon0801/goncloud-pr-review/actions/runs/43776779120):
  terminados en éxito. Cinco checks correctos sobre el SHA exacto; `reconcile`
  omitido. Ese skip no se usa como prueba de integración en producción.
- Repetición independiente de 48 casos de capacidad contra
  `2396be5571d5c0ca2f584478f4f5a5c421ca9f34`: cero diferencias.
- Repetición de las tres sondas del publicador directo: desborde inicial,
  desborde tras redacción y cuerpo sin espacio conservan memoria/cabecera y
  reportan `reviewed=false`, con los códigos de salida esperados.
- Verificación final del head local y remoto: ambos `dfbf47f`; el commit
  posterior al último cambio de código solo agrega evidencia documental.

Resultados de las sondas y matriz: [evidencia JSON](pr173-rereview-evidence.json).
La respuesta histórica utilizada es la captura real de la revisión anterior,
no una consulta nueva al historial completo. Las pruebas de recuperación no
despachan workers ni publican comentarios reales.

## Alcance y estado final

Se revisaron las diez rutas modificadas desde la revisión anterior y las
interacciones afectadas: clasificación del historial, recuperación y reintentos,
reducción de documentos, presupuesto y propiedad de imports. Se reutiliza la
evidencia anterior de C1–C7 donde el código no cambió. El delta no introduce
capas innecesarias ni crecimiento injustificado de archivos; la compatibilidad
histórica queda en la frontera que interpreta el listado de runs.

**C1–C7 quedan conformes para este SHA.** No queda una corrección pendiente
identificada en esta revisión. La aprobación tiene este alcance; no constituye
una garantía de ausencia de cualquier defecto ni una verificación del deploy.

No se implementaron cambios, no se alteraron permisos o credenciales y no hubo
merge, auto-merge ni deploy. Los artifacts de revisión quedaron en el checkout
principal; la rama de Devin permanece intacta.
