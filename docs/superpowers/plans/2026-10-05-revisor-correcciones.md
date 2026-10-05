# Plan de implementación para corregir y completar el revisor de PRs

> **Para quien ejecute el plan:** después de recibir un encargo de implementación, usa `superpowers:executing-plans` para avanzar por tareas. Si el usuario elige delegación, usa `superpowers:subagent-driven-development`. Las casillas registran trabajo futuro.

**Objetivo:** corregir la preparación del contexto, conservar la identidad y memoria de los hallazgos, y publicar resultados vigentes mediante un coordinador por PR.

**Arquitectura:** conserva el estado completo en el comentario único y concentra sus escrituras en un coordinador confiable. Los workers leen solicitudes persistidas y devuelven resultados aislados. Activa el diff incremental después de medirlo contra una revisión completa.

**Tecnologías:** Python, `unittest`, Git, GitHub CLI, GitHub Actions, Bash, Ruff y pre-commit. Reutiliza el runtime del modelo y su fallback.

**Diseño:** [Arquitectura para corregir y completar el revisor de PRs](../../reviewer-corrections-architecture.md). Lee sus contratos junto con este plan.

**Base de trabajo:** `179e6a3719b8ff611c820e1575f15d40e36e1ddb` o un descendiente que conserve sus capacidades. Las rutas existentes se refieren a ese commit.

**Estado:** pendiente. Fecha: 2026-10-05. Este documento entrega el plan solicitado. La implementación, la instalación en consumidores y las mediciones pagadas requieren un encargo posterior.

## Conserva las restricciones del proyecto

- Preserva DeepSeek V4.1 Flash, el fallback, el comentario único y los filtros para forks y drafts.
- Obtén instrucciones y configuración desde la base confiable. Limita el modelo a Read, Grep y Glob.
- Ante un fallo del proveedor, conserva la revisión confirmada y muestra un aviso sin acreditar cobertura completa.
- Reutiliza Q0, P0, M0, M1 y F0. Completa sus callers y contratos donde las tareas lo indiquen.
- Conserva IDs, descartes, solicitudes pendientes y recibos necesarios para impedir efectos repetidos.
- Usa artifacts sólo para transportar resultados. Propón siete días de retención.
- Cuenta presupuestos en bytes UTF-8 de la representación final. Declara cualquier obligación omitida.
- Mantén un solo escritor por repositorio y PR. Usa `cancel-in-progress: false` y excluye escritores antiguos antes de activar el nuevo.
- Conserva el modo actual para consumidores sin migrar. Fija el piloto a un SHA candidato: los consumidores actuales que usan `@main` pueden recibir cambios al hacer merge.
- Mantén las opciones nuevas desactivadas hasta superar sus condiciones de activación. Un bloque implementado no equivale a un bloque activado.

## Comprueba cinco fallos antes de cerrar

| Entrada o condición | Resultado que debe observar el usuario | Tarea que fija la prueba |
|---|---|---|
| Ruta con Unicode, tab, salto de línea o `-->` | La revisión usa el archivo correcto y conserva su identidad al persistir. | T01 y T04 |
| Búsqueda truncada que todavía no encontró tests | El contexto informa una búsqueda incompleta. | T02 |
| Descarte mientras termina un worker anterior | El descarte sobrevive y el resultado conserva su SHA real. | T06, T07 y T08 |
| Respuesta de PATCH perdida o capacidad agotada | No se repite el efecto ni se confirma un comando que no quedó guardado. | T05 y T08 |
| Reintento fallido o revisión sin pareja en la evaluación | El informe conserva el fallo, el costo y la causa de exclusión. | T12 y T13 |

## Ordena los bloques

Sigue estas dependencias. R0, S0 y E2 pueden desarrollarse como bloques separados. Integra secuencialmente las tareas que modifican el mismo archivo.

| Tarea | Bloque | Depende de | Entregable |
|---|---|---|---|
| T01 | R0 | Base verificada | Rutas Git exactas |
| T02 | R0 | T01 | Búsqueda de contexto con resultado explícito |
| T03 | R0 | T01 | Presupuestos medidos sobre bytes escritos |
| T04 | S0 | Base verificada | Codec schema 3 y escritor compatible operativo |
| T05 | S1 | T04 | Capacidad reservada y desborde sin pérdida |
| T06 | F1 | T01–T04 | Identidad F0 conectada al recorrido completo |
| T07 | U0 | T04 y T05 | Solicitudes y transiciones puras |
| T08 | U0 | T06 y T07 | Publicación autenticada y recuperación |
| T09 | U1 | T05, T06 y T08 | Comandos procesados sin push |
| T10 | U0/U1 | T08 y T09 | Coordinador y worker separados |
| T11 | Instalación | T10 | Instalador atómico y ensayo de retorno |
| T12 | E2 | Base verificada | Comparación por producto y pares válidos |
| T13 | E2 | T12 | Adjudicación versionada y muestra reservada |
| T14 | D0 | T03, T06 y T08 | Delta real y fallback a revisión completa |
| T15 | C0 | T02 y T14 | Contexto seleccionado con procedencia |
| T16 | Activación | T05, T11 y T13–T15 | Piloto, medición y decisión registrada |

R0 puede entregarse antes del piloto. Activa F1 y los comandos después de validar capacidad y retirar los escritores antiguos. El código D0 y C0 puede probarse offline antes de completar E2; su activación depende de E2 y del baseline suficiente.

## Prepara la ejecución futura

1. Conserva los cambios del checkout del usuario. El checkout usado para escribir este plan estaba 35 commits detrás de la base.
2. Cuando comience la implementación, prepara un worktree aislado mediante `superpowers:using-git-worktrees`.
3. Comprueba la ascendencia con `git merge-base --is-ancestor 179e6a3719b8ff611c820e1575f15d40e36e1ddb HEAD`. Espera código de salida `0`.
4. Revisa cambios posteriores que afecten las funciones nombradas. Ajusta referencias antes de editar si la base ya contiene una corrección.
5. Conserva los documentos pendientes del usuario. Actualiza `Plans.md` únicamente durante la ejecución autorizada, con estados y evidencia reales.

Los comandos de pruebas siguientes se ejecutan desde la raíz del worktree. Las clases y archivos marcados como nuevos se crean en su tarea. Los tests usan repositorios temporales y los dobles existentes `FAKE_GH`, `FAKE_GH_WRITER` y `FAKE_CLAUDE` cuando corresponda.

## Ejecuta cada tarea con una prueba de aceptación

### T01. Conserva las rutas Git como bytes

**Archivos:** modifica `review.py` en `grep_files`, `cmd_prepare`, `changed_since` y sus lectores de rutas. Amplía `tests/test_review.py` con la clase `GitPaths`.

**Interfaces:** define `GitPath(raw: bytes)` en `review.py`. Haz que los lectores Git entreguen registros NUL y que los callers conserven `raw` hasta invocar Git. T14 moverá este tipo al módulo de contexto.

- [ ] Agrega `GitPaths.test_special_paths_roundtrip`: crea `src/niño.py`, `tab\tname.py`, `line\nname.py` y `x-->y.py` como rutas reales. Comprueba que el conjunto de bytes leído coincide exactamente con el creado.
- [ ] Agrega `GitPaths.test_unrepresentable_path_is_omitted`: comprueba que una ruta binaria no representable produce una omisión identificable y nunca un nombre con caracteres de reemplazo. Agrega también un blob obligatorio ilegible y comprueba que su omisión impide cobertura completa.
- [ ] Ejecuta `PYTHONPATH=tests python3 -m unittest test_review.GitPaths -v`. Confirma una falla por el comportamiento anterior.
- [ ] Sustituye separación por líneas y decodificación con reemplazos por lectura binaria NUL. Usa argumentos separados y `--` en las consultas que admiten rutas. Separa la presentación Markdown de la identidad usada por Git.
- [ ] Repite el comando. Espera todos los casos aprobados y acceso al contenido del archivo exacto.

**Cierre:** la ruta presentada nunca vuelve a utilizarse como identificador para leer Git.

### T02. Filtra tests antes de limitar la búsqueda

**Archivos:** modifica `review.py` en `grep_files`, `build_tests` y `build_callers`. Amplía `tests/test_review.py` con `ContextSearch`.

**Interfaces:** cambia `grep_files(patterns, limit)` a `SearchResult`, con variantes `Complete(paths)`, `Truncated(paths, reason)` y `Failed(reason)`. Permite seleccionar el filtro de candidatos antes del límite. Migra ambos callers en esta tarea.

- [ ] Agrega `ContextSearch.test_tests_after_205_source_matches`: crea 205 coincidencias en fuentes y una en `tests/test_app.py`. Comprueba que el contexto incluye esa prueba.
- [ ] Agrega casos para búsqueda completa vacía, error Git, techo de salida y timeout. Comprueba estados distintos y motivo visible, incluso si el resultado truncado tiene cero rutas.
- [ ] Ejecuta `PYTHONPATH=tests python3 -m unittest test_review.ContextSearch test_review.TestFileDetection -v`. Confirma que la nueva regresión falla.
- [ ] Consume la salida en flujo. Define techos iniciales de `8_000_000` bytes examinados y `15` segundos por búsqueda. Al alcanzar un techo, termina y recoge el proceso antes de devolver `Truncated`.
- [ ] Repite el comando con límites controlados por el test, sin esperas reales de 15 segundos. Espera todos los casos aprobados.

**Cierre:** una búsqueda incompleta nunca produce el mensaje de ausencia de tests. La omisión de una pista opcional queda visible sin convertir por sí sola la revisión en parcial.

### T03. Cuenta el diff y el contexto serializados

**Archivos:** modifica `review.py` en `cmd_prepare`, `build_callers`, `build_tests` y `build_conventions`. Amplía `tests/test_review.py` con `ContextBudgets` y los casos pertinentes de `PrepareContext`.

**Interfaces:** conserva el campo `diff_bytes` del manifiesto como longitud real del archivo. Agrega `over_budget_bytes` para la excepción compatible del primer archivo grande.

- [ ] Agrega `ContextBudgets.test_utf8_manifest_matches_file`: usa dos archivos con texto multibyte y un límite de `1000`. Comprueba `manifest["diff_bytes"] == len(diff_path.read_bytes())` y que la selección usa bytes.
- [ ] Agrega `ContextBudgets.test_first_large_file_reports_excess`: comprueba `over_budget_bytes == max(0, actual_bytes - budget)` y conserva la admisión del primer archivo grande en modo compatible.
- [ ] Agrega casos con encabezados y avisos de truncamiento. Comprueba que cada paquete respeta su presupuesto final y decodifica como UTF-8 válido.
- [ ] Ejecuta `PYTHONPATH=tests python3 -m unittest test_review.ContextBudgets test_review.PrepareContext -v`. Confirma las fallas anteriores.
- [ ] Calcula los límites sobre la serialización final. Reserva espacio para el aviso antes de recortar y conserva límites de caracteres válidos.
- [ ] Repite el comando. Espera todos los casos aprobados.

**Cierre:** ningún manifiesto declara menos bytes que los escritos. El presupuesto estricto de D0 queda para T14.

### T04. Entrega el codec y el escritor de compatibilidad

**Archivos:** modifica `review_domain.py`, `review.py` en `build_findings` y `cmd_publish`, y `tests/test_review_domain.py`. Amplía `PersistenciaSinPerdida` en `tests/test_review.py`.

**Interfaces:** extiende `Snapshot`, `ReviewPolicy`, `ReviewPlan` y `ValidatedReport` existentes. Define los tipos `ReviewTarget`, `Coverage`, `WorkRequest`, `RunKey`, `StorageBudget` y `EncodedCheckpoint` del diseño. Usa `encode_snapshot(current: Snapshot, budget: StorageBudget) -> EncodedCheckpoint | CapacityExceeded` y conserva los resultados discriminados de `read_snapshot`.

- [ ] Agrega `Schema3Compatibility.test_preserves_state_and_requests` para legacy, v2 y schema 3. Comprueba IDs, `next_id`, descartes, `seen`, cursor, contador de solicitudes y recibos después de escribir y leer.
- [ ] Agrega `Schema3Compatibility.test_invalid_or_future_never_initializes_empty`. Sólo `Missing` puede iniciar memoria vacía. La cobertura antigua sin prueba suficiente debe resultar `Unknown`.
- [ ] Agrega `Schema3Compatibility.test_identity_strings_roundtrip` con Unicode, tabs, saltos de línea y `-->`. Comprueba igualdad semántica, incluidos campos de identidad.
- [ ] Agrega una prueba de publicación con dos actualizaciones sucesivas sobre v2 y schema 3 usando identidad `current`. Comprueba que cambia la revisión y sobrevive un descarte.
- [ ] Ejecuta `PYTHONPATH=tests python3 -m unittest test_review_domain.Schema3Compatibility test_review.PersistenciaSinPerdida -v`. Confirma las fallas.
- [ ] Implementa el codec y el escritor compatible. Usa escapes JSON reversibles como `\u003e`. Mantén desactivada la emisión de schema 3 hasta distribuir este lector y escritor.
- [ ] Migra las solicitudes v2 con IDs de texto mediante una correspondencia persistida en su origen. Asigna IDs numéricos monotónicos sin perder el identificador anterior ni compactar pendientes.
- [ ] Normaliza la política confiable una vez, incluidos modos, versiones, reglas, exclusiones y presupuestos. Usa identidad externa `current` o `anchors`; adapta el valor interno antiguo `titles` en la frontera de compatibilidad.
- [ ] Repite el comando. Espera publicación efectiva, además de lectura correcta.

**Cierre:** el escritor de retorno puede actualizar schema 3 en modo `current`. Conserva el binario y la configuración que cumplan esa prueba para T11.

### T05. Reserva capacidad antes de confirmar efectos

**Archivos:** modifica `review_domain.py` en `encode_snapshot`, `review.py` en la composición del comentario y `tests/test_review_domain.py`. Amplía `DesbordeSinMemoriaPrevia` en `tests/test_review.py`.

**Interfaces:** completa `StorageBudget` con presupuesto de estado y límites de bytes y caracteres del comentario. `CapacityExceeded` devuelve la causa sin entregar un checkpoint parcialmente válido.

- [ ] Agrega `CheckpointCapacity.test_representative_state_roundtrip` con hallazgos enriquecidos, evidencia, solicitudes pendientes y recibos. Mide el JSON final y el comentario completo.
- [ ] Agrega `CheckpointCapacity.test_overflow_preserves_confirmed_state`. Comprueba igualdad del SHA confirmado, cursor, descartes y pendientes antes y después del desborde.
- [ ] Ejecuta `PYTHONPATH=tests python3 -m unittest test_review_domain.CheckpointCapacity test_review.DesbordeSinMemoriaPrevia -v`. Confirma las fallas.
- [ ] Conserva el perfil de `8000` bytes. Agrega un perfil propuesto de `40000` bytes de estado y un máximo de `60000` bytes y `60000` caracteres para el comentario completo.
- [ ] Asigna a la prosa visible el espacio restante. Reduce explicaciones visibles antes de afectar memoria necesaria. Conserva las solicitudes aceptadas y los recibos que impiden repetir efectos.
- [ ] Repite el comando con fronteras exactas y un byte adicional. Espera aceptación dentro del límite y conservación del checkpoint previo fuera de él.

**Cierre:** registra la reserva medida para coordinación. Mantén pendiente la activación del perfil ampliado hasta la prueba de comentario real de T16.

### T06. Conecta F0 con la política y los hechos reales

**Archivos:** modifica `review.py` en `politica_de_identidad`, `hechos_de_repo`, `build_findings` y `cmd_publish`. Modifica `review_domain.py` en `observation_de_entrada`, `validar_reporte` y `accept_report`. Amplía `CableadoIdentidad` y agrega `ReportBoundary` en los tests respectivos.

**Interfaces:** lleva la misma `ReviewPolicy` normalizada por preparación, ejecución y aceptación. Usa `accept_report(current, plan, report)` para ambas estrategias. Amplía `RepositoryFacts` con el delta real y los blobs anteriores necesarios.

- [ ] Agrega un recorrido completo que descarte F1, cambie su título y mueva su ancla. Comprueba que `anchors` conserva F1 descartado y que dos defectos distintos no se fusionan.
- [ ] Agrega `ReportBoundary.test_malformed_ranges_are_typed_errors` con `[3]`, `[3, "x"]` y booleanos como líneas. Comprueba rechazo sin excepción y sin avance de cobertura.
- [ ] Agrega casos de reversión, cambio relacionado y omisión del hallazgo en la respuesta. Comprueba que sólo evidencia pertinente permite resolverlo.
- [ ] Agrega un caso con `reviewed` distinto de `changed_since`. Comprueba que los hechos usan el cambio real y que la cobertura visible coincide con la persistida.
- [ ] Ejecuta `PYTHONPATH=tests python3 -m unittest test_review.CableadoIdentidad test_review_domain.ReportBoundary -v`. Confirma las fallas del recorrido anterior.
- [ ] Conecta la aceptación del dominio y elimina el retorno incondicional `Keep` para v2 en ese recorrido. Migra los callers que terminaban en `merge_findings`.
- [ ] Deriva cobertura una sola vez: exige reporte válido y vigente, runtime terminado y todas las obligaciones requeridas entregadas y declaradas cubiertas. Conserva `Partial` o `Unknown` cuando falte una condición.
- [ ] Repite el comando. Espera IDs estables, descartes conservados y cobertura coherente.

**Cierre:** la opción de identidad cambia la ruta realmente ejecutada. Mantén `anchors` desactivado para consumidores hasta T16.

### T07. Persiste solicitudes antes de despachar

**Archivos:** modifica `review_domain.py`. Crea `tests/test_review_coordinator.py` con `RequestTransitions`.

**Interfaces:** implementa `reconcile(current: Snapshot, event: DomainEvent, facts: RepositoryFacts, policy: ReviewPolicy) -> Decision`. Reutiliza `Keep`; agrega `Commit(snapshot, work_after_commit)`. Define `Origin`, los eventos y los recibos del diseño en este módulo.

- [ ] Agrega `RequestTransitions.test_persists_request_before_work`: comprueba que el trabajo propuesto sólo aparece junto con su solicitud persistible y su generación.
- [ ] Agrega casos de evento automático repetido, comando explícito del mismo SHA y compactación. Comprueba agrupación automática, nueva solicitud explícita e IDs nunca reutilizados.
- [ ] Agrega `RequestTransitions.test_only_pending_request_accepts_result`: repite una clave `(request_id, run_id, attempt)` y envía un ID ausente. Comprueba que ninguno crea nuevos hallazgos después de finalizar la solicitud.
- [ ] Agrega un descarte entre preparación y resultado. Comprueba aceptación sobre el snapshot actual y conservación del descarte.
- [ ] Agrega cambios de head, base, política y obligaciones requeridas durante el trabajo. Comprueba que el resultado antiguo no acredita cobertura y que queda admitido trabajo para el target vigente.
- [ ] Ejecuta `python3 -m unittest discover -s tests -p 'test_review_coordinator.py' -v`. Confirma las fallas.
- [ ] Implementa las transiciones con los estados `Pending`, `Running`, `Finished` y `FailedRetryable`. Representa el rechazo terminal mediante `Finished(receipt)` con su motivo.
- [ ] Compacta solicitudes terminadas sólo después de persistir resultado y recibo. Mantén pendientes y contador monotónico.
- [ ] Repite el comando. Espera transiciones deterministas sin Git, red ni llamadas al modelo.

**Cierre:** todos los efectos externos quedan después de una decisión persistible. El dominio nunca despacha trabajo directamente.

### T08. Autentica resultados y recupera escrituras inciertas

**Archivos:** modifica `review.py` para agregar el comando `reconcile` y sus adaptadores. Amplía `tests/test_review_coordinator.py` con `CheckpointRecovery` y `ResultAuthentication`.

**Interfaces:** define `publish_checkpoint(decision, observed_comment) -> PublishReceipt | Unconfirmed` y `dispatch_confirmed(receipt)`. `PublishReceipt` contiene generación, recibo persistido y trabajo confirmado. Define `authenticate_result(run_metadata, artifact, request) -> AuthenticatedResult | Rejected` en el adaptador.

- [ ] Agrega `CheckpointRecovery.test_patch_response_lost`: simula PATCH aplicado con respuesta perdida. Comprueba que la relectura encuentra el recibo y no reaplica el resultado.
- [ ] Agrega casos de caída antes del dispatch, dispatch incierto, artifact vencido y evento reemplazado. Comprueba recuperación desde pendientes al siguiente evento o despacho manual.
- [ ] Agrega casos de POST incierto y varios comentarios con el marcador. Comprueba búsqueda antes de crear y detención de escritura ante duplicados.
- [ ] Agrega `ResultAuthentication.test_workflow_sha_is_not_pr_sha`: valida por separado el SHA confiable del workflow y el HEAD del PR. Rechaza repo, workflow, ref, attempt, request o digest incorrectos.
- [ ] Agrega un push durante PATCH y un fallo de worker antiguo después de una revisión nueva. Comprueba SHA visible correcto, nueva solicitud al releer y ausencia de aviso obsoleto.
- [ ] Ejecuta `python3 -m unittest discover -s tests -p 'test_review_coordinator.py' -v`. Confirma las fallas.
- [ ] Implementa lectura paginada, autenticación por API y validación del paquete como datos. Relee head, base, política y checkpoint antes de aceptar; vuelve a consultar después de publicar.
- [ ] Despacha sólo después de confirmar la solicitud. Si falta confirmación, redescubre checkpoint y runs asociados antes de otro intento.
- [ ] Repite el comando. Espera un solo efecto aceptado por solicitud y ninguna ejecución del contenido del artifact.

**Cierre:** la recuperación admite repetir un intento del modelo, pero no repetir su efecto. Documenta recuperación manual cuando no llega otro evento; no prometas un cron.

### T09. Procesa comandos sin esperar otro push

**Archivos:** modifica `review.py` en `parse_dismiss_command`, `collaborator_permission` y `collect_dismissals`, con adaptación al coordinador. Amplía `review_domain.py` y crea `tests/test_review_commands.py`.

**Interfaces:** conserva el prefijo `ai-review:`. Construye `AuthorizedCommand` sólo tras verificar permiso `write`, `maintain` o `admin`. `Explain` fija `finding_id`, `ReviewTarget` y digest del hallazgo. `Review` admite una solicitud explícita del mismo SHA.

- [ ] Agrega `CommandAdmission.test_permission_failure_holds_cursor`: coloca un fallo de permisos entre dos comentarios. Comprueba que el cursor no salta el comentario fallido ni procesa los posteriores.
- [ ] Agrega casos de permiso denegado, ID inexistente y edición previa a la admisión. Comprueba rechazo visible con recibo. Una edición posterior a la admisión debe conservar la decisión original.
- [ ] Agrega `CommandAdmission.test_dismiss_all_captures_current_ids`: descarta todos y luego acepta un hallazgo nuevo. Comprueba que el nuevo ID sigue abierto.
- [ ] Agrega `CommandExecution.test_reauthorize_before_model`: revoca el permiso de una solicitud pendiente. Comprueba cero llamadas al proveedor y rechazo terminal visible.
- [ ] Agrega explicación vigente y explicación antigua. Comprueba que ninguna cambia hallazgos y que la antigua identifica su target anterior.
- [ ] Ejecuta `python3 -m unittest discover -s tests -p 'test_review_commands.py' -v`. Confirma las fallas.
- [ ] Procesa comentarios por ID y persiste un prefijo confirmado. Aplica `descartar`, `explicar` y `revisar` mediante T07 y T08. Captura los IDs de `descartar todo` antes de incorporar resultados pendientes.
- [ ] Repite el comando. Espera confirmación sólo después de guardar efecto o solicitud, incluido cursor y recibo.

**Cierre:** una ejecución disparada por comentario produce el efecto sin push. Un fallo transitorio deja trabajo recuperable.

### T10. Separa los workflows del coordinador y del worker

**Archivos:** crea `templates/ai-review-publish.yml` y `templates/ai-review-worker.yml`. Modifica `review.py` para agregar `execute-request` y amplía `Workflows` en `tests/test_review.py`. Conserva la interfaz actual de `action.yml`.

**Interfaces:** `reconcile` recibe un PR identificado por el evento confiable. `execute-request` recibe repositorio, PR e ID de solicitud, y devuelve un paquete con target, plan, policy digest y `RunKey`. El coordinador reconstruye la confianza; esos campos no autentican el paquete por sí solos.

- [ ] Agrega `Workflows.test_coordinator_is_only_writer` y `Workflows.test_worker_cannot_publish`. Comprueba permisos explícitos y ausencia de claves del proveedor en el coordinador.
- [ ] Agrega casos de `pull_request_target`, `issue_comment.created`, `workflow_run.completed` y recuperación por `workflow_dispatch`. Comprueba que cada evento resuelve el mismo grupo por repositorio y PR.
- [ ] Agrega una prueba del entorno del modelo. Comprueba que contiene las credenciales necesarias del proveedor y carece de credenciales GitHub u otros secretos heredados.
- [ ] Ejecuta `PYTHONPATH=tests python3 -m unittest test_review.Workflows -v`. Confirma las fallas.
- [ ] Define el coordinador con `contents: read`, `pull-requests: write` y `actions: write` para consultar y despachar. Usa exclusión por repositorio y PR sin cancelación del escritor activo, incluso desde el workflow padre.
- [ ] Define el worker con lectura de contenido y sin escritura de comentarios. Usa código fijado a una revisión confiable y lee el PR como datos. Conserva Read, Grep y Glob como herramientas del modelo.
- [ ] Publica artifacts aislados por solicitud, run y attempt, con siete días de retención. Mantén fuera del grupo del escritor la ejecución del modelo.
- [ ] Repite las pruebas y valida las plantillas con actionlint, siguiendo la versión fijada en `.github/workflows/ci.yml`. Espera aprobación de permisos, eventos y sintaxis.

**Cierre:** los workflows nuevos permanecen como plantillas hasta la instalación autorizada. El guard actual de `action.yml` para `pull_request` no recibe eventos de comentarios.

### T11. Instala el conjunto completo y ensaya el retorno

**Archivos:** modifica `scripts/install.sh` y la documentación de instalación en `README.md`. Crea `tests/test_install.py` y `docs/reviewer-rollout.md`. Define la instalación futura de `.github/workflows/ai-review-publish.yml` y `.github/workflows/ai-review-worker.yml` en cada consumidor.

**Interfaces:** conserva la invocación por repositorios de `scripts/install.sh`. Agrega una selección explícita del SHA confiable y del modo coordinado. Genera un único commit con ambas plantillas y la retirada del escritor anterior de `.github/workflows/ai-review.yml`.

- [ ] Agrega `AtomicInstall.test_single_commit_replaces_writer_set` con una API GitHub simulada. Comprueba que ningún estado publicado contiene sólo la mitad del conjunto.
- [ ] Agrega `AtomicInstall.test_retry_preserves_unrelated_changes`: simula una rama ya existente con cambios ajenos. Comprueba reuso seguro o conflicto explícito, sin reset forzado.
- [ ] Agrega `CompatibleRollback.test_updates_expanded_schema3_in_current_mode`: crea memoria ampliada con un descarte y una solicitud pendiente. Comprueba otra actualización con el escritor de retorno.
- [ ] Ejecuta `python3 -m unittest discover -s tests -p 'test_install.py' -v`. Confirma las fallas.
- [ ] Implementa el commit atómico mediante la API Git de árboles y commits. Conserva cambios ajenos y evita sobrescribir una rama que cambió durante la instalación.
- [ ] Escribe el procedimiento de corte: detener admisión, drenar ejecuciones antiguas, comprobar checkpoint y activar el coordinador. Fija el piloto al SHA candidato.
- [ ] Escribe el retorno: detener y drenar al escritor activo, desactivar `anchors` y modos experimentales, conservar schema y presupuesto, y habilitar el escritor compatible probado.
- [ ] Repite el comando. Espera conjunto coherente, reintento seguro y memoria conservada al retornar.

**Cierre:** el instalador queda verificable con dobles de API. Instalar en repositorios reales pertenece a T16. No uses `179e6a3` como versión de retorno: conserva v2 sin continuar su actualización.

### T12. Compara productos y configuraciones por separado

**Archivos:** modifica `scripts/compare_reviews.py` y `tests/test_compare_reviews.py`. Crea `evaluation/reviewer/v2/README.md` para el formato nuevo.

**Interfaces:** extrae `compare_reviews(corpus, observations, judgments, pairing) -> dict` y conserva el CLI existente. Agrega `--pairing` para la comparación versionada. Usa `CaseKey`, `ObservationKey`, `FindingKey` y `PairKey` definidos en el diseño, con repositorio, tarea, SHAs y repetición explícitos.

- [ ] Agrega `ComparisonV2.test_products_keep_separate_precision`: usa producto A con un válido y un falso positivo, y producto B con dos válidos. Comprueba precisiones `0.5` y `1.0`, cada una con denominador `2`.
- [ ] Agrega dos hallazgos llamados F1 en productos distintos. Comprueba adjudicación por clave completa y rechazo de migración histórica ambigua.
- [ ] Agrega pares con repo, base, head, tarea o SHA anterior diferentes. Comprueba rechazo de esos pares y conservación de observaciones sin pareja con motivo.
- [ ] Agrega un intento fallido de 10 segundos seguido de uno exitoso de 20. Comprueba duración de solicitud `30`, ambos intentos informados y costo ausente como desconocido.
- [ ] Agrega no adjudicados, duplicados y defectos de referencia ausentes. Comprueba denominadores explícitos y recuperación de defectos sin calcular cuando no existe referencia.
- [ ] Ejecuta `python3 -m unittest discover -s tests -p 'test_compare_reviews.py' -v`. Confirma las fallas.
- [ ] Implementa salidas por producto y configuración, cohorte pareada y observaciones sin pareja. Conserva tasas de fallo, reintentos y costos de todos los intentos.
- [ ] Repite el comando. Espera métricas separadas y validación estricta de claves.

**Cierre:** la nueva comparación conserva los informes históricos. Ningún agregado mixto se presenta como precisión individual de un revisor.

### T13. Versiona el cegamiento y completa el baseline

**Archivos:** modifica `scripts/build_adjudicacion_ciega.py` y `tests/test_adjudicacion_ciega.py`. Crea `evaluation/reviewer/v2/partition.json`, `evaluation/reviewer/v2/pairing.json` y `docs/evidence/reviewer/E2.md` cuando exista evidencia real.

**Interfaces:** agrega una salida versionada explícita al generador, conservando el modo histórico. La partición asigna PRs completos a ajuste o evaluación reservada. `pairing.json` fija pares y repeticiones antes de observar resultados.

- [ ] Agrega `BlindJudgmentsV2.test_removes_product_cues`: usa nombres, badges y severidades propias de cada producto. Comprueba representación común del diagnóstico, ubicación y evidencia, con severidad original guardada aparte.
- [ ] Agrega `BlindJudgmentsV2.test_all_pushes_share_partition`. Comprueba que todos los pushes de un PR pertenecen al mismo grupo.
- [ ] Agrega `BlindJudgmentsV2.test_historical_outputs_unchanged`. Comprueba que el modo nuevo escribe en una ruta distinta y conserva los archivos originales byte por byte.
- [ ] Agrega un corpus nuevo de 21 casos. Comprueba que el modo versionado usa su inventario y no exige los 20 casos fijados en el generador histórico.
- [ ] Ejecuta `python3 -m unittest discover -s tests -p 'test_adjudicacion_ciega.py' -v`. Confirma las fallas.
- [ ] Versiona normalización, jueces, desacuerdos y desempates. Identifica la adjudicación por IA cuando se use.
- [ ] Repite el comando. Espera claves compatibles con T12 y separación entre ajuste y evaluación.
- [ ] Registra la muestra disponible: 20 de 30 PRs, seis de diez pares de pushes y cinco pares de productos en el SHA exacto, según la base de este plan.
- [ ] Antes de activar D0 y C0, completa los criterios pendientes de E1 o registra una revisión explícita de esos criterios. Deja cada ausencia con su causa.

**Cierre:** entrega el generador offline por separado de la recolección pendiente. La parte de baseline permanece abierta si faltan datos o autorización para obtenerlos.

### T14. Prepara el delta real con obligaciones completas

**Archivos:** crea `review_context.py` y `tests/test_review_context.py`. Mueve desde `review.py` los lectores Git y constructores de contexto que pasan a usar `prepare_review`. Amplía los tipos existentes de `review_domain.py`.

**Interfaces:** define `GitRepository`, `PreparedReview` y `prepare_review(repo: GitRepository, request: WorkRequest, current: Snapshot, policy: ReviewPolicy) -> PreparedReview`. Mueve `GitPath` y `SearchResult` desde T01 y T02. `PreparedReview` reúne plan, hechos, bytes entregados y omisiones con causa.

- [ ] Agrega `IncrementalContext.test_second_push_uses_previous_head_delta`: comprueba que los cambios nuevos corresponden a `previous_head..head`, separados del contexto histórico `merge_base..head`.
- [ ] Agrega eliminación y reversión ausentes del diff global. Comprueba que siguen presentes en el delta y en los hechos usados para resolver hallazgos.
- [ ] Agrega renombre con blobs anterior y actual. Comprueba identidad y procedencia verificadas.
- [ ] Agrega rebase, cambio de base, cambio de política, memoria inválida y cobertura previa parcial. Comprueba modo completo y conservación del delta real cuando pueda determinarse.
- [ ] Agrega `IncrementalContext.test_strict_budget_leaves_obligation_pending`. Comprueba cobertura parcial ante un archivo obligatorio que no cabe, sin cortar hunks de forma invisible.
- [ ] Ejecuta `python3 -m unittest discover -s tests -p 'test_review_context.py' -v`. Confirma las fallas.
- [ ] Implementa la preparación con obligaciones por cambios nuevos y hallazgos abiertos. Usa incremental sólo con memoria suficiente, ancestro válido, base compatible y política compatible.
- [ ] Migra todos los callers y elimina las implementaciones reemplazadas en el mismo bloque. Mantén la interfaz CLI compatible como adaptador.
- [ ] Repite el comando. Espera contexto, hechos y cobertura coherentes para cada caso.

**Cierre:** D0 queda disponible como variante desactivada. Conserva los límites de turnos actuales hasta medir.

### T15. Selecciona callers y tests con procedencia

**Archivos:** modifica `review_context.py` y `tests/test_review_context.py`.

**Interfaces:** agrega procedencia de cada pista a `PreparedReview`: relación textual o sintáctica, archivo de origen y resultado de búsqueda. Reutiliza `SearchResult` y los presupuestos de T02 y T03.

- [ ] Agrega `SelectiveContext.test_prefers_direct_callers_and_tests`: enfrenta referencias directas a coincidencias irrelevantes. Comprueba selección de consumidores y pruebas relacionados dentro del presupuesto.
- [ ] Agrega `SelectiveContext.test_relation_keeps_provenance`: comprueba que una coincidencia textual no se presenta como relación sintáctica.
- [ ] Agrega una pista opcional truncada y una obligación requerida omitida. Comprueba aviso de contexto en el primer caso y cobertura parcial en el segundo.
- [ ] Ejecuta `python3 -m unittest discover -s tests -p 'test_review_context.py' -v`. Confirma las fallas.
- [ ] Implementa la prioridad de relaciones directas y su serialización con presupuesto. Conserva la exploración Read, Grep y Glob del modelo.
- [ ] Repite el comando. Espera selección reproducible y omisiones explícitas.

**Cierre:** C0 queda seleccionable por política y desactivado hasta T16.

### T16. Activa un piloto y decide con evidencia

**Archivos:** completa `docs/reviewer-rollout.md`, `docs/evidence/reviewer/S1.md`, `docs/evidence/reviewer/U0.md`, `docs/evidence/reviewer/D0-C0.md` y el registro de estado en `Plans.md`. Usa las salidas versionadas de `evaluation/reviewer/v2/`.

**Condición:** ejecuta esta tarea sólo con encargo posterior para el piloto y, si corresponde, para mediciones pagadas. Usa un SHA candidato ya validado y la revisión cruzada exigida por el repo para medición viva o release.

- [ ] Publica en el piloto autorizado los casos representativos de T05. Relee el comentario real y comprueba límites y roundtrip, incluidas solicitudes simultáneas.
- [ ] Si el perfil ampliado no alcanza, conserva la activación general pendiente y registra la necesidad de revisar almacenamiento. Permite el perfil de 8000 sólo con reserva medida suficiente.
- [ ] Instala el conjunto de T11 después de drenar escritores antiguos. Comprueba un único comentario, un único escritor y resultados identificados por SHA.
- [ ] Ejecuta una revisión, un descarte concurrente, una explicación, otra revisión del mismo SHA y una recuperación manual de solicitud pendiente. Comprueba las condiciones de T08 y T09.
- [ ] Ensaya el retorno con memoria schema 3 ampliada. Comprueba que conserva descartes y pendientes, y que el escritor compatible continúa publicando.
- [ ] Fija en `pairing.json` tres repeticiones por par como propuesta inicial y el orden alternado control-variante. Congela esos valores antes de la medición.
- [ ] Compara revisión completa del segundo push contra D0, con el mismo target, modelo, proveedor y reglas. Mide C0 como variante separada antes de evaluar la combinación.
- [ ] Evalúa el criterio propuesto de D0: mediana por solicitud al menos un 20% menor, cero High o Critical conocidos perdidos, cero falsos resueltos en controles y precisión reservada al menos igual al control.
- [ ] Informa tamaño de muestra, dispersión, fallos, reintentos y costos desconocidos. Si el control queda incompleto, no lo uses para acreditar una mejora de detección.
- [ ] Exige a C0 los mismos criterios de calidad antes de activarlo y registra su costo y duración frente al control. No atribuyas a C0 el ahorro medido sólo para D0.
- [ ] Registra una decisión por opción: activar, mantener en piloto o volver a `current` y revisión completa. Conserva el esquema y el presupuesto necesarios al desactivar funciones.

**Cierre:** el piloto no se convierte en activación general por un merge incidental. La evidencia identifica SHA del código, configuración, muestra y criterios satisfechos.

## Cierra cada bloque con evidencia del mismo SHA

1. Incluye cada corrección de bug y su prueba discriminante en el mismo cambio.
2. Durante la implementación, ejecuta los comandos focalizados de las tareas afectadas.
3. Después del último cambio del bloque, ejecuta Ruff mediante pre-commit y las pruebas focalizadas pendientes.
4. Ejecuta `pre-commit run --all-files` antes de cerrar. Corrige cualquier fallo sin `--no-verify` ni exclusiones improvisadas.
5. Revisa los hallazgos del bloque en una ronda. Para cambios delicados, propone la revisión cruzada mediante `/Users/dn/quality-kit/cross-review.ps1`.
6. Haz un commit con los archivos explícitos del bloque y su evidencia. Evita incluir cambios ajenos del usuario.
7. Valida la batería completa una vez sobre el SHA final del bloque de código, preferentemente en CI. Conserva los dos shards y `python3 scripts/run_test_shard.py --verify-partition 2`.
8. Si commit, push o CI ya validaron ese SHA, reutiliza la evidencia. Si corriges código después, identifica el nuevo SHA antes de cerrar.
9. Aplica el carril documental sólo cuando todos los archivos estén en la allowlist versionada. Sin clasificación, usa el carril de código. Mantén los checks documentales separados de la batería.
10. Una observación no bloqueante pendiente va a `Plans.md` y al PR del bloque. Sólo un bloqueante con reproducción abre otra ronda, limitada al diff del arreglo y con otro revisor.
11. Si el mismo bloqueante vuelve en dos rondas consecutivas, detén el cierre para decisión del operador. Nunca cierres el bloque con ese hallazgo abierto.
12. Después de un despliegue autorizado, ejecuta el checklist una vez y conserva la evidencia mientras el SHA no cambie.

## Mantén fuera de esta entrega las extensiones opcionales

Registra C1 y C2 como propuestas posteriores: reglas por carpeta desde la base confiable y checks asociados a productor y SHA exactos. Define su precedencia y sus pruebas antes de implementarlas. Ninguna es requisito para cerrar R0, S0, S1, F1, U0, U1 o E2.

Si se requiere historial durable, recuperación automática sin eventos nuevos o más capacidad que la del comentario, reabre la decisión de almacenamiento del diseño antes de ampliar compromisos.
