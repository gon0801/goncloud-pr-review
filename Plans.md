# Plans — goncloud-pr-review

Plan de mejoras del revisor automático de PRs. PR A implementado y medido el 2026-09-26 (PR #9); PR B implementado y probado en real el 2026-09-26 (PR #13).

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

## Propuesta siguiente — pendiente de ejecución

La exclusión de conversación e inline anterior corresponde a PR A y PR B. La propuesta siguiente los estudia por separado; no cambia el alcance ya entregado.

Diseño: [Mejoras del revisor](docs/reviewer-improvements-design.md). Plan detallado: [Implementación por bloques](docs/reviewer-improvements-plan.md).

El usuario autorizó escribir el diseño y el plan, no implementar. Todos los bloques siguientes permanecen sin iniciar. Los IDs son etiquetas del plan, no números de PR. Autorización de David (2026-09-27): ejecutar el plan completo (B0 a U1, y luego X0 y X1), E1 sin mantenedor humano, merges por claw con la herramienta de saikit, y revisiones pagadas con DeepSeek V4.1 Flash avisando por Telegram al empezar cada tanda.

| Tarea | Contenido | Criterio de cierre | Depende de | Estado |
|---|---|---|---|---|
| B0 | Plan y candados: versionar plan/diseno/Plans, quality-kit y hooks; ruff format + lint sin cambio de conducta. | pre-commit 8/8 verde, unittest 191 OK, AST identico salvo renombres lint | Ninguna | cc:done (PR #24, merge 749a6f5; bootstrap autopilot PR #25, merge 1f4488b; evidencia docs/evidence/reviewer/B0.md) |
| Q0 | Validación por bloques y particiones de CI. | Unión de particiones idéntica al descubrimiento completo, sin duplicados. | Ninguna | cc:done (PR #28, merge 2a0e7d8; evidencia docs/evidence/reviewer/Q0.md) |
| P0 | Regresión del recorrido del proveedor, seguimiento #23. | Subproceso real prueba recuperación, límites y aislamiento de credenciales. | Q0 | cc:done (PR #29, merge 09f7da4; evidencia docs/evidence/reviewer/P0.md) |
| E0 | Comparador reproducible de revisiones. | Rechaza pares de SHA distintos y reporta denominadores y datos ausentes. | Q0 | cc:done (PR #31, merge 7e2dfac; enmienda multi-producto E1b en PR #35, merge c2cd83f; evidencia docs/evidence/reviewer/E0.md) |
| E1 | Corpus y medición inicial. | 30 PRs, al menos diez pares de pushes, salidas congeladas y adjudicación. | E0 | cc:done con desvío declarado (PR #35, merge c2cd83f; E1a: PR #32 workflow merge 96f46d7, PR #33 proveedor medición merge e8a00f9, PR #34 fallback merge f7f9054; evidencia docs/evidence/reviewer/E1.md; adjudicación ciega 62/62 sin mantenedor humano, consenso sha256 bd839224). Criterio NO cumplido literalmente: corpus congelado 20/30 PRs, 6 pares de pushes (<10) y partición 13 ajuste / 7 reservada frente a la 20/10 del plan; aprobado así en r3 y superados desde entonces sus pendientes: tanda de 20 ejecutada (autorizada por David 2026-09-27 20:42 EDT) con retanda vía proveedor directo E1aP, 20 salidas retenidas en salidas/ con indice.json, CodeRabbit capturado exact-SHA en 5 casos e informe end-to-end exit 0; los 10 PRs restantes no existen al congelar (repo con 26 PRs, 20 merged); el corpus se extenderá cuando el repo los acumule |
| M0 | Dominio y lector compatible de memoria. | Legacy y v2 conservan IDs, descartes y cursor; corrupción no equivale a vacío. | Q0 | cc:done (PR #37, merge 1174b60; evidencia docs/evidence/reviewer/M0.md; residuales r2/r3 consumidos por M1 salvo schema:1 explicito -> Future(1), abierto) |
| M1 | Persistencia sin pérdida silenciosa. | Desborde no avanza SHA ni pierde estado; retorno a lector compatible probado. | M0 | cc:done (PR #38, merge 08286ef; evidencia docs/evidence/reviewer/M1.md; prueba real state_schema:v2 OK sobre 2d06702; escritura v2 desactivada por defecto; 11 residuales Claude r4 + AI + CodeRabbit al pie) |
| F0 | Identidad y evidencia de hallazgos. | IDs estables en movimientos comprobados; ambigüedad no fusiona bugs. | M1 | cc:done con desvío declarado (PR #39, merge 538bcf7; evidencia docs/evidence/reviewer/F0.md; prueba real dos-bugs-igual-titulo OK con finding_identity=anchors; default current; anchors declarado, no cableado al pipeline; DESVÍO: la identidad por anchors inerte hasta cablear el pipeline y habilitar escritura v2 — la garantía de no-fusión por título no está viva en producción; residuales r1-r8 + AI F1-F9 + CodeRabbit al pie) |
| D0 | Experimento de delta real. | Cumple los criterios de calidad y ahorro del plan, o queda desactivado. | F0, E1 | reemplazada por el plan 2026-10-05 (T14, T15, T07-T08 y T10, T09) |
| C0 | Experimento de contexto selectivo. | Mejora un caso confirmado entre archivos sin degradar calidad, o queda desactivado. | F0, E1 | reemplazada por el plan 2026-10-05 (T14, T15, T07-T08 y T10, T09) |
| C1 | Reglas por carpeta. | Precedencia y límites comprobados; reglas leídas de la base confiable. | C0 | fuera de alcance (2026-10-10): depende de C0, que quedó apagada sin pares propios para medirla |
| C2 | Evidencia de CI. | Checks del SHA exacto y degradación explícita ante permisos insuficientes. | C0 | fuera de alcance (2026-10-10): depende de C0, que quedó apagada sin pares propios para medirla |
| U0 | Publicación serializada. | Carreras reproducibles no pierden decisiones confirmadas. | M1, F0 | reemplazada por el plan 2026-10-05 (T14, T15, T07-T08 y T10, T09) |
| U1 | Comandos en comentarios. | Explicar, descartar y revisar funcionan sin push y respetan permisos e idempotencia. | U0 | reemplazada por el plan 2026-10-05 (T14, T15, T07-T08 y T10, T09) |
| X0 | Decidir continuación por archivos. | Medición del umbral y decisión o subplan, sin implementación automática. | E1 y medición posterior | Condicionado |
| X1 | Decidir comentarios inline. | Evidencia de necesidad y aceptación del cambio de contrato visible. | U1 y evaluación de uso | Condicionado |

## Corrección y cierre del revisor (plan 2026-10-05)

Diseño: [Arquitectura de correcciones](docs/reviewer-corrections-architecture.md). Plan: [Plan de correcciones 2026-10-05](docs/superpowers/plans/2026-10-05-revisor-correcciones.md).

Autorización de David (2026-10-05): ejecutar T01 a T15 por loop, con merges por claw; T16 (piloto en repos reales y mediciones pagadas) espera su go.

| Tarea | Bloque | Entregable | Depende de | Estado |
|---|---|---|---|---|
| T01 | R0 | Rutas Git exactas | Base verificada | cc:done (PR #42, squash 91e7c85; evidencia docs/evidence/reviewer/R0.md) |
| T02 | R0 | Búsqueda de contexto con resultado explícito | T01 | cc:done (PR #42, squash 91e7c85; evidencia docs/evidence/reviewer/R0.md) |
| T03 | R0 | Presupuestos medidos sobre bytes escritos | T01 | cc:done (PR #42, squash 91e7c85; evidencia docs/evidence/reviewer/R0.md) |
| T04 | S0 | Codec schema 3 y escritor compatible operativo | Base verificada | cc:done (PR #44, squash 9c93844; evidencia docs/evidence/reviewer/S0.md) |
| T05 | S1 | Capacidad reservada y desborde sin pérdida | T04 | cc:done (PR #46, squash 1bd1542; evidencia docs/evidence/reviewer/S1.md) |
| T06 | F1 | Identidad F0 conectada al recorrido completo | T01–T04 | cc:done (PR #48, squash b2b82e4; evidencia docs/evidence/reviewer/F1.md) |
| T07 | U0 | Solicitudes y transiciones puras | T04 y T05 | cc:done (PR #50, squash 51025e4; evidencia docs/evidence/reviewer/U0.md) |
| T08 | U0 | Publicación autenticada y recuperación | T06 y T07 | cc:done (PR #50, squash 51025e4; evidencia docs/evidence/reviewer/U0.md) |
| T09 | U1 | Comandos procesados sin push | T05, T06 y T08 | cc:done con desvío declarado (PR #52, squash 657c283; evidencia docs/evidence/reviewer/U1.md; desvío: T09 añade parse_comando_reconcile y collect_dismissals queda para el flujo run/publish sin tocar) |
| T10 | U0/U1 | Coordinador y worker separados | T08 y T09 | cc:done (PR #54, squash 4f9b263; evidencia docs/evidence/reviewer/UW.md) |
| T11 | Instalación | Instalador atómico y ensayo de retorno | T10 | cc:done (PR #56, squash a3b4891; evidencia docs/evidence/reviewer/IN.md) |
| T12 | E2 | Comparación por producto y pares válidos | Base verificada | cc:done (PR #58, squash 47336dc; evidencia docs/evidence/reviewer/E2.md) |
| T13 | E2 | Adjudicación versionada y muestra reservada | T12 | cc:done (PR #58, squash 47336dc; evidencia docs/evidence/reviewer/E2.md) |
| T14 | D0 | Delta real y fallback a revisión completa | T03, T06 y T08 | cc:done (PR #60, squash ca5a7a5; evidencia docs/evidence/reviewer/D0.md) |
| T15 | C0 | Contexto seleccionado con procedencia | T02 y T14 | cc:done (PR #62, squash bebd325; evidencia docs/evidence/reviewer/C0.md) |
| T16 | Activación | Piloto, medición y decisión registrada | T05, T11 y T13–T15 | cc:done con desvío declarado: medición de 63 corridas ($110.6439) y decisión D0 opción 2 ejecutada en el PR #65. Piloto del revisor coordinado en el repo central: ronda 1 (#68-#77, sin capa visible), ronda 2 (#79-#86, pérdida de hallazgos) y rondas 3 y 3b (#87-#98) con descarte concurrente, explicación, re-revisión del mismo SHA, resolución vista en el central y worker con base desfasada; el central corre el coordinado (#96). Desvíos que siguen: C0 no se activa (sin pares propios) y la precisión no tiene jueces. Evidencia: docs/evidence/reviewer/T16-piloto.md, T16-piloto-2.md y D0-C0.md |

### Residuales

| Origen | Nota | Estado |
|---|---|---|
| T16 consumidores | desplegar el coordinador en gon0801/summonaikit-claude, gon0801/goncloud-openclaw y gon0801/goncloud-Orbit (nombres reales; los tres públicos) después del piloto | cerrada (summonaikit-claude #370, goncloud-openclaw #254, goncloud-Orbit #416 con el check requerido review→reconcile; central re-fijado a 9fa765d en #107) |
| T16 ronda 3b bloque con memoria | con hallazgos previos en modo completo el modelo respondió en prosa sin bloque ni COVERAGE en 3 de 5 revisiones (ronda 3 caso 3; #97 pushes 2 y 3), así que su «resuelto» no llega al coordinador; el mensaje solo recordaba el formato en incremental | abierta (#98: el mensaje de la revisión completa con memoria pide el bloque y COVERAGE; comprobar en el central) |
| T16 #89 F2 | vocabulario de identidad duplicado (titles/current) sin normalizar en la frontera; normalizar una vez en ReviewPolicy | abierta |
| T16-piloto-2 instalador (revisión de #87) | Low tardío de ai-review en 31c0e00: los reintentos del instalador no vuelven a fusionar main si main avanza entre intentos (la otra mitad, el rollout que decía "el modo actual no cambia", quedó corregida en la limpieza del plan) | abierta |
| T16 #83 Detalle | la prosa del revisor en Detalle no neutraliza marcadores `<!-- ai-review:* -->` (misma familia que #83 F2, preexistente) | abierta |
| Issue [#99](https://github.com/gon0801/goncloud-pr-review/issues/99) | Revisor coordinado: robustez del publicador, el worker y los comandos (25 puntos no bloqueantes) | abierta |
| Issue [#100](https://github.com/gon0801/goncloud-pr-review/issues/100) | Instalador y plantillas: robustez de la instalación (9 puntos no bloqueantes) | abierta |
| Issue [#101](https://github.com/gon0801/goncloud-pr-review/issues/101) | Dominio: identidad, memoria y presupuesto del comentario (15 puntos no bloqueantes) | abierta |
| Issue [#102](https://github.com/gon0801/goncloud-pr-review/issues/102) | Contexto, diff y runtime de la revisión (prepare/run) (14 puntos no bloqueantes) | abierta |
| Issue [#103](https://github.com/gon0801/goncloud-pr-review/issues/103) | Higiene de pruebas: discriminantes, fragilidad y duplicación (11 puntos no bloqueantes) | abierta |
| Issue [#104](https://github.com/gon0801/goncloud-pr-review/issues/104) | Evaluación v2 (E2): normalización, adjudicación y recolección (14 puntos no bloqueantes) | abierta |

Las filas cerradas de esta tabla están en [plans-historico.md](docs/evidence/reviewer/plans-historico.md).

### Cierre

Cierre 2026-10-08T10:03:16Z: T01-T15 mergeadas en main (bloques #42, #44, #46, #48, #50, #52, #54, #56, #58, #60, #62 con ledgers #43, #45, #47, #49, #51, #53, #55, #57, #59, #61, #63; plan versionado en #41; ultimo squash 04a0b8d). Comprobacion de los cinco fallos del plan, cada prueba existe en origin/main (git grep -- tests/) y la bateria CI RID 37760122162 esta success sobre 04a0b8d: (1) ruta con Unicode, tab, salto de linea o `-->`, la revision usa el archivo correcto -> GitPaths.test_special_paths_roundtrip (tests/test_review.py:7055, T01); (2) busqueda truncada que todavia no encontro tests, el contexto informa busqueda incompleta -> ContextSearch.test_test_found_after_205_source_matches (tests/test_review.py:6705, T02); (3) descarte mientras termina un worker anterior, el descarte sobrevive -> RequestTransitions.test_descarte_entre_preparacion_y_resultado (tests/test_review_coordinator.py:188, T07); (4) respuesta de PATCH perdida o capacidad agotada, no se repite el efecto -> CheckpointRecovery.test_patch_response_lost (tests/test_review_coordinator.py:626, T08); (5) reintento fallido o revision sin pareja en la evaluacion, el informe conserva fallo, costo y causa -> ComparisonV2.test_failed_attempt_then_success_reports_request_duration_and_unknown_cost (tests/test_compare_reviews.py:599, T12). T16 (piloto en repos reales y mediciones pagadas) espera go de David.

Cierre 2026-10-10: T16 cerrada (piloto en el repo central, rondas 1 a 3b). Coordinado activo en el central y los tres consumidores. El seguimiento no bloqueante vive en los issues #99 a #104.
