# Plan de implementación de las mejoras del revisor

Estado: pendiente de ejecución. El usuario autorizó escribir este plan y prohibió implementar. Autorización de David (2026-09-27): ejecutar el plan completo, de principio a fin (B0 a U1, y luego X0 y X1); las cuatro decisiones quedan registradas en el párrafo de autorización de este documento.

**Objetivo:** mejorar la detección, la memoria y la interacción del revisor mediante entregas que puedan evaluarse por separado.
**Arquitectura:** conservar la action y el comentario fijo. Concentrar las reglas de estado en `review_domain.py`. Separar el contexto cuando se introduzca esa capacidad.
**Tecnología:** Python, unittest, Git, GitHub Actions, Claude Code y LiteLLM con las versiones fijadas por el repositorio.
**Diseño:** [Propuesta de mejoras del revisor](reviewer-improvements-design.md).

Para un futuro ejecutor: lee ambos documentos. Usa `superpowers:subagent-driven-development` o `superpowers:executing-plans` sólo después de recibir autorización de implementación. Una casilla se completa con evidencia, no por haber escrito código. Este plan no autoriza commits, PRs, mediciones pagadas ni merges en esta sesión. La autorización de implementación llegó el 2026-09-27; el párrafo siguiente la registra.

Autorización de David, 2026-09-27 (orden de ejecución del plan):
1. Alcance: el plan completo, de principio a fin (B0 a U1, y luego X0 y X1).
2. E1 sin mantenedor humano: Claude clasifica a ciegas y codex resuelve los desacuerdos.
3. Merges: claw mergea todos los bloques con la herramienta de saikit.
4. Revisiones pagadas autorizadas (corpus E1, comparaciones D0 y C0, PRs de prueba), siempre con DeepSeek V4.1 Flash. Aviso a David por Telegram al empezar cada tanda.

## Preparar cada entrega

1. Confirma qué bloque autorizó el usuario. Conserva las restricciones de esa autorización durante la ejecución.
2. Ejecuta `git fetch origin`. Crea la rama de trabajo desde `origin/main`, después de integrar sus dependencias.
3. Usa la rama `plan/<id>-<nombre>` para el bloque. No mezcles bloques independientes en un PR.
4. Antes de abrir el PR, comprueba que `git log origin/main..HEAD` contiene únicamente sus commits.
5. Antes del cambio, reproduce el caso mediante la prueba focalizada indicada. Cada bug corregido incluye su regresión.
6. Ejecuta las pruebas focalizadas durante la implementación. No ejecutes toda la batería localmente si CI puede ejecutarla.
7. Ejecuta los hooks instalados. Nunca uses `--no-verify`.
8. Abre el PR para activar CI. Ejecuta la batería completa una vez sobre el SHA final del bloque y conserva el enlace.
9. Agrupa los hallazgos de revisión en una ronda. Un bloqueo necesita un comando que lo reproduzca. Aplica las reglas de revisión de AGENTS.md.
10. Para mediciones vivas y releases, incluye la revisión cruzada requerida por el repo. No leas ni imprimas valores de secretos.
11. Prueba primero la revisión candidata en el repo central. Los consumidores usan `@main`; mergear cambia los tres repositorios.
12. Deja el PR listo para revisión. Haz merge únicamente si la autorización de ejecución incluye esa operación. La autorización de David (2026-09-27) la incluye: claw mergea todos los bloques con la herramienta de saikit.

Registra la evidencia por bloque en `docs/evidence/reviewer/<id>.md`: base y head, comando, resultado, enlace de CI, prueba real, revisión y decisión de activación. Esos archivos se crearían durante la ejecución. No existen como comprobantes de trabajo realizado.

## Respetar los contratos globales

- Conserva PR #20 y PR #22. No vuelvas a implementar sus correcciones.
- Mantén el modelo, el proveedor, las herramientas de sólo lectura y las exclusiones de forks y drafts.
- Mantén un comentario del bot hasta que se apruebe expresamente el bloque de comentarios inline.
- Una falla del proveedor no avanza el commit revisado ni elimina la revisión anterior.
- Un descarte confirmado no reaparece por una revisión posterior, un cambio de título o una migración.
- Una declaración de cobertura completa no demuestra que se hayan encontrado todos los defectos.
- No uses resultados de CodeRabbit como verdad de referencia. No compares revisiones de commits distintos.
- Conserva un lector compatible con el estado nuevo al desactivar funciones. No regreses a un escritor que lo pierda.
- Si falta evidencia para activar una optimización, conserva la conducta actual. No conviertas ausencia de datos en aprobación.

## Seguir las dependencias

Los identificadores siguientes son nombres del plan, no números de PR existentes.

| ID | Entrega | Depende de | Cierre observable |
|---|---|---|---|
| Q0 | Preparar la validación por bloques | Ninguna | CI ejecuta todas las pruebas exactamente una vez entre sus particiones. |
| P0 | Cubrir el recorrido del reintento del proveedor | Q0 | El comando real inicia dos procesos ante el error recuperable y conserva los límites. |
| E0 | Construir el comparador reproducible | Q0 | Un informe rechaza pares de SHA distintos y distingue datos ausentes. |
| E1 | Congelar la medición inicial | E0 | Corpus, resultados y adjudicación permiten repetir la comparación. |
| M0 | Introducir el dominio y el lector compatible | Q0 | El código lee estado legado y v2 sin perder identidad. |
| M1 | Activar persistencia sin pérdida silenciosa | M0 | El desborde conserva el estado anterior y no avanza cobertura. |
| F0 | Incorporar identidad y evidencia | M1 | Renombres comprobados conservan IDs y bugs distintos permanecen separados. |
| D0 | Probar el delta real | F0, E1 | La comparación acepta el ahorro sin perder defectos conocidos. |
| C0 | Probar contexto selectivo | F0, E1 | Mejora la muestra de defectos entre archivos sin degradar precisión. |
| C1 | Añadir reglas por carpeta | C0 | Sólo se aplican reglas autorizadas de la base confiable. |
| C2 | Adjuntar evidencia de CI | C0 | Cada check corresponde al SHA revisado y declara su procedencia. |
| U0 | Serializar la publicación | M1, F0 | Una revisión vieja no pisa decisiones confirmadas. |
| U1 | Atender comentarios del PR | U0 | Explicar, descartar y revisar funcionan sin otro push. |
| X0 | Decidir continuación por archivos | E1 y medición posterior | Se acredita el umbral del diseño antes de ampliar el estado. |
| X1 | Decidir comentarios inline | U1 y evaluación de uso | Se acredita la necesidad y se acepta cambiar el contrato visible. |

Orden recomendado: Q0, P0, E0, E1, M0, M1, F0, D0, C0, C1, C2, U0, U1. E0 y M0 pueden prepararse por separado después de Q0. Serializa los bloques que escriban `review.py`, `action.yml` o sus tests. X0 y X1 son decisiones condicionadas, no implementaciones listas para iniciar.

## Crear los archivos en su bloque propietario

| Archivo futuro | Dueño | Contenido |
|---|---|---|
| `scripts/run_test_shard.py` | Q0 | Descubrimiento y partición determinista de unittest. |
| `scripts/compare_reviews.py` | E0 | Validación de pares y cálculo de métricas a partir de salidas ya capturadas. |
| `evaluation/reviewer/corpus.json` | E1 | Identificadores de casos, SHAs, partición de ajuste y evaluación. Sin secretos. |
| `review_domain.py` | M0 | Tipos de estado, lectura, transiciones y contratos de dominio. |
| `review_context.py` | D0 o C0, el primero que se integre | Selección de delta, contexto, reglas y procedencia. |
| `tests/test_review_domain.py` | M0 | Lectura, identidad, migración y capacidad. |
| `tests/test_review_context.py` | D0 o C0 | Alcance, contexto y reglas. |
| `tests/test_review_commands.py` | U0 | Publicación concurrente y comandos. |
| `.github/workflows/ai-review-commands.yml` | U1 | Disparador de comentarios con código confiable. |
| `templates/ai-review-commands.yml` | U1 | Plantilla para consumidores. |

No crees archivos vacíos anticipadamente. Cada bloque incluye su documentación, sus tests y las modificaciones de instalación necesarias. No agregues una base de datos, un servicio ni una cola externa.

## Mantener las funciones nuevas desactivadas hasta validarlas

Introduce estos inputs en `action.yml` y sus pruebas únicamente en el bloque propietario. Pásalos a Python como variables de entorno con el mismo nombre en mayúsculas. Rechaza valores no admitidos antes de llamar al modelo.

| Input propuesto | Valores | Default inicial | Bloque |
|---|---|---|---|
| `state_schema` | `legacy`, `v2` | `legacy` | M1 |
| `finding_identity` | `current`, `anchors` | `current` | F0 |
| `incremental_mode` | `current`, `delta` | `current` | D0 |
| `context_mode` | `current`, `selective` | `current` | C0 |
| `path_rules` | `true`, `false` | `false` | C1 |
| `ci_context` | `true`, `false` | `false` | C2 |

`state_schema=legacy` impide crear nuevos estados v2, pero nunca degrada uno ya guardado. Si el estado existente es v2, conserva ese esquema. Desactivar `finding_identity=anchors` conserva las anclas persistidas y usa sólo las coincidencias inequívocas que pueda establecer. El modo `anchors` requiere estado v2. Los demás controles no cambian el esquema.

Los comandos se habilitan al instalar su workflow en U1 y se deshabilitan retirando ese disparador. La publicación serializada de U0 es un requisito de corrección, no una optimización opcional. Actualiza la lista de archivos del bloque con `action.yml`, sus plantillas y las pruebas de inputs cuando corresponda.

## Q0. Preparar la validación por bloques

**Archivos:** modifica `.github/workflows/ci.yml`; crea `scripts/run_test_shard.py` y `tests/test_test_shards.py`.
**Interfaz:** el script acepta `--shard i/N` y `--verify-partition N`. Usa numeración desde cero.

- [ ] Descubre todos los casos de `unittest` bajo `tests`, ordena sus IDs y asigna cada posición a `posición % N`.
- [ ] Añade pruebas de partición vacía, cantidad impar, ausencia de duplicados y unión idéntica al descubrimiento completo. Rechaza argumentos inválidos.
- [ ] Ejecuta `python3 -m unittest discover -s tests -p 'test_test_shards.py' -v`. Los casos deben fallar antes del cambio y pasar después.
- [ ] Configura dos jobs de batería con `--shard 0/2` y `--shard 1/2`. Añade el candado `--verify-partition 2`.
- [ ] Separa actionlint y futuros checks que lean documentación real en un job breve. No omitas pruebas por el tipo de archivo modificado.
- [ ] Abre el PR y comprueba que la unión de los manifiestos contiene cada ID una vez. No ejecutes otra batería local.

**Cierre:** conserva manifiestos y enlace de CI. Este bloque no modifica el revisor ni se atribuye una mejora de detección. Si un job supera diez minutos más adelante, aumenta las particiones sin recortar la batería.

## P0. Cubrir el recorrido del error del proveedor

**Archivos:** modifica `tests/test_review.py`, clase `RunAgent`. Modifica `review.py` sólo si la prueba descubre un bug reproducible.
**Entrada:** el error exacto observado en #22 con `PROVIDER=opencode-go`. **Salida:** resultado exitoso del segundo proceso o aviso específico tras agotar intentos.

- [ ] Extiende `FAKE_CLAUDE` para entregar respuestas consecutivas mediante una lista controlada por el test.
- [ ] Añade `test_opencode_replay_error_restarts_cli`. Ejecuta el subcomando `run` y afirma dos invocaciones, mismo modelo y resultado final exitoso.
- [ ] Afirma que ambas invocaciones usan `--no-session-persistence`, no usan resume y no reciben el secreto upstream ni el token de GitHub.
- [ ] Añade casos de error persistente y presupuesto insuficiente. Conserva las pruebas de otros 400 y de 401, 403 y 404 sin reintento.
- [ ] Ejecuta `PYTHONPATH=tests python3 -m unittest test_review.RunAgent test_review.ReasoningReplayRecovery -v`.
- [ ] Reproduce el ciclo con las versiones fijadas de Claude Code y LiteLLM contra un proveedor local con credenciales ficticias. Provoca el error sólo en la primera conversación.

**Cierre:** documenta una conversación fallida y otra exitosa, además de CI. No amplíes el matcher sin un mensaje distinto observado. La aceptación no requiere volver a pagar una revisión externa para reproducir un fallo ya controlado.

## E0. Construir el comparador reproducible

**Archivos:** crea `scripts/compare_reviews.py` y `tests/test_compare_reviews.py`. Documenta su uso en `evaluation/reviewer/README.md`.
**Interfaz:** `compare_reviews.py --corpus PATH --observations PATH --judgments PATH --output PATH`.
**Contrato de entrada:** caso, repo, base y head exactos, producto, configuración, intento, resultado, hallazgos, duración, turnos y costo conocido o desconocido.

- [ ] Define una fila de adjudicación por hallazgo con `valid`, `false_positive`, `duplicate` o `unresolved`, además de IDs de defectos conocidos detectados.
- [ ] Añade fixtures pequeños que produzcan exactamente dos hallazgos válidos, uno falso y uno duplicado. Afirma esos conteos literales.
- [ ] Rechaza pares de distinto SHA y filas duplicadas. Excluye `unresolved` del cálculo de precisión e informa cuántos casos excluiste.
- [ ] Informa costo ausente como desconocido. No lo reemplaces por cero ni calcules recuperación cuando no existe un conjunto de defectos adjudicados.
- [ ] Ejecuta `python3 -m unittest discover -s tests -p 'test_compare_reviews.py' -v`.
- [ ] Ejecuta el comando del comparador sobre los fixtures y lee su informe JSON. Afirma conteos, denominadores y exclusiones.

**Cierre:** el comparador funciona sin llamar a modelos ni publicar comentarios. El informe identifica precisión, defectos conocidos omitidos, duplicados, falsos resueltos, cobertura declarada y métricas de tiempo.

## E1. Congelar la medición inicial

**Archivos:** crea `evaluation/reviewer/corpus.json`, `evaluation/reviewer/judgments.jsonl` y `docs/evidence/reviewer/E1.md`.
**Entrada:** E0 y salidas capturadas con acceso autorizado. **Salida:** corpus y baseline congelados.

- [ ] Selecciona 30 PRs, con al menos diez pares de pushes. Incluye casos limpios, renombres, reversiones, cortes anteriores y cambios entre módulos.
- [ ] Asigna 20 PRs a ajuste y diez a evaluación reservada. Mantén todos los commits de un mismo PR en la misma partición.
- [ ] Registra SHA, base, versiones, reglas y configuración. Guarda las salidas originales en una ubicación con retención y acceso documentados.
- [ ] Oculta el producto al adjudicar. Un mantenedor clasifica los hallazgos y otra revisión resuelve desacuerdos. Sin mantenedor humano (autorización de David, 2026-09-27): Claude clasifica a ciegas y codex resuelve los desacuerdos.
- [ ] Marca pares sin salida comparable como pendientes. Si hay límites de CodeRabbit o falta autorización para corridas externas, conserva los datos disponibles y no declares completo el corpus.
- [ ] Genera el informe con E0. Registra conteos y dispersión, no sólo promedios.

**Cierre:** publica el informe sólo cuando existan los pares definidos y la adjudicación. Si la muestra queda incompleta, deja D0 y C0 sin autorización de activación. M0, M1 y P0 no dependen de obtener esos resultados.

## M0. Introducir el dominio y el lector compatible

**Archivos:** crea `review_domain.py` y `tests/test_review_domain.py`; modifica `review.py` para llamar al dominio sin cambiar los comandos públicos.
**Tipos:** define `Revision`, `Snapshot`, `Finding`, `Anchor`, `Evidence`, `LoadResult` y `Transition` con los campos del diseño.
**Interfaces:** `read_snapshot(body: str) -> LoadResult`; `encode_snapshot(state: Snapshot) -> str | CapacityExceeded`.

- [ ] Representa por separado `Valid`, `Legacy`, `Missing`, `Invalid` y `Future`. No uses una lista vacía para representar corrupción.
- [ ] Conserva los campos legados `findings`, `next` y `seen`. Mapea `seen` al cursor nuevo sin inventar anclas ni evidencias.
- [ ] Añade `test_legacy_preserves_ids_dismissals_and_cursor`, `test_missing_differs_from_invalid` y `test_future_version_is_not_legacy`.
- [ ] Rechaza IDs duplicados, tipos inválidos y referencias inconsistentes mediante tests en la frontera de lectura.
- [ ] Mantén la escritura legacy por defecto para estados legacy. Si se lee v2, exige escritura v2 sin pérdida o conserva el estado sin modificarlo.
- [ ] Ejecuta `python3 -m unittest discover -s tests -p 'test_review_domain.py' -v`.
- [ ] Ejecuta las clases existentes `FindingsBlock`, `FindingIds`, `ResolvedLock`, `Fallback` y `ReviewContinuity` como pruebas focalizadas de compatibilidad.

**Cierre:** captura el mismo comentario antes y después de la extracción y verifica sus IDs, estados y cobertura. Esta versión será el punto mínimo de retorno seguro para M1.

## M1. Activar persistencia sin pérdida silenciosa

**Archivos:** modifica `review_domain.py`, `review.py`, `tests/test_review_domain.py`, `tests/test_review.py` y `README.md`.
**Contrato:** el bloque v2 admite como máximo 8000 bytes UTF-8. Conserva también el límite total del comentario.

- [ ] Añade pruebas con Unicode que separen cantidad de caracteres y bytes. Verifica los casos justo en el límite y un byte por encima.
- [ ] Añade `test_overflow_keeps_previous_snapshot_and_reviewed_sha`. Afirma que no cambian rutas, IDs, descartes ni cursor.
- [ ] Permite recortar sólo prosa no esencial. Devuelve `CapacityExceeded` si no cabe la memoria necesaria.
- [ ] Integra `Keep(reason)` en publicación. Conserva el bloque anterior y muestra el aviso sin confirmar el commit o comando nuevo.
- [ ] Prueba leer un snapshot v2 con la revisión exacta de M0. Demuestra que desactivar las funciones nuevas no lo convierte a legacy con pérdida.
- [ ] Mide el tamaño del estado enriquecido en casos representativos. Si no cabe, conserva la escritura nueva desactivada y documenta la decisión de almacenamiento pendiente.
- [ ] Activa v2 en un PR de prueba sólo después de terminar ejecuciones que usen escritores anteriores a M0.

**Verificación focalizada:** `python3 -m unittest discover -s tests -p 'test_review_domain.py' -v`, más los casos de publicación modificados.
**Cierre:** estado persistente intacto en desborde y retorno probado a M0. No se cierra mediante un test que sólo compruebe la longitud del JSON.

## F0. Incorporar identidad y evidencia

**Archivos:** modifica `review_domain.py`, `review.py`, `prompt.md`, `tests/test_review_domain.py` y `README.md`.
**Interfaces:** `match_finding(previous: list[Finding], observation: Observation, facts: RepositoryFacts) -> MatchResult`; `accept_report(current: Snapshot, plan: ReviewPlan, report: ValidatedReport) -> Transition`.
**Tipos adicionales:** `Observation`, `MatchResult`, `RepositoryFacts`, `ReviewPlan`, `ReviewPolicy` y `ValidatedReport`. Usa los campos de `Plan` y `Report` del diseño; valida las citas antes de construir `ValidatedReport`. `MatchResult` distingue `Existing(id)`, `New` y `Ambiguous(ids)`.

`ReviewPolicy` reúne filtros, presupuestos, digest de reglas y los controles de activación definidos arriba. `RepositoryFacts` contiene revisión, delta real, correspondencias de renombres y referencias de blobs verificadas. El adaptador obtiene esos hechos con Git; el dominio no ejecuta comandos.

- [ ] Mantén la asignación de IDs en el programa. Trata el título y la causa propuesta como información modificable.
- [ ] Añade casos de cambio de título, desplazamiento de líneas, renombre confirmado y dos bugs con el mismo título.
- [ ] Devuelve coincidencia ambigua sin fusionar cuando hay dos candidatos plausibles. Conserva los descartes legacy.
- [ ] Valida ruta, blob, rango y digest del extracto contra el SHA indicado. Etiqueta aparte afirmaciones sin evidencia comprobable.
- [ ] Exige cambio pertinente para resolver. Conserva la resolución por reversión exacta y el arreglo en archivos relacionados.
- [ ] Prueba una cita existente con una interpretación falsa. La ubicación puede validarse, pero no se debe etiquetar el bug como reproducido.
- [ ] Ejecuta los tests de dominio y las clases `FindingIds`, `ResolvedLock`, `PublishFindings` y `ReviewContinuity`.

**Prueba real:** usa un PR de prueba con dos bugs de igual título, mueve uno y descarta el otro. Verifica los IDs y descartes publicados después del segundo push.
**Cierre:** no hay fusiones ambiguas ni falsos resueltos en los casos de control. Desactivar el reconocimiento nuevo conserva los IDs ya persistidos.

## D0. Probar el delta real

**Archivos:** crea o amplía `review_context.py` y `tests/test_review_context.py`; modifica `review.py`, `prompt.md` y `README.md`.
**Interfaz:** `plan_review(previous: LoadResult, facts: RepositoryFacts, policy: ReviewPolicy) -> ReviewPlan`.
**Tipo:** `ReviewPolicy` identifica filtros, presupuesto, digest de reglas y opciones independientes de delta y contexto.

- [ ] Añade casos de segundo push, eliminación, reversión, arreglo en consumidor, rebase, cambio de reglas y cobertura previa parcial.
- [ ] Usa `previous_sha..head` como tarea principal sólo cuando la memoria anterior es válida y completa. Mantén el diff global como contexto separado.
- [ ] Conserva `changed_paths` como delta real, independiente del alcance completo de recuperación.
- [ ] Registra obligaciones omitidas por presupuesto. No las conviertas en exclusiones deliberadas.
- [ ] Ejecuta `python3 -m unittest discover -s tests -p 'test_review_context.py' -v` y las clases `IncrementalPrepare` y `ReviewContinuity`.
- [ ] Compara el modo actual y el candidato sobre los pares congelados. Alterna el orden de las corridas y registra configuración e intentos.

**Activación:** cero falsos resueltos de control, ninguna pérdida nueva de High/Critical conocidos, precisión no inferior al control y reducción de al menos 20% en la mediana del segundo push. Son criterios de la muestra, no garantías generales. Si no se cumplen, deja el delta nuevo desactivado.

## C0. Probar contexto selectivo

**Archivos:** crea o amplía `review_context.py` y `tests/test_review_context.py`; modifica `review.py` y `prompt.md`.
**Interfaz:** `select_context(plan: ReviewPlan, facts: RepositoryFacts, policy: ReviewPolicy) -> ContextSelection`.
**Salida:** referencias seleccionadas, tipo de relación, método de descubrimiento y omisiones por presupuesto.

- [ ] Añade fixtures con un consumidor directo, una prueba relacionada, nombres homónimos y una llamada dinámica no encontrada por grep.
- [ ] Prioriza imports, consumidores y pruebas. Distingue búsqueda textual de relación sintáctica comprobada.
- [ ] Usa un presupuesto explícito de contexto. Conserva Read/Grep/Glob para que el modelo amplíe su investigación.
- [ ] Ejecuta el archivo focalizado de contexto. Afirma referencias concretas y omisiones, no sólo cantidad de archivos.
- [ ] Compara actual y candidato con E0 sobre la muestra reservada. Mantén fijo el modo de delta para aislar este cambio.

**Activación:** no empeoran precisión ni detección de defectos High/Critical conocidos y mejora al menos un caso confirmado entre archivos. Registra tiempo y costo; no atribuyas ahorro a la mera presencia de archivos precalculados. Sin mejora observada, conserva el modo actual.

## C1. Añadir reglas por carpeta

**Archivos:** modifica `review_context.py`, `review.py`, `tests/test_review_context.py` y `README.md`.
**Interfaz:** `load_path_rules(trusted_base_sha: str, paths: list[str]) -> RuleSelection`.

- [ ] Define `.github/ai-review-rules.json` como archivo opcional confiable en la base. Usa `version: 1` y una lista `rules` con `glob` e `instructions`.
- [ ] Conserva `.github/ai-review.md` como regla general. Aplica después las reglas coincidentes en orden declarado; las posteriores prevalecen ante conflicto.
- [ ] Limita el archivo nuevo a 16000 bytes UTF-8. Ante formato inválido o exceso, informa el problema y conserva sólo las reglas generales válidas.
- [ ] Prueba dos patrones superpuestos, ninguna coincidencia, archivo inválido y un PR que intenta cambiar sus propias instrucciones.
- [ ] Ejecuta el archivo focalizado de contexto y verifica que sólo se leen reglas del SHA confiable.

**Prueba real:** abre un PR de prueba que modifique el archivo de reglas y código afectado. La revisión debe usar las reglas de base y tratar las nuevas como contenido del diff.
**Cierre:** documenta precedencia, límites y ejemplo de configuración. La ausencia del archivo conserva la conducta anterior.

## C2. Adjuntar evidencia de CI

**Archivos:** modifica `review.py`, `review_context.py`, `tests/test_review_context.py`, `action.yml`, plantillas, workflows y `README.md`.
**Interfaz:** `collect_check_evidence(repo: str, head_sha: str) -> CheckEvidenceResult`.

- [ ] Consulta checks del SHA exacto. Conserva productor, conclusión y URL; no aceptes un check de otro commit.
- [ ] Declara `checks: read` sólo para esta integración. No añadas descarga de logs ni permiso de Actions en esta primera entrega.
- [ ] Prueba paginación, check pendiente, permiso denegado y resultado perteneciente a otro SHA.
- [ ] Usa `Unavailable(reason)` cuando no se puede consultar. No interpretes indisponibilidad como check exitoso.
- [ ] Ejecuta los tests focalizados modificados y actionlint mediante CI.

**Prueba real:** revisa un PR con un check fallido conocido y otro pendiente. El comentario debe enlazarlos y distinguir sus estados sin afirmar que explican por sí solos un bug.
**Cierre:** evidencia del mismo SHA y funcionamiento degradado sin permisos. Desactivar la integración elimina sólo ese contexto.

## U0. Serializar la publicación

**Archivos:** modifica `review.py`, `review_domain.py`, `action.yml`, workflows y plantillas; crea `tests/test_review_commands.py`.
**Interfaces:** `reconcile_publication(current: Snapshot, candidate: ReviewCandidate, facts: RepositoryFacts) -> Transition`; `publish_candidate(candidate_path: str) -> PublishOutcome`.
**Salida:** `Published`, `Superseded`, `Keep` o `Retryable`. El candidato identifica run, intento, head, base, política y generación de origen.

- [ ] Separa el cálculo del modelo de la publicación breve. Cada run escribe su propio candidato.
- [ ] Usa el mismo grupo de publicación por repo y PR para todos los escritores. No canceles al escritor activo.
- [ ] Relee estado y referencias antes de escribir. Fusiona descartes recientes; descarta para cobertura un análisis cuya base ya no es válida.
- [ ] Prueba dos publicadores, un descarte entre lectura y escritura, un push durante el PATCH y un candidato inaccesible.
- [ ] No trates `concurrency` como cola FIFO ni como transacción entre HEAD y comentario. Mantén el SHA visible y reconsulta después de publicar para detectar desfases.
- [ ] Ejecuta `python3 -m unittest discover -s tests -p 'test_review_commands.py' -v`.

**Prueba real:** fuerza una revisión lenta del commit A, publica B y termina A. A no debe reemplazar una revisión ya publicada de B. Documenta por separado la exposición temporal posible si el push ocurre durante un PATCH.
**Cierre:** publicación idempotente y decisiones confirmadas intactas. Si falta el candidato, registra fallo recuperable sin avanzar cobertura.

## U1. Atender comentarios del PR

**Archivos:** crea los workflows de comandos indicados en el mapa; modifica `review.py`, `review_domain.py`, `tests/test_review_commands.py`, `scripts/install.sh` y `README.md`.
**Interfaces:** `parse_command(body: str) -> Command | None`; `apply_command(current: Snapshot, command: AuthorizedCommand) -> Transition`.
**Comandos:** conserva `descartar F3` y `descartar todo`; añade `explicar F3` y `revisar`, todos con el prefijo `ai-review:`.

- [ ] Acepta sólo eventos de creación de comentarios en PRs abiertos del mismo repo. Rechaza forks y drafts.
- [ ] Ejecuta el workflow desde la rama por defecto. Obtén el código de revisión de una revisión confiable; no ejecutes scripts del HEAD con secretos.
- [ ] Verifica permiso write, maintain o admin para cada comando. Un fallo de consulta detiene el prefijo consumido; no saltes ese comentario al avanzar el cursor.
- [ ] Persiste cursor y efecto juntos. Para trabajo largo, persiste primero la solicitud pendiente con ID de comentario y SHA.
- [ ] Implementa rechazo visible de IDs desconocidos. Una edición posterior no reescribe comandos ya atendidos.
- [ ] Aplica descartes sin esperar al modelo. Para `descartar todo`, captura los IDs abiertos al procesar y enuméralos en la respuesta.
- [ ] Explica primero con evidencia guardada. Si se consulta el modelo, conserva el estado del hallazgo y etiqueta la revisión usada.
- [ ] Agrupa solicitudes compatibles de revisión. No las marques completadas sólo por despacharlas.
- [ ] Prueba duplicados, revocación de permisos, desborde, crash antes y después del PATCH, explicación fallida y reemplazo de jobs pendientes.
- [ ] Ejecuta los tests de comandos y las clases `DismissCommands`, `GatePrev` y `PublishFindings` que se modifiquen.

**Prueba real:** envía los tres comandos en un PR de prueba sin hacer otro push. Repite un evento. Comprueba una sola transición, respuesta visible, SHA correcto y descarte persistente después de una revisión concurrente.
**Cierre:** una solicitud sin ejecución puede recuperarse mediante otra ejecución o repetición del comando. No prometas entrega garantizada. Desactivar el disparador conserva los descartes ya persistidos.

## X0. Decidir si hace falta continuación por archivos

Este bloque produce una decisión y, si corresponde, un subplan. No autoriza escribir la cola de pendientes.

- [ ] Examina veinte recuperaciones completas recientes. Cuenta cuántas vuelven a agotar presupuesto.
- [ ] Si son menos de tres, registra “no se construye por ahora” y conserva la recuperación completa.
- [ ] Si son tres o más, comprueba que los pendientes caben sin pérdida en el estado.
- [ ] Especifica pendientes ligados a head, base y política, invalidación ante cambios y un máximo de dos continuaciones.
- [ ] Define pruebas de interrupción, repetición, cambio de HEAD y cobertura parcial. Presenta el subplan antes de implementar.

## X1. Decidir si hacen falta comentarios inline

Este bloque también produce una decisión y un subplan condicionado.

- [ ] Reúne al menos cinco casos evaluados donde el comentario fijo dificulte localizar la corrección.
- [ ] Si no existen esos casos, conserva el comentario único.
- [ ] Presenta la modificación del contrato visible para aceptación del usuario.
- [ ] Si se acepta, diseña identidad del comentario, línea válida del diff, SHA, deduplicación y actualización tras un push.
- [ ] Mantén en el resumen los hallazgos sin una línea válida. Define pruebas de rename, línea eliminada y evento repetido antes de implementar.

## Resolver los bloqueos sin ampliar el alcance

| Situación | Acción |
|---|---|
| No hay autorización de implementación | Entrega el plan y detén la ejecución. |
| Falta un par comparable con CodeRabbit | Deja el caso pendiente y no actives optimizaciones que dependan de E1. |
| La prueba focalizada no falla antes del arreglo | Corrige el caso antes de afirmar que demuestra la regresión. |
| El estado v2 no cabe | Conserva el snapshot anterior y detén su activación. Decide capacidad antes de seguir. |
| La versión de retorno pierde información | No actives el escritor nuevo. Corrige primero M0. |
| Cambia el SHA después de validar | Revisa la evidencia afectada. No atribuyas checks del SHA anterior al nuevo. |
| Una ronda detecta un bloqueo reproducible | Corrígelo antes de mergear. Aplica el límite de convergencia de AGENTS.md. |
| Aparece una observación tardía no bloqueante | Regístrala como tarea y nómbrala en el PR. No reabras la batería o revisión completa. |
| La comparación no demuestra mejora | Conserva desactivada esa opción y publica los resultados. |

## Revisar los riesgos antes de cerrar un bloque

Cinco casos deben tener evidencia explícita en los bloques propietarios:

1. Estado corrupto o demasiado grande no se convierte en memoria vacía. M0 y M1.
2. Un resultado viejo no revierte un descarte ni se presenta sin SHA. U0 y U1.
3. Un bug distinto con título parecido no desaparece por deduplicación. F0.
4. Un arreglo o reversión fuera del archivo principal conserva el ciclo correcto. F0 y D0.
5. Un comando no autorizado o repetido no modifica estado ni consume trabajo dos veces. U1.

Los datos aún no obtenidos son capacidad real de v2, corpus pareado completo y latencia de comandos. Se miden en M1, E1 y U1 respectivamente. No se realizaron prototipos ni mediciones nuevas al escribir este plan.

## Cerrar la ejecución futura

- [ ] Cada bloque autorizado tiene evidencia del SHA final, CI, revisión y comportamiento observable.
- [ ] Las opciones experimentales tienen una decisión documentada de activación o permanencia desactivada.
- [ ] Los consumidores reciben sólo revisiones que hayan pasado el piloto central.
- [ ] `Plans.md` refleja lo realmente terminado. No marques X0 o X1 como implementados por haber tomado una decisión.
- [ ] El informe final distingue código integrado, funciones activadas, experimentos rechazados y trabajo condicionado.

Este documento termina en la planificación. El siguiente paso requiere una instrucción explícita para ejecutar los bloques elegidos.
