# Plans — goncloud-pr-review

Plan de mejoras del revisor automático de PRs. Aprobado para diseño el 2026-09-25; **no implementado todavía**. Orden: PR A, luego PR B.

## Diagnóstico (medido el 2026-09-25)

- Por revisión, ~45 s son instalar Claude Code y LiteLLM y ~5 s arrancar el proxy. El resto, entre 3.5 y 8 min, es el modelo explorando el repo: de 41 a 54 turnos en PRs normales.
- En los últimos 40 PRs de cada repo (120 PRs, medido el 2026-09-26), 35 tienen comentario del revisor y 7 de esos 35 muestran corte por el tope de 60 turnos en su última revisión (20%): Orbit 4 de 11, openclaw 2 de 21, summonaikit-claude 1 de 3. Salieron con el aviso "Revisión incompleta". El corte por push individual es mayor que este 20%, porque el comentario solo conserva la última vuelta de cada PR (pendiente de medición vía logs de Actions).
- Los PRs tienen en promedio 2.5 pushes (Orbit 2.8, openclaw 2.5, summonaikit-claude 2.3). Cerca del 60% de las revisiones son segundas vueltas del mismo PR que hoy se revisan desde cero.
- En cada push el revisor puede volver a sacar hallazgos que ya se descartaron. Se vio en el dogfood de los PRs #2 y #4.

## PR A — Más rápida

| # | Tarea | Estado |
|---|---|---|
| A1 | Guardar en caché (`actions/cache`) el paquete de Claude Code y el venv de LiteLLM, con llave por versión fijada. Meta: instalar pasa de ~45 s a ~15 s. | cc:TODO |
| A2 | Que `prepare` precalcule el contexto sin gastar turnos: `callers.txt` (con `git grep`, quién usa cada símbolo cambiado en el diff), `tests.txt` (pruebas que mencionan los archivos cambiados) y `conventions.md` (CLAUDE.md/AGENTS.md recortados desde la rama base, igual que `.github/ai-review.md`, para que un PR no cuele instrucciones como contexto). El prompt manda leer esos 3 archivos antes de explorar. | cc:TODO |
| A3 | Prompt con presupuesto explícito: cuántos turnos tiene, agrupar lecturas independientes en un mismo turno, y no leer planes, ledgers ni evidencias (`Plans.md`, `docs/evidencia/`, `.saikit/`, `out/`) salvo que el diff los toque. | cc:TODO |
| A4 | Tope de turnos según tamaño del diff, en vez de 60 fijo. Ajustar timeouts para que el peor caso quede con margen bajo el límite de 30 min del job (issue #5). | cc:TODO |
| A5 | Cerrar el issue #5 completo, punto por punto: (1) el aviso no dice "revisión anterior" cuando el sticky es solo-aviso; (2) `install` falla suave en Python en vez de depender de `continue-on-error`; (3) tiempos con margen bajo el límite de 30 min (va en A4); (4) `disabled` sin distinguir mayúsculas; (5) README sin contradicciones: color del check, si corre Publish, rango de duración, y tabla con `provider` inválido y fallas de Gate/Prepare; (6) pruebas: `disabled:` presente en la plantilla y `assertNotIn(SHA)` en error+sticky; (7) `redact()` también en el camino de error de Publish; (8) con `disabled=true` no se corre checkout (hoy un checkout fallido deja rojo el check apagado); (9) la clave `error` de `result.json` se separa de la salida cruda del CLI (`ai_review_error`). | cc:TODO |

**Metas del PR A:** mediana de turnos en primera revisión de ~45 a ≤ 30; revisiones cortadas por tope de ~20% a < 5% (medido igual que el Diagnóstico: sobre el último comentario de cada PR con revisión); tiempo total por revisión de 4–9 min a 2–5 min.

## PR B — No repetir hallazgos

| # | Tarea | Estado |
|---|---|---|
| B1 | Memoria en el propio comentario fijo: además del SHA revisado, un bloque oculto con la lista de hallazgos (id estable, archivo, línea, gravedad, título, estado). Sin base de datos, sobrevive entre corridas. El modelo lo emite antes de la línea `COVERAGE:` (nunca después: `split_coverage` descarta lo que va detrás y los recortes a 50.000/65.000 cortan por el final); su tamaño se reserva dentro de esos presupuestos. | cc:TODO |
| B2 | Segunda vuelta incremental: desde el push 2, `prepare` calcula qué archivos cambiaron desde la última revisión y le pasa al modelo los hallazgos anteriores. El modelo verifica los abiertos contra lo que cambió, marca resueltos y busca bugs nuevos solo en lo que cambió. Meta: ≤ 20 turnos en esas vueltas. | cc:TODO |
| B3 | Candado determinista contra falsos "resuelto": un hallazgo solo pasa a resuelto si su archivo cambió desde la última revisión; si no, sigue abierto diga lo que diga el modelo. Si el archivo volvió a su contenido base (el cambio que lo causó ya no está en el PR), se resuelve solo. El caso cross-file (arreglo en otro archivo) queda para el diseño fino de PR B, sin aflojar este candado. | cc:TODO |
| B4 | Descartar a mano: un comentario `ai-review: descartar F3` (o `descartar todo`) en el PR, de un dueño o colaborador con permiso de escritura, se aplica en el siguiente push y ese hallazgo no vuelve a salir. Comentarios de terceros se ignoran. | cc:TODO |
| B5 | Comentario con secciones: "Nuevos en este push", "Siguen abiertos", y plegados "Resueltos" y "Descartados". El veredicto cuenta solo los abiertos. | cc:TODO |
| B6 | Si el modelo no entrega el bloque de hallazgos bien formado, se cae a la revisión completa actual. Nunca se pierde una revisión por esto. En la caída se conserva el último estado parseable. Prueba unitaria: bloque bien formado sobrevive al parseo de cobertura y al recorte; bloque ausente o roto cae a revisión completa. | cc:TODO |
| B7 | Rebase o force-push: si el SHA anterior ya no es ancestro del nuevo, revisión completa otra vez, conservando los descartes. | cc:TODO |

## Cómo se prueba

- **Unitarias** para cada regla: parseo del estado, autorización de descartes, candado de "resuelto", continuidad de ids, caída a revisión completa y rebase.
- **Real, en el repo central (dogfood):** PR con bugs sembrados → push 2 que arregla uno → debe mostrar 1 resuelto y 1 abierto sin repetirlo → comentario `descartar` → push 3 lo muestra descartado. Se miden turnos y tiempo del push 1 contra el 2.
- **Comparativa** en 5 PRs reales de Orbit y openclaw (re-run sobre el mismo commit): turnos, tiempo y cuántas revisiones se cortan, antes y después.
- Los 3 repos usan la action `@main`, así que mergear es poner en producción en los tres a la vez. Primero va el dogfood en el repo central; si algo sale mal, el revert es un solo PR.

## Lo que no cambia

El modelo (DeepSeek V4.1 Flash vía OpenCode Go), un solo comentario por PR, drafts y forks fuera, y la protección contra fallas de proveedor (check verde con aviso).

## Fuera de alcance por ahora

Comentarios en la línea exacta del diff, responderle al revisor en el PR, y repos privados en runner propio (goncloud).
