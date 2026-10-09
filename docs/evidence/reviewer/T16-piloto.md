# T16: piloto del revisor coordinado en el repo central y cierre del plan

Fecha: 2026-10-09. Cierra la tarea T16 del plan
`docs/superpowers/plans/2026-10-05-revisor-correcciones.md`. La medición y la
decisión de D0 están en `D0-C0.md`.

## Qué se probó

El conjunto coordinado (publicador `ai-review-publish.yml` y worker
`ai-review-worker.yml`) se instaló en el repo central con
`scripts/install.sh --coordinado`, fijado a un SHA candidato por tag. El
PR de prueba #71 tiene errores a propósito (división por cero, índice fuera
de rango y `eval` sobre datos leídos de archivo).

## Defectos que el piloto encontró y se corrigieron

Ninguno se veía en las pruebas, porque simulaban los eventos de GitHub.

1. El worker leía los secrets `API_KEY` y `FALLBACK_API_KEY`, que no existen.
   Ahora usa `AI_REVIEW_API_KEY` (opencode-go) y `DEEPSEEK_API_KEY` de
   respaldo (#68).
2. El worker nunca instalaba Claude Code ni LiteLLM antes del modelo (#68).
3. Las plantillas expandían `${{ }}` de eventos e inputs dentro de scripts,
   con riesgo de inyección; la primera corrección tampoco frenaba valores
   multilínea (#70).
4. El resultado del worker nunca llegaba al publicador: un run despachado con
   `GITHUB_TOKEN` no dispara `workflow_run`. El worker avisa ahora con
   `workflow_dispatch` (#72).
5. Con `run-name`, la API devuelve en `name` el título del run y no el
   workflow; la identidad sale ahora de `path` (#74).
6. Si `main` avanza mientras el worker corre, el resultado auténtico se
   rechazaba; ahora se acepta un SHA del run que sea ancestro de `main` (#76).

## Resultado

- La cadena funciona de punta a punta. En el #71, la recuperación manual
  (`workflow_dispatch` del publicador) re-despachó la solicitud pendiente; el
  worker revisó y el publicador dejó la memoria en `complete_claim`, sin
  solicitudes pendientes y con F1 Critical (`eval()` sobre el contenido del
  archivo).
- Brecha que impide activarlo: el publicador coordinado solo escribe el bloque
  oculto de memoria y no muestra los hallazgos. Quien abre el PR ve un
  comentario sin contenido visible.
- Casos del checklist que no se ejercieron por esa brecha: descarte
  concurrente, explicación, otra revisión del mismo SHA y los casos
  representativos de T05 sobre un comentario visible.

## Decisión por opción

- **Revisor coordinado: volver a current.** Se repone `ai-review.yml` en el
  repo central (#77). Reactivarlo exige primero la capa visible del publicador,
  como tarea aparte.

## Ensayo de retorno

Tras el #77, un segundo push al #71 (0a88e65) hizo correr el revisor actual
sobre la memoria schema 3 que dejó el coordinador. El revisor la leyó y la
actualizó: conservó F1 Critical con el mismo id en «Siguen abiertos», agregó
F2 a F5 como nuevos, avanzó la generación de 2 a 3 sin cambiar el schema, dejó
un único comentario identificado por el SHA y la memoria quedó en
`complete_claim` sin solicitudes pendientes. El escritor compatible continúa
publicando sobre la memoria ampliada, como pide el plan.
- **D0: revisión completa en el segundo push**, ejecutada en el PR #65
  (detalle en `D0-C0.md`).
- **C0: no se activa.** No tiene pares propios congelados ni medición; medirlo
  sería un encargo nuevo.
- **Precisión con jueces: cerrada sin decidir.** La adjudicación ciega no
  corrió; ninguna decisión de este plan depende de ella.
- **Consumidores** (summonaikit-claude y los otros dos repos del rollout):
  quedan fuera del plan como tarea de despliegue, después de la capa visible.
  `gon0801/openclaw` y `gon0801/Orbit` no existen con esos nombres; la tarea
  tiene que confirmar los repos reales.

## Costo

La medición de T16 costó $110.6439 (`D0-C0.md`). Durante el piloto, el
coordinador activo revisó el PR de prueba y también los PRs de arreglo que se
abrieron mientras tanto: 11 corridas del worker con opencode-go, de las que
unas 9 llegaron al modelo. Con la mediana de opencode-go en E1 ($0.5135) son
unos $4 a $5. Es una estimación: `close-result` descarta `total_cost_usd`, así
que el camino coordinado no conserva el costo (fila en Plans.md).
