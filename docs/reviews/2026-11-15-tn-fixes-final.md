# Revisión final de TN1–TN7 — PR #173

**Veredicto: REQUEST_CHANGES. No mergear este SHA.**

PR: [gon0801/goncloud-pr-review#173](https://github.com/gon0801/goncloud-pr-review/pull/173).
SHA revisado: `2396be5571d5c0ca2f584478f4f5a5c421ca9f34`.
Base: `d1858c0c1dd67b5a726f9ec09318e88a778a6e08`.
Estado remoto comprobado: abierto, borrador, no fusionado, head exacto.
Skill: `pstack:thermo-nuclear-code-quality-review`.

Hay un bloqueador funcional nuevo en C4 y dos pendientes menores de cierre en
C6/C7. La mayoría de los cambios sí cumple el diseño; el verde de tests no
cubre el historial anterior que ya existe en el repositorio.

## R1 — High / bloqueador: una corrida histórica detiene la cosecha de todos los PR

Ubicación: `review_coordinator.py:409–411`, consumo en `cosechar:487–490`.
[Condición en el SHA revisado](https://github.com/gon0801/goncloud-pr-review/blob/2396be5571d5c0ca2f584478f4f5a5c421ca9f34/review_coordinator.py#L409).

El listado ahora recorre correctamente todas las páginas, pero devuelve
`RunSearchUnknown` en cuanto encuentra cualquier título que no cumpla
`PR <n> solicitud <id>`, antes incluso de decidir a qué PR pertenece. El formato
anterior del worker usaba únicamente el número de PR. Es historial válido y
permanente, no una respuesta temporalmente incompleta de GitHub.

Se consultó el historial real en modo de lectura: **201 runs, tres páginas,
163 títulos anteriores**. Por ejemplo, el run
[43574699960](https://github.com/gon0801/goncloud-pr-review/actions/runs/43574699960)
tiene título `155`, evento `workflow_dispatch`, rama `main` y el path correcto
del worker. Reproducir esa respuesta sobre el código del PR da
`RunSearchUnknown(reason='run del worker sin solicitud atribuible')`.

**Impacto:** desplegar este SHA en el central impediría cosechar los resultados
actuales mientras se conserve ese historial. `cosechar` vuelve antes de consultar
el callback/los artifacts; las solicitudes quedan pendientes y la revisión sigue
en curso aunque el worker haya terminado. Repetir el evento no resuelve nada,
porque el run antiguo continúa en el listado. No se observó este defecto en
producción: el SHA del PR aún no fue desplegado.

**Reproducción aislada:** con `GitHubFalso` y `cmd_reconcile` reales, admitir
solicitud 1, añadir worker 900 completado con resultado válido y notificarlo dos
veces. Sin run histórico, la solicitud termina y se publica. Añadiendo un único
run antiguo de otro PR con `display_title='155'`, ambos eventos salen 0, el sticky
queda idéntico y la solicitud 1 sigue pendiente. No hay llamadas externas ni
modificaciones de GitHub en esta reproducción.

**Corrección:** distinguir el protocolo histórico conocido de una respuesta
incompleta o de un worker actual cuya identidad no puede acreditarse. Clasificar
el historial por su formato/procedencia de manera que un run antiguo ajeno no
invalide la búsqueda de todos los PR. Conservar la cautela para workers relevantes
que puedan seguir vivos, los fallos de página, callbacks inciertos y la selección
del run/attempt más nuevo. No corregirlo borrando el historial ni ignorando
indiscriminadamente toda fila desconocida.

**Cierre:** fixture pequeño derivado de la mezcla real (histórico terminado y
worker moderno con resultado); probar publicación y vaciado de pendientes por
callback y por recuperación posterior. Mantener los tests de más de 1.000 runs,
fallo de página, ausencia real, callback viejo y attempt actual. La condición
Unknown estaba prevista para incertidumbre; su aplicación global al historial
válido revela una omisión de compatibilidad que también faltó concretar en el
diseño. La corrección debe quedar fijada en código, pruebas y contrato.

## R2 — Low: C6 conserva cinco ramas que el contrato pedía retirar

Ubicación: `review_document.py:572`, `583`, `588`, `606`, `618`.
[Primer caso](https://github.com/gon0801/goncloud-pr-review/blob/2396be5571d5c0ca2f584478f4f5a5c421ca9f34/review_document.py#L570).

Tras `_with_notice` todavía se comprueba `isinstance(current, CapacityExceeded)`
en cinco sitios. Ese helper devuelve exclusivamente `ReviewDocument`. Se
eliminaron tres de las ocho ramas identificadas, no las ocho.

**Impacto:** no se reprodujo un fallo funcional por estas ramas; mantienen caminos
de error imposibles y dejan incompleta una eliminación expresamente incluida en
C6. La reducción compartida sí funciona: 48 escenarios comparados contra el
commit anterior al refactor produjeron resultados idénticos.

**Corrección/cierre:** retirar las cinco comprobaciones restantes, sin tocar
los controles de capacidad reales de codificación/render. Reutilizar la
caracterización de cuerpos/summaries y las pruebas de capacidad; comprobar que
ninguna llamada a `_with_notice` sigue tratándose como un resultado de error.

## R3 — Low: el codemod deja dos consumidores usando imports de otro módulo

Ubicación: `scripts/migrate_review_facade.py:13–18`,
`tests/test_review_document.py:938`, `tests/test_corrida_cierre.py:265`.
[Selección del supuesto dueño](https://github.com/gon0801/goncloud-pr-review/blob/2396be5571d5c0ca2f584478f4f5a5c421ca9f34/scripts/migrate_review_facade.py#L13).

`owner` usa `hasattr` y escoge el primer módulo que expone el nombre, aunque lo
haya importado de otro. Por eso la migración dejó `review_sticky.ERROR_KEY`
(en vez de `review_config.ERROR_KEY`) y `review_sticky.review_domain.PARTIAL`
(en vez de `review_domain.PARTIAL`). La fachada grande del CLI sí fue retirada;
estos dos usos conservan la dependencia en exports accidentales.

**Impacto:** un cambio interno de imports de sticky rompería tests ajenos a su
responsabilidad. Es deuda menor de C7, no un fallo de publicación. La segunda
ejecución sin cambios del codemod no comprueba que haya elegido el dueño correcto.

**Corrección/cierre:** migrar los dos consumidores a sus dueños y evitar que el
codemod clasifique un import transitivo como definición propia. Basta una
selección acotada de símbolos de esta migración; no introducir otro framework de
exports. Revisar inventario de imports transitivos y las suites afectadas.

## Estado de las siete entregas

| Entrega | Resultado de esta revisión |
| --- | --- |
| C1 / TN4 | Conforme: el mismo input llega a prepare/run/publish; default y validación preservados. |
| C2 / TN6 | Conforme: hints inválidos rechazados en la frontera, sin convertirlos a texto; entradas válidas y parcialidad conservadas. |
| C3 / TN5 | Conforme: un escape reversible para estado/vista/libro, con lectura compatible y cobertura de delimitadores hostiles. |
| C4 / TN7 | **No aprobado: R1**. La paginación y los resultados tipados están implementados, pero el historial legacy bloquea la cosecha. |
| C5 / TN2 | Conforme: resultado tipado, sin intercambio encode/readback en CLI; diferencias operativas de desborde conservadas. |
| C6 / TN3 | Reducción equivalente; **pendiente menor R2**. |
| C7 / TN1 | Fachada retirada y E1 migrado; **pendiente menor R3**. |

## Comprobaciones ejecutadas por Codex

- `bash scripts/check.sh` en el worktree del SHA revisado: **850 pruebas,
  103,764 s, `check: OK`**. Gitleaks, conflictos, actionlint y partición correctos.
  Log de sesión: `/tmp/reviewer-pr173-check.log`.
- `ruff check --select F review*.py scripts/*.py`: correcto.
- `git diff --check d1858c0..HEAD`: correcto; worktree candidato limpio.
- GitHub: cinco checks en success sobre el SHA exacto; `reconcile` skipped.
  CI y Quality terminados correctamente. No se trata el skip como evidencia de
  cosecha real ni como fallo del PR borrador.
- Reproducción de historial con/sin un run legacy a través de `cmd_reconcile`,
  dos notificaciones consecutivas, con todos los efectos de GitHub simulados.
- Publicador directo real con adapter en memoria: desborde inicial conserva
  memoria/cabecera, publica `state-kept` y sale 0; desborde tras redacción conserva
  memoria y sale 1 con aviso de capacidad; cuerpo sin espacio conserva memoria y
  sale 1. Todos reportan `reviewed=false`. La aceptación se inyectó tipada en esta
  sonda para aislar la política de publicación; las suites cubren aceptación.
- Comparación diferencial de **48 escenarios deterministas** entre C5
  `25372d08bf615fc241b10447bbdf6070d8a45a96` y el SHA final: 37 publicables y 11
  resultados de capacidad/bloqueo; **cero diferencias** de cuerpo/summary o
  resultado. Incluye presupuestos de bytes/caracteres, multibyte, explicación,
  histórico, fallo, avisos y libro. Es evidencia de equivalencia de C6, no de
  ausencia universal de defectos.

Evidencia durable: [resultados JSON](pr173-review-evidence.json),
[sondas de historial y publicación](pr173-review-probes.py),
[matriz de capacidad](pr173-capacity-matrix.py).

Reproducir sobre un checkout del SHA exacto, desde su raíz:

```sh
python3 /Users/dn/dev/goncloud-pr-review/docs/reviews/pr173-review-probes.py "$PWD"
python3 /Users/dn/dev/goncloud-pr-review/docs/reviews/pr173-capacity-matrix.py "$PWD"
```

La sonda de historial afirma la presencia del defecto; su salida exitosa no
significa que R1 esté corregido. La matriz emite hashes para cotejar dos versiones.

## Alcance y siguiente revisión

Revisión de los cambios de las 31 rutas del PR, contratos C1–C7 y consumidores
vecinos: publicación directa/coordinada, memoria, capacidad, redacción,
autenticación de resultados, recuperación, action, seed E1 y migración de tests.
Se reutilizó el análisis del resto del proyecto de la auditoría base donde el
código permanece idéntico. No es una segunda auditoría de proveedores ni una
prueba con gasto del modelo.

Devin debe corregir R1 y cerrar los dos pendientes menores en la misma rama,
registrar pruebas y devolver un SHA nuevo para revisión. El caso de historial
mixto es obligatorio antes de aprobar. Codex volverá a revisar el cambio y sus
interacciones conservando la evidencia aún válida; no hace falta rehacer las
siete entregas.

No se modificó implementación, no se comentó ni emitió review remota, no se
cambiaron credenciales, no hubo merge, auto-merge o deploy. Los artifacts de esta
revisión se guardaron en el checkout principal, dejando intacta la rama de Devin.
