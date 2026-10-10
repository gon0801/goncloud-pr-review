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
| C1 | Reglas por carpeta. | Precedencia y límites comprobados; reglas leídas de la base confiable. | C0 | cc:TODO |
| C2 | Evidencia de CI. | Checks del SHA exacto y degradación explícita ante permisos insuficientes. | C0 | cc:TODO |
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
| T16 | Activación | Piloto, medición y decisión registrada | T05, T11 y T13–T15 | cc:done con desvío declarado: medición de 63 corridas ($110.6439) y decisión D0 opción 2 ejecutada en el PR #65; piloto del revisor coordinado en el repo central (PR de prueba #71) con seis defectos corregidos (#68, #70, #72, #74, #76; y #77 para volver) y la cadena funcionando de punta a punta, pero sin capa visible en el publicador, así que vuelve a current (#77). Desvíos: C0 no se activa (sin pares propios), precisión sin jueces, casos de descarte/explicación/re-revisión no ejercidos y consumidores fuera del plan. Evidencia: docs/evidence/reviewer/T16-piloto.md y D0-C0.md |

### Residuales

| Origen | Nota | Estado |
|---|---|---|
| T16-f15 análisis | full no forzada por rebase resuelve contra todo lo revisado (rutas_de_cambio, rama por defecto): comportamiento previo a T16, su efecto en falsos resueltos no esta medido; informativo, no bloquea (análisis en docs/evidence/reviewer/D0-C0.md) | abierta |
| T16-f11 análisis | defecto de produccion: en modo incremental el modelo emitía el bloque de hallazgos sin el cierre `-->` (o con el cierre en otra línea) y la corrida perdía hallazgos nuevos y resoluciones; 7 de 24 corridas incrementales de evaluation/reviewer/v2/medicion-t16/textos.jsonl (0 de 38 completas). Evidencia: runs 37878698057, 37879482251, 37879515870, 37879576042, 37879918075, 37885194802 (sin cierre) y 37884726453 (cierre en otra línea). Causa: el parser del modelo exigía ` -->` exacto con el JSON completo; ahora recupera el JSON completo, el texto visible ya no arrastra un `<!--` abierto y el prompt repite el cierre | cerrada (#82) |
| T16 #65 ai-review F1 | preparar_lineal ignora su parametro y su docstring no coincide (tests/test_review.py:266); higiene de pruebas, no bloquea | abierta |
| T16 #65 ai-review F2 | preparar_lineal hereda el entorno sin fijar EXTRA_EXCLUDES/MAX_DIFF_BYTES (tests/test_review.py:314); posible flake, CI verde | abierta |
| T16 #65 ai-review F3 | action.yml max_turns documenta tope incremental inalcanzable tras forzar full; actualizar descripcion y prueba o aclarar camino execute-request | abierta |
| T16 #65 ai-review F4 | con diff mayor que MAX_DIFF_BYTES el forzado a full puede dejar archivos del push actual fuera del diff con causa budget (visible en manifest.excluded); priorizar delta o registrar perdida en medicion D0-C0 | abierta |
| T16 #65 ai-review F5 | cuerpo del PR decia no tocar Plans.md pero el diff agrega fila T16-f15; corregir cuerpo o mover fila | abierta |
| T16 piloto | el publicador coordinado solo escribe el bloque de memoria y no muestra los hallazgos; agregar la capa visible (hallazgos abiertos, cobertura y SHA) antes de reactivar el coordinador | cerrada (rama feat/coordinado-capa-visible: revision_visible reusa compose) |
| T16 piloto | ejercer en un piloto con capa visible: descarte concurrente, explicación, otra revisión del mismo SHA y casos representativos de T05 | abierta (ronda 2 detenida en el caso 2 por pérdida de hallazgos; casos 3-5 sin ejercer: docs/evidence/reviewer/T16-piloto-2.md. El modo coordinado NO se despliega en ningún repo hasta mergear el arreglo de T16-piloto-2 y pasar la ronda 3 completa; install.sh --coordinado exige AI_REVIEW_PILOTO=1) |
| T16 consumidores | desplegar el coordinador en gon0801/summonaikit-claude, gon0801/goncloud-openclaw y gon0801/goncloud-Orbit (nombres reales; los tres públicos) después del piloto | abierta (bloqueada por T16 piloto) |
| T16-piloto-2 pérdida | accept_report comparaba cada observación también contra los hallazgos creados en el mismo reporte: con anclas legadas en un solo archivo la segunda casaba con el F recién creado, no estaba en por_id y un continue la tiraba (6 observaciones -> 1 hallazgo en los workers 38017166195 y 38017336748 del #85) | cerrada (PR de este arreglo: se compara solo contra los previos al entrar; una coincidencia sin previo entra como nueva; fixture con las 12 observaciones reales y equivalencia con merge_findings) |
| T16-piloto-2 gate v3 | F2 de #86: cmd_gate leía solo el bloque legado; con la memoria schema 3 del coordinador el revisor directo daba "Sin hallazgos previos" al modelo tras el retorno | cerrada (mismo PR: estado_legado_de_memoria da la vista legada de v2/v3; probado con la memoria real del #85) |
| T16-piloto-2 memoria del worker | el worker no escribe prev.json (lo hace gate, que no corre en el worker): revisa sin hallazgos previos (log "full/no-prev"), el modelo reescribe títulos y emite F-new; con identidad por título cada título reescrito entra como "posible duplicado" y un descartado con otro título reaparece (en el #85 el eval descartado F1 volvió como F2) | abierta (bloquea la ronda 3: execute-request debe escribir prev.json desde el snapshot y la identidad debe aceptar el id que el modelo repite para un previo del mismo archivo, como merge_findings) |
| T16-piloto-2 instalador | tras un squash merge chore/ai-review queda divergente de main y el retorno salía sobre la punta vieja (modify/delete en ai-review.yml y el conjunto coordinado seguía en main) | cerrada (#87). Notas no bloqueantes de su revisión: cada reinstalación tras avanzar main agrega otro commit de merge a chore/ai-review (nunca fast-forward); la fusión se aplica antes del commit del instalador y no se deshace si un paso posterior falla; el doble de pruebas elige un solo merge-base |
| T16-piloto-2 instalador (revisión de #87) | Low tardíos de ai-review en 31c0e00: docs/reviewer-rollout.md todavía dice que el modo actual "no cambia" aunque ahora pone al día la rama propia; los reintentos del instalador no vuelven a fusionar main si main avanza entre intentos | abierta |
| T16-piloto-2 drenado | en el retorno #86 el worker 38017921567 (PR #87) seguía en vuelo al mergear y su aviso al publicador dio 422 (el publicador ya no existía): resultado perdido y una solicitud pending huérfana en la memoria del #87 (inofensiva: el revisor directo la conserva) | abierta (el paso 1 del retorno exige esperar runs del worker in_progress, no solo del publicador; docs/reviewer-rollout.md) |
| T16 #83 Detalle | la prosa del revisor en Detalle no neutraliza marcadores `<!-- ai-review:* -->` (misma familia que #83 F2, preexistente) | abierta |
| T16 piloto | close-result reescribe result.json con el paquete del coordinador y descarta total_cost_usd y usage: el camino coordinado no conserva el costo por corrida; conservarlo en el paquete | cerrada (rama feat/coordinado-capa-visible) |
| T16 capa visible | un comando (descartar, explicar) en issue_comment cambia el bloque pero no re-renderiza la prosa visible: el comentario muestra el estado del último resultado del worker hasta el siguiente; re-renderizar desde el snapshot sin texto del modelo. El resultado de una explicación tampoco se muestra en visible (solo cierra la solicitud) | cerrada (#83: estado_visible re-renderiza veredicto y secciones desde el snapshot tras un comando y agrega la explicación vigente; sha=/completion= no avanzan) |
| T16 #69 CodeRabbit | fijar actions/* a SHA completo en workflows y plantillas: política de todo el repo, no del piloto | abierta |
| T16 #81 F2 | el output reviewed del worker (steps.cerrar.outputs.reviewed) no lo consume el publicador; consumirlo en el PR de activación | cerrada (#83: el publicador corre en otro workflow y solo recibe el result.json; deriva lo mismo de ERROR_KEY. Igualados por presencia de la clave: con motivo vacío el publicador aceptaba el reporte; prueba de equivalencia) |
| T16 #74 | el camino workflow_run de cmd_reconcile todavía toma el workflow de name; con run-name es el título del run, derivarlo del path como metadatos_del_run | cerrada (#83: forma_del_run normaliza API y payload; el workflow sale del path) |
| T16 cierre | conciliacion con rc/t16-piloto: evidencia, brazo delta de e1-measure, script de analisis y rollout del piloto en main; PrepararBrazoDelta afirma el forzado (full, forced-full-t16, delta real conservado) porque el brazo delta solo reproduce incremental sobre el tag t16-candidato-e0898ff | cerrada (rama rc/t16-cierre) |
| R0-r3 CodeRabbit | proc.wait sin timeout en flujo post-EOF de review.py; acotar al deadline restante con stop_proc; sin reproduccion | abierta |
| R0-r3 CodeRabbit | encoding_omissions no acotada al modo incremental en review.py; filtrar por wanted en incremental + prueba | abierta |
| R0-r3 CodeRabbit | R0.md:26 recuento historico ronda 1 dice 11, deberian ser 10 (9 ContextSearch + 1); corregir numero o quitar parentesis | abierta |
| R0-r3 revisor | N3 colision de identidad para rutas no UTF-8 en changed_files (consumido en T04/S0: escape reversible + test_identity_strings_roundtrip, S0.md Reproductores) | resuelta en S0 |
| R0-r3 revisor | N4 pathspecs con globs en git diff y files_matching_base | abierta |
| R0-r3 revisor | N7 higiene mktree-test/mt2 en el checkout principal | abierta |
| R0-r3 revisor | Refactor: duplicacion del recorte y la nota de build_callers entre ramas Complete y Truncated | abierta |
| R0.md:86 | Matiz de redaccion: dice que la vineta no UTF-8 fue reescrita, pero se borro; estado resuelto en seccion Ronda 2 | abierta |
| R0.md:49-52 | build_conventions lee contenido con sh en modo texto; revisar decodificacion | abierta |
| R0.md:49-52 | test_utf8_manifest_matches_file: asercion de seleccion por presupuesto no discrimina sola; cubre igualdad diff_bytes | abierta |
| S0-r5 revisor | N1 review_domain.py:796 state valida str no vacio sin enum (el codigo no trae Running ni enum; el checklist de T07 exige Pending/Running/Finished/FailedRetryable y la arquitectura define RequestState con Running(run_key); T07 quedo done sin declararlo; endurecer a enum o declarar el pendiente) | abierta |
| S0-r5 revisor | N2 review_domain.py:750 rechazo basis_generation bool/neg sin subtest (mutante isinstance sobrevive; caso x cubierto) | abierta |
| S0-r5 revisor | N3 review.py:1884 proxy.wait(timeout=10) en finally puede lanzar TimeoutExpired tras timeout del modelo y tapar ruta infra (preexistente en main) | abierta |
| S0-r5 revisor | N4 S0.md:122 cifra verify-partition 370 desactualizada (sobre 73d7067 da 371: 186+185) | abierta |
| S0-r5 LISTO | policy_digest vacio en revision avanzada | abierta |
| S0-r5 LISTO | prueba de desborde del escritor compatible pendiente | resuelta en S1 (ConservacionAlDesbordar tests/test_review.py:5255; S1.md:42) |
| S0-r5 LISTO | hallazgos legacy con id None pendientes | abierta |
| S0-r5 LISTO | aviso corto review.py:2050 | abierta |
| S0-r5 LISTO | N1-N7 de r1, N1-N2 de r3 y F1-F5 del sticky 7a1e2b1 arrastrados (siguen abiertos) | abierta |
| S1-r3 LISTO | fallback B2 no aplica en rama con budget del camino generico de compose (budget + reviewed=[] + prosa vacia publica texto por defecto); resolver antes de activar perfil en T16 | cerrada (#83: el mensaje B2 se decide antes del presupuesto; con y sin budget sale idéntico) |
| S1-r3 LISTO | con presupuestos custom cuyo margen restante es menor que el aviso (~53 unidades) la salida excede hasta ~52 unidades; CodeRabbit 4197481797 misma familia; resolver antes de activar perfil en T16 | cerrada (#83: prosa_en_presupuesto recorta sin aviso cuando el aviso no cabe; probado margen 0-119 en ambos caminos) |
| S1-r3 LISTO | en compose_with_findings el guard de presupuesto valida solo el bloque, no el fijo completo; con fijos inflados el cuerpo excede el budget; resolver antes de activar perfil en T16 | cerrada (#83: el guard mide el cuerpo fijo completo, bloque incluido; si no cabe, topes actuales) |
| S1-r3 LISTO | slice [:GITHUB_COMMENT_MAX] cuenta caracteres, no bytes (pre-existente en la base, heredado por N1) | abierta |
| S1-r3 revisor N2 | docs/evidence/reviewer/S1.md:46 dice ai-review 0 hallazgos sobre 5e55715; el sticky tiene F1 Medium y F2 Low resueltos; errata historica | abierta |
| S1-r3 revisor N3 | guarda del checkpoint en compose_with_findings sin prueba que la discrimine; la propiedad el checkpoint nunca se recorta se sostiene igual | abierta |
| F1-r2 revisor N3 | vigencia del reporte no se comprueba (T07/T08) | resuelta en U0 (B2 vigencia discriminada por campo, VEREDICTO-U0-r3; evidencia U0.md Ronda 3) |
| F1-r2 revisor N4 | LISTO-F1-r1 dijo 0 hallazgos de ai-review y no era asi; transparencia | abierta |
| F1-r2 revisor N6 | blobs de anclas ilegibles sin omision (solo anchors) | abierta |
| F1-r2 revisor N8 | runtime_terminado solo mira error_max_turns | abierta |
| F1-r2 revisor N9 | boilerplate de las pruebas | abierta |
| F1-r2 revisor N10 | FINDING_IDENTITY invalido tambien falla en publish | abierta |
| F1-r2 revisor N5 | F1.md:79-81 seccion Verificacion del bloque con totales viejos (20 vs 24, 396 vs 400); correccion anunciada en F1.md:51 no aplicada a esa seccion, ver fila F1-r2 CodeRabbit F1.md:79-81 | abierta |
| F1-r2 LISTO | rebase con prev_sha no ancestro usa diff de arboles, ventana mas ancha mitigada por PARTIAL | abierta |
| F1-r2 LISTO | incomplete-prev con arreglo caido antes de prev_sha queda abierto, igual que en main | abierta |
| F1-r2 LISTO | entradas basura de blobs cuando la ruta no existe en HEAD | abierta |
| F1-r2 LISTO | rendimiento de blobs con anchors (~un subprocess por ancla, solo con anchors) | abierta |
| F1-r2 review F5 | F1.md:87 delta vacio no calculable degrada cobertura, docs | abierta |
| F1-r2 CodeRabbit | F1.md:79-81 totales viejos (20 vs 24, 396 vs 400), docs sin repro | abierta |
| F1-r2 CodeRabbit | review.py:2178 rebase full-mode usa base vs prev_sha, sin repro, residual ya declarado rebase prev_sha no ancestro | abierta |
| U0-r3 veredicto N1 | filtro _mismo_target en busqueda de viva sin prueba discriminante (carrera 3 targets re-despacha B en vez de crear C; push a C la crea despues, solo un despacho de mas; prueba propuesta vencido_con_viva_de_otro_target_crea_nueva) | abierta |
| U0-r1/r2 LISTO | obligaciones de vigencia/cobertura solo hasta T14; banner rancio y sha congelado tras migrar (T10/T16) | abierta |
| U0-r1/r2 LISTO | resultado superseded deja job en rojo benigno; legado con id no-FX; explain huerfano; carrera sin CAS entre coordinadores (T10; al habilitar cmd_reconcile serializar escrituras por PR o pedir decision del operador, publish ya serializa por PR) | abierta |
| U0-r1 veredicto | N1, N2, N5-N10 y N12-N14 arrastrados de r1 (no bloqueantes) | abierta |
| U0-r2 LISTO | GC de solicitudes huerfanas sin target era T04; metadata del worker por API si T10 cambia contrato; runbook de limpieza ante duplicados manuales | abierta |
| U0-r3 LISTO | snapshot_a_v3 de sticky invalido parcial; admitir issue_comment con verificacion de permisos (T09); explain con digest del hallazgo (T09) | abierta |
| U1-r2 veredicto N1 | re-autorizacion de explicar sin prueba discriminante (mutante sin solicitante sobrevive; fail-open hoy inalcanzable) | abierta |
| U1-r2 veredicto N2 | consulta caida en re-verificacion deja comando pending varado hasta T11 (sin despacho indebido ni corrupcion) | abierta |
| U1-r2 veredicto N3 | solicitante para origin re-run es dato muerto (nadie construye Origin con login) | abierta |
| U1-r2 veredicto N4 | log solicitud terminada antes de publish_checkpoint (Unconfirmed no persistido, se corrige en siguiente evento) | abierta |
| U1-r2 veredicto N5 | reautorizar no cachea permiso por login (N llamadas con N solicitudes del mismo autor) | abierta |
| U1-r2 veredicto N6 | U1.md:65 fragmento suelto y desglose por clase 6+3+3+6=18 (total cuadra) | abierta |
| U1-r2 veredicto N7 | arrastre r1 N3/N4/N5/N6/N8 (carrera issue_comment T10, parser asimetrico, sin dedupe, explain vencido, sticky vacio) + fail-open sin solicitante | abierta |
| U1-r2 LISTO | recibo de comando optimista aunque la solicitud termine revocada (motivo en tumba y log; render en T11/T16) | abierta |
| U1-r3 review F4 | reautorizar huerfana follow-up (walkthrough comandos correctos, check en pass) | abierta |
| U1-r3 review F3+F5 | docs evidencia (desglose y redaccion, sin repro de codigo) | abierta |
| U1-r3 CodeRabbit | review.py:3083-3094 cache de permisos por login (Trivial, misma familia N5) | abierta |
| U1 cierre | AuthorizedCommand de reconcile crea explain sin digest (dos entradas divergentes para el mismo concepto) | abierta |
| U1 cierre | digest se persiste y nadie lo consume aún (entrega de explicaciones) | abierta |
| U1 cierre | GITHUB_SHA en issue_comment era el tip de la rama default; resuelta en T10 (cmd_reconcile toma head/base vivos del PR por API; activa al instalar las plantillas en T11) | resuelta en T10 |
| U1 cierre | falta el render de los rechazos al usuario (T11/T16; T10 cerro sin render, solo log) | abierta |
| U1 cierre | descartar F1 y F2 multi-id del legado se ignora silenciosamente en reconcile (soportar o rechazo visible) | abierta |
| U1 cierre | escritor legado vivo puede avanzar el cursor por encima de un explicar pendiente + sticky sin previo crea checkpoint vacío (ruido) | abierta |
| UW-r7 review F5 | UW.md:54 sigue citando prueba inexistente en lista de mutantes (parte fuerte corregida; queda nombre muerto documental) | abierta |
| UW-r7 review F9 | guard del coordinador filtra draft/fork pero no la bandera AI_REVIEW_DISABLED (depende de variables del consumidor, T11) | abierta |
| UW-r7 veredicto N1 | falta prueba punta a punta F10->F11 (fallo blando luego recuperacion re-despacha esa failed_retryable); sin prueba de orden autenticacion->fallo; motivo sin validacion/truncado; artifact de solicitud podada deja run en rojo sin dano; abreviaturas con ellipsis en UW.md:54 historico | abierta |
| UW-r7 veredicto N2 | recuperacion por workflow_dispatch solo re-deriva solicitudes de origen push; las de comando varadas (U1-r2 N2) siguen sin derivarse | abierta |
| UW-r6 veredicto N1 | descarga sin diagnostico con pipefail (grep -c falla antes del mensaje) y patron attempt-1 coincide con attempt-10; usar grep -c . \|\| true y patron anclado | abierta |
| UW-r6 veredicto N2 | subtype del resultado viaja en paquete pero el carril workflow_run no lo usa para degradar cobertura ni reintentar (costura de fallos del worker, T11) | abierta |
| UW-r6 veredicto N3 | FINDING_IDENTITY no viaja al worker; close-result parsea con current aunque el coordinador opere en anchors (T16) | cerrada (PR de T16-piloto-2 pérdida: el coordinador despacha finding_identity, el worker lo valida y lo exporta al job, execute-request lo escribe en el paquete y close-result parsea con el del paquete. Hoy el coordinador opera en current, así que no cambia las anclas de producción: la pérdida la causaba accept_report) |
| UW-r4 LISTO | flags de aislamiento del runtime sin prueba que los fije; working-directory de preparar contexto sin discriminante (benigno); instalacion y firma del proveedor en T11 | abierta |
| UW cierre | timeout-minutes de las plantillas nuevas sin valor fijado (UW.md:97; AI_REVIEW_DISABLED ya en F9) | abierta |
| UW cierre | secrets.API_KEY vs el nombre real en consumidores (UW.md:97, T11): el worker usa AI_REVIEW_API_KEY y DEEPSEEK_API_KEY, como el escritor actual | cerrada (rama rc/t16-worker-secrets) |
| UW cierre | git fetch sin credenciales en repos privados (UW.md:97, T11) | abierta |
| UW cierre | filtros de issue vs PR en issue_comment (UW.md:97, T11) | abierta |
| UW cierre | intent no numerico en execute-request deja traceback crudo (UW.md:97, T11) | abierta |
| IN-r1 veredicto N1 (ai-review F4) | `gh pr list --head` sin dueño puede casar un PR de fork con la misma rama y editarle título/cuerpo (T11/T16) | abierta |
| IN-r1 veredicto N2 (ai-review F5) | ACTION_SHA sin validar como 40 hex; entrada del operador, sin shell en modo coordinado (T11/T16) | abierta |
| IN-r1 veredicto N3 (ai-review F2) | or-true en lectura de rama confunde fallo transitorio con ausencia; sin corrupcion (POST falla y set -e corta) | abierta |
| IN-r1 veredicto N4 | `rutas` se calcula una sola vez antes de reintentos; si la rama cambia y alguien borró ai-review.yml, sale sin publicar (seguro, sin reintento) | abierta |
| IN-r1 veredicto N5 (LISTO, T16) | ejercer contra API real `sha: null` en path ausente y `truncated: true` en árboles grandes; rama `gh pr edit` del modo coordinado sin prueba propia; falta bandera AI_REVIEW_DISABLED en plantillas nuevas | abierta |
| IN-r1 review F1 Medium | worker instalado sin fetch con credenciales en repos privados (rastreada desde UW; solo públicos por límite gasto $0) | abierta |
| IN-r1 review F7 Low | IN.md:38 dice 6 pruebas, hay 8 (errata documental) | abierta |
| IN-r1 cierre (IN.md) | el modo actual conserva el reset con fuerza de la rama propia solo en la actualizacion trivial de una rama ya existente (preexistente de la base, fuera del delta; el retorno usa via atomica sin fuerza) (T11/T16) | abierta |
| E2-r1 veredicto N1 (ai-review F3) | README v2 sugiere --pairing sobre corpus v1 y sale rc=2 (falta clave tarea); declarar que el corpus v2 con claves CaseKey no existe aun y donde vivira | abierta |
| E2-r1 veredicto N2 | redactar_identificadores_v2 solo redacta configs de 8+ caracteres y reemplazo de producto sin limites de palabra (fuga corta / rompe palabras) | abierta |
| E2-r1 veredicto N3 | experimento sin pares aceptado en silencio; sin rechazo de nombre duplicado; sorts mixtos y defectos no-dict con traceback en vez de falla(); caso desconocido en sin_pareja con motivo enganoso | abierta |
| E2-r1 veredicto N4 | test_removes_product_cues sin fuga cb ni hash Addressed; correr_v2 sin timeout; clases nuevas en ingles vs convencion en espanol | abierta |
| E2-r1 veredicto N5 (LISTO, T16) | T13 sin campo propio desacuerdos (lo cubre desempates); ejecucion fallida todo-null cuenta sin_ejecucion; pendientes recoleccion v2, jueces por congelar, 3 repeticiones T16, D0/C0 sin autorizar | abierta |
| E2-r1 review F4 | ObservationKey con texto exacto de configuracion en vez de configuration_digest (equivalente a efectos de identidad; boceto declarativo) | abierta |
| E2-r1 review F5 | pairing.json con lista de jueces vacia (estado honesto, causa registrada) | abierta |
| E2-r2 veredicto N1 | emoji de dos code points (U+26AA U+FE0F): resuelto en r4, FE0F opcional en RE_MARCA_SEVERIDAD_V2 (scripts/build_adjudicacion_ciega.py:29-36) con prueba test_adjudicacion_ciega.py:341 (#### ⚪️ Low, base + FE0F) y 0 residuos en barrido de corpus real | resuelta en E2 r4 |
| E2-r2 veredicto N2 | separador distinto de medio/pipa deja resto huerfano (cosmetico) | abierta |
| E2-r2 veredicto N3 | severidad sin emoji o badges sin emoji sobreviven (heuristica anclada al emoji a proposito, README lo dice) | abierta |
| E2-r2 veredicto N5 | titulos en negrita de CodeRabbit quedan como primera linea de evidencia; T13 decidio en r3 que normalizacion v2 quita marca de severidad con enfasis (RE_MARCA_SEVERIDAD_V2), no enfasis de titulo; pendiente decidir con recoleccion v2 / antes de T16 | abierta |
| E2-r3 veredicto N1 | [^\x00-\x7F] admite no-ASCII como emoji y come rayas/comillas tipograficas ante palabra de severidad (0 casos en corpus real); acotar a pictogramas excluyendo U+2000-U+206F | abierta |
| E2-r3 veredicto N2 | marca pegada a puntuacion ASCII no se quita por lookbehind de espacio; sin ocurrencias en corpus real, cubrir con recoleccion v2 | abierta |
| E2-r4 veredicto N1 | generador ciego v2 exige set(particion)==casos observaciones.json; con 26 casos, recoleccion sin los 6 push-casos sale exit 2; decirlo en README v2 junto a recoleccion D0 | abierta |
| E2-r4 veredicto N2 | pairing.json recongelado en r4 es legitimo sin salidas v2 observadas; tras recoleccion, recongelar se declara desvio | abierta |
| E2-r4 review F8 | conteo documental pendiente en E2 (Low, no bloqueante) | abierta |
| D0-r1 veredicto N1 (ai-review F2) | reordenar templates/ai-review-worker.yml: fetch + worktree antes de execute-request, con working-directory pr; si no, D0 siempre fallback en worker real; resolver antes de T16 | cerrada (#83: el worker trae el PR antes de execute-request y lo corre en pr) |
| D0-r1 veredicto N2 | ReviewPolicy nueva cambia digest_de_politica de toda politica existente; fuerza una revision completa por PR y rechaza artifacts en vuelo; anotar en runbook de activacion | abierta |
| D0-r1 veredicto N3 | digest con diff_max_bytes de entorno vs politica_de_revision del coordinador; si difieren, rechaza todos los resultados; derivar de una sola fuente | abierta |
| D0-r1 veredicto N4 | cobertura COMPLETE_CLAIM con omisiones budget del modo historico; T16 no debe tomarla tal cual | abierta |
| D0-r1 veredicto N5 (ai-review F1) | Obligation solo ruta, sin estado de entrega; T16 decide si amplia el tipo | abierta |
| D0-r1 veredicto N6 (ai-review F4) | chunks merge_base..head en incremental: reversion entrega chunk vacio; comportamiento previo, lo mide T16 | abierta |
| D0-r1 veredicto N7 | test fragil sin .git (falla en git archive); usar repo temporal propio o mock de GitRepository | abierta |
| D0-r1 veredicto N8 | D0.md:10 decia base en vez de merge_base | resuelta en D0 r2 |
| D0-r1 veredicto N9 | e1-measure.yml:89 no copia review_context.py (ya faltaba review_domain.py) | abierta |
| D0-r1 veredicto N10 | adaptador calcula modo dos veces (historico + prepare_review); colapsar cuando coordinador sea unico camino | abierta |
| D0-r1 veredicto N10b | memoria legacy reconstruida con base/digest del target: guardas nuevos de base y politica vacuos en camino legacy, reales en coordinador; colapsar con N10 cuando coordinador sea unico camino | abierta |
| D0-r2 proceso | /Users/dn/quality-kit/cross-review.ps1 sigue borrado sin commit; si es intencional, los briefs deben dejar de citarlo | abierta |
| C0-r3 veredicto N1 (kimi) | Atrapar MemoryError alrededor de ast.parse puede ocultar falta de memoria real; acotado a parseo de contenido ajeno, contrato no-parsea→textual | abierta |
| C0-r3 veredicto N2 (kimi) | Prueba depende de que CPython no pueda parsear 200k .b (limite de pila); si algun dia parseara falla ruidoso, no en vacio | abierta |
| C0-r2 veredicto N2 | Aviso simbolos recortados sin prueba discriminante (mutante if False sigue OK; verificado a mano en C0.md) | abierta |
| C0-r2 veredicto N3 | Archivos no .py quedan siempre textual aunque sean llamadas reales en JS/TS; coherente sin analisis para otros lenguajes, se pierde prioridad en repos poliglotas | abierta |
| C0-r2 veredicto N4 | Costo: hasta 2 subprocesos + ast.parse por candidato unico con cache; acotado por CALLERS_MAX_MATCHES/SYMBOLS; C0 apagada | abierta |
| C0-r1 veredicto N1 | Cierre T15 seleccionable por politica vs kwarg selectivo=False; digest no distingue C0; llevar a ReviewPolicy + tipado Literal/enum ContextRef en T16 | abierta |
| C0-r1 veredicto N3 | Refs no se filtran contra exclusiones (DEFAULT_EXCLUDES) ni otros archivos ya entregados; solo descarta archivo origen; ruido acotado | abierta |
| C0-r1 veredicto N4 | Guarda if result.plan.context_refs deja asercion truncada vacia en escenario real; compensa subTest mockeado | abierta |
| C0-r3 review F4 | C0.md:107 corrida verde r3 repite tiempo literal de la roja (3.686s en ambas); pegar tiempo real o declarar misma salida | abierta |

### Cierre

Cierre 2026-10-08T10:03:16Z: T01-T15 mergeadas en main (bloques #42, #44, #46, #48, #50, #52, #54, #56, #58, #60, #62 con ledgers #43, #45, #47, #49, #51, #53, #55, #57, #59, #61, #63; plan versionado en #41; ultimo squash 04a0b8d). Comprobacion de los cinco fallos del plan, cada prueba existe en origin/main (git grep -- tests/) y la bateria CI RID 37760122162 esta success sobre 04a0b8d: (1) ruta con Unicode, tab, salto de linea o `-->`, la revision usa el archivo correcto -> GitPaths.test_special_paths_roundtrip (tests/test_review.py:7055, T01); (2) busqueda truncada que todavia no encontro tests, el contexto informa busqueda incompleta -> ContextSearch.test_test_found_after_205_source_matches (tests/test_review.py:6705, T02); (3) descarte mientras termina un worker anterior, el descarte sobrevive -> RequestTransitions.test_descarte_entre_preparacion_y_resultado (tests/test_review_coordinator.py:188, T07); (4) respuesta de PATCH perdida o capacidad agotada, no se repite el efecto -> CheckpointRecovery.test_patch_response_lost (tests/test_review_coordinator.py:626, T08); (5) reintento fallido o revision sin pareja en la evaluacion, el informe conserva fallo, costo y causa -> ComparisonV2.test_failed_attempt_then_success_reports_request_duration_and_unknown_cost (tests/test_compare_reviews.py:599, T12). T16 (piloto en repos reales y mediciones pagadas) espera go de David.
