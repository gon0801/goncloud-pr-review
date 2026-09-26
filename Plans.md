# Plans — goncloud-pr-review

Plan de mejoras del revisor automático de PRs. PR A implementado y medido el 2026-09-26 (PR #9); PR B pendiente.

## Diagnóstico (medido el 2026-09-25)

- Por revisión, ~45 s son instalar Claude Code y LiteLLM y ~5 s arrancar el proxy. El resto, entre 3.5 y 8 min, es el modelo explorando el repo: de 41 a 54 turnos en PRs normales.
- En los últimos 40 PRs de cada repo (120 PRs, medido el 2026-09-26), 35 tienen comentario del revisor y 7 de esos 35 muestran corte por el tope de 60 turnos en su última revisión (20%): Orbit 4 de 11, openclaw 2 de 21, summonaikit-claude 1 de 3. Salieron con el aviso "Revisión incompleta". El corte por push individual es mayor que este 20%, porque el comentario solo conserva la última vuelta de cada PR (pendiente de medición vía logs de Actions).
- Los PRs tienen en promedio 2.5 pushes (Orbit 2.8, openclaw 2.5, summonaikit-claude 2.3). Cerca del 60% de las revisiones son segundas vueltas del mismo PR que hoy se revisan desde cero.
- En cada push el revisor puede volver a sacar hallazgos que ya se descartaron. Se vio en el dogfood de los PRs #2 y #4.

## PR A — Más rápida

| # | Tarea | Estado |
|---|---|---|
| A1 | Guardar en caché (`actions/cache`) el paquete de Claude Code y el venv de LiteLLM, con llave por versión fijada. Meta: instalar pasa de ~45 s a ~15 s. | cc:done — medido: instalar pasa de ~45 s a ~7 s con caché caliente (2º push en adelante; la caché de GitHub se comparte solo dentro del mismo PR) |
| A2 | Que `prepare` precalcule el contexto sin gastar turnos: `callers.txt` (con `git grep`, quién usa cada símbolo cambiado en el diff), `tests.txt` (pruebas que mencionan los archivos cambiados) y `conventions.md` (CLAUDE.md/AGENTS.md recortados desde la rama base, igual que `.github/ai-review.md`, para que un PR no cuele instrucciones como contexto). El prompt manda leer esos 3 archivos antes de explorar. | cc:done — sin efecto medible en turnos (mediana 56 → 57.5 en la comparativa); se queda como pista para el modelo |
| A3 | Prompt con presupuesto explícito: cuántos turnos tiene, agrupar lecturas independientes en un mismo turno, y no leer planes, ledgers ni evidencias (`Plans.md`, `docs/evidencia/`, `.saikit/`, `out/`) salvo que el diff los toque. | cc:done |
| A4 | Tope de turnos según tamaño del diff, solo hacia arriba: 60 por default y 80 si el diff pasa de 20 archivos o 300 KB. El tiempo por intento es 15 s por turno permitido (mínimo 5 min), con un presupuesto de 22 min para el paso Review; un intento cortado por tiempo no se reintenta. Los topes 25/40 del primer intento duplicaron las revisiones cortadas y se descartaron. | cc:done — medido: revisiones cortadas 3/10 → 0/10 |
| A5 | Cerrar el issue #5 completo, punto por punto: (1) el aviso no dice "revisión anterior" cuando el sticky es solo-aviso; (2) `install` falla suave en Python en vez de depender de `continue-on-error`; (3) tiempos con margen bajo el límite de 30 min (va en A4); (4) `disabled` sin distinguir mayúsculas; (5) README sin contradicciones: color del check, si corre Publish, rango de duración, y tabla con `provider` inválido y fallas de Gate/Prepare; (6) pruebas: `disabled:` presente en la plantilla y `assertNotIn(SHA)` en error+sticky; (7) `redact()` también en el camino de error de Publish; (8) con `disabled=true` no se corre checkout (hoy un checkout fallido deja rojo el check apagado); (9) la clave `error` de `result.json` se separa de la salida cruda del CLI (`ai_review_error`). | cc:done |

**Metas del PR A:** mediana de turnos en primera revisión de ~45 a ≤ 30; revisiones cortadas por tope de ~20% a < 5% (medido igual que el Diagnóstico: sobre el último comentario de cada PR con revisión); tiempo total por revisión de 4–9 min a 2–5 min.

**Resultado medido (2026-09-26).** Comparativa en 5 PRs reales (openclaw #161, #175 y #160; Orbit #345 y #342), cada uno revisado 2 veces con `main` y 2 con el PR A, en la misma corrida:

| | Antes (`main`) | Después (PR A) |
|---|---|---|
| Revisiones cortadas o incompletas | 3 de 10 (+1 con cobertura parcial) | 0 de 10 |
| Hallazgos High | 1 | 2 (Orbit #345 en las 2 corridas) |
| Mediana de turnos | 56 | 57.5 |
| Tiempo promedio de revisión | 373 s | 394 s |
| Instalación, 2º push en adelante | ~45 s | ~7 s |

Meta de cortadas: cumplida. Meta de turnos (≤ 30) y de tiempo (2–5 min): no cumplidas; la revisión sigue en ~6 min. La palanca de velocidad real es el PR B, que en los pushes siguientes revisa solo lo que cambió.

## PR B — No repetir hallazgos

| # | Tarea | Estado |
|---|---|---|
| B1 | Memoria en el propio comentario fijo: además del SHA revisado, un bloque oculto con la lista de hallazgos (id estable, archivo, línea, gravedad, título, estado). Sin base de datos, sobrevive entre corridas. El modelo lo emite antes de la línea `COVERAGE:` (nunca después: `split_coverage` descarta lo que va detrás y los recortes a 50.000/65.000 cortan por el final); su tamaño se reserva dentro de esos presupuestos. | cc:done (35262e6) — bloque `ai-review:findings` JSON, tope 8 KB, re-emitido tras el SHA |
| B2 | Segunda vuelta incremental: desde el push 2, `prepare` calcula qué archivos cambiaron desde la última revisión y le pasa al modelo los hallazgos anteriores. El modelo verifica los abiertos contra lo que cambió, marca resueltos y busca bugs nuevos solo en lo que cambió. Meta: ≤ 20 turnos en esas vueltas; el tope es 30 si el push incremental es chico (≤ 30 KB y ≤ 5 archivos) y el normal si no. | cc:done (35262e6) — `prev.json`→`prepare` angosta el diff, `prev_findings.md` al modelo, tope 20 turnos |
| B3 | Candado determinista contra falsos "resuelto": un hallazgo solo pasa a resuelto si cambió alguno de sus archivos (donde está el problema o donde va el arreglo, por ejemplo las pruebas que usan una función renombrada) desde la última revisión. Un hallazgo previo cuyo archivo cambió en ese push y volvió al contenido base se resuelve solo; un hallazgo nuevo nunca nace resuelto. | cc:done — archivos relacionados por hallazgo tras el e2e del PR #15 (un arreglo en las pruebas dejaba el hallazgo abierto para siempre) |
| B4 | Descartar a mano: un comentario `ai-review: descartar F3` (o `descartar todo`) en el PR, de un dueño o colaborador con permiso de escritura, se aplica en el siguiente push y ese hallazgo no vuelve a salir. Comentarios de terceros se ignoran. El diseño define con qué endpoint y token se verifica el permiso de escritura. | cc:done (35262e6) — endpoint `GET /repos/{repo}/collaborators/{user}/permission` con `github_token`; writer+ = admin/maintain/write |
| B5 | Comentario con secciones: "Nuevos en este push", "Siguen abiertos", y plegados "Resueltos" y "Descartados". El veredicto cuenta solo los abiertos. | cc:done (35262e6) — `compose_with_findings`, veredicto generado solo-abiertos |
| B6 | Si el modelo no entrega el bloque de hallazgos bien formado, se cae a la revisión completa actual. Nunca se pierde una revisión por esto. En la caída se conserva el último estado parseable. Prueba unitaria: bloque bien formado sobrevive al parseo de cobertura y al recorte; bloque ausente o roto cae a revisión completa. | cc:done (35262e6) — bloque ausente/roto publica el texto y conserva estado + aplica descartes |
| B7 | Rebase o force-push: si el SHA anterior ya no es ancestro del nuevo, revisión completa otra vez, conservando los descartes. | cc:done (35262e6) — `decide_mode` por `merge-base --is-ancestor`; descartes pegajosos |

**Resultado del e2e (2026-09-26, PR #16 sobre este código).** Push 1 completo: 2 bugs sembrados, F1 Critical y F2 High, cada uno con su archivo relacionado. Push 2, arreglo solo en las pruebas: F2 pasó a resuelto por su archivo relacionado y F1 siguió abierto (incremental, instalación desde caché). Descarte de F1 + push 3: "sin problemas abiertos (1 resuelto, 1 descartado)" y F1 no se volvió a describir. Re-run del mismo commit (revisión completa con estado previo): mismos ids, nada pisado ni duplicado, F1 no reapareció.

## Cómo se prueba

- **Unitarias** para cada regla: parseo del estado, autorización de descartes, candado de "resuelto", continuidad de ids, caída a revisión completa y rebase.
- **Real, en el repo central (dogfood):** PR con bugs sembrados → push 2 que arregla uno → debe mostrar 1 resuelto y 1 abierto sin repetirlo → comentario `descartar` → push 3 lo muestra descartado. Se miden turnos y tiempo del push 1 contra el 2.
- **Comparativa** en 5 PRs reales de Orbit y openclaw (re-run dos veces sobre el mismo commit): turnos, tiempo y cuántas revisiones se cortan, antes y después.
- Los 3 repos usan la action `@main`, así que mergear es poner en producción en los tres a la vez. Primero va el dogfood en el repo central; si algo sale mal, el revert es un solo PR.

## Lo que no cambia

El modelo (DeepSeek V4.1 Flash vía OpenCode Go), un solo comentario por PR, drafts y forks fuera, y la protección contra fallas de proveedor (check verde con aviso).

## Fuera de alcance por ahora

Comentarios en la línea exacta del diff, responderle al revisor en el PR, y repos privados en runner propio (goncloud).
