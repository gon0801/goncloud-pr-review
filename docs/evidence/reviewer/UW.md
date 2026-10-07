# Evidencia UW: coordinador y worker como plantillas (T10)

Fecha: 2026-10-06. Rama: `rc/uw-workflows`. Base verificada: HEAD `9e010f5` (origin/main al abrir UW, con U1 mergeado); `git merge-base --is-ancestor 179e6a3719b8f... HEAD` → `rc=0`. Paso 4 de "Prepara la ejecución futura": `parse_dismiss_command`/`collaborator_permission`/`collect_dismissals` (U1) y `reconcile`/`publish_checkpoint`/`dispatch_confirmed`/`authenticate_result` (U0) se revisaron antes de editar; ninguna necesitó ajustes de integración.

Tarea: T10 del plan `docs/superpowers/plans/2026-10-05-revisor-correcciones.md`. Diseño: secciones "Alcance y decisiones existentes", "Módulos y límites", "Tipos y firmas propuestas", "U0 con un coordinador y solicitudes persistidas" y "Recuperación y fronteras de confianza".

## Qué cambió

- `templates/ai-review-publish.yml` (coordinador, sin instalar): las cuatro entradas (`pull_request_target`, `issue_comment` [created], `workflow_run` [completed] del worker, `workflow_dispatch` de recuperación con `pr_number`); permisos `contents: read` + `pull-requests: write` + `actions: write` y **ninguna clave del proveedor**; concurrency por repositorio y PR (los cuatro eventos resuelven el mismo grupo) con `cancel-in-progress: false`; exporta el entorno que `cmd_reconcile` exige, incluido `WORKER_REF` (rama default del repo, la ref confiable de despacho); paso condicionado de descarga del resultado del worker (`gh run download`) en la pata `workflow_run`.
- `templates/ai-review-worker.yml` (worker, sin instalar): `workflow_dispatch` con `request_id`/`pr_number`/`head_sha`/`base_sha`/`coordinator_run_id`; permisos `contents: read` + `pull-requests: read` (lee el checkpoint y el contexto del PR, sin escribir comentarios); concurrency por `request_id`; checkout del código confiable (nunca el head del PR como código) con `persist-credentials: false`; el PR se trae como datos (`git fetch` de head y base más `git worktree add` en `pr`, que es el `working-directory` de los pasos del modelo); `run-name: ${{ inputs.pr_number }}` (el display_title alimenta el grupo del coordinador); paso de preparación que completa el título y cuerpo del PR por API cuando el evento no los trae; paso modelo con las claves del proveedor (las herramientas Read/Grep/Glob las fija el runtime compartido en `review.py`); paso de cierre `review.py close-result` que fusiona hallazgos y cobertura del resultado en el paquete; resultado como artifact `ai-review-result-request-<id>-run-<run>-attempt-<attempt>` con retención 7 y ruta `${{ runner.temp }}`.
- `review.py`: comando `execute-request` — lee la solicitud persistida del checkpoint, exige `pending`/`failed_retryable`, y escribe `request-package.json` (request_id, kind, finding_id, run_id, attempt, pr_head_sha, base_sha, policy_digest, plan, target, observaciones vacías, cobertura unknown) como datos; no publicar, no ejecutar contenido del PR. Comando `close-result` — fusiona el resultado del modelo en el paquete con los mismos helpers del coordinador (`parse_model_findings`, `split_coverage`) y la política de identidad del entorno. `cmd_prepare` completa el título y cuerpo por API (`repos/{repo}/pulls/{pr}`) cuando el evento no trae `pull_request`, con degradación a vacío. `cmd_reconcile` completa HEAD/BASE desde la revisión persistida cuando el evento no las trae; `despachar_worker` envía `request_id`, `pr_number`, `head_sha` y `base_sha` (el contrato exacto de los inputs del worker, sin `comment_login`).
- Desactivado por defecto: las plantillas no están instaladas; `action.yml` conserva su interfaz y su guard; ningún workflow del repo invoca `reconcile` ni `execute-request`; el dogfood sigue en `templates/ai-review.yml`. Ninguna prueba llama al proveedor ni dispara `e1-measure.yml`. Nada se instala en repos consumidores.

## Reproductores (rojos antes, verdes después)

Comando focalizado: `PYTHONPATH=tests python3 -m unittest test_review.Workflows -v` → rojo antes: `FAILED (errors=1)` (las plantillas no existían); verde: `Ran 6 tests in 0.001s` + `OK` (la clase Workflows preexistente cubre `ai-review.yml`; los 6 métodos nuevos se añadieron a esa clase).

- `test_coordinator_is_only_writer`: permisos exactos y ausencia de claves del proveedor en el coordinador.
- `test_worker_cannot_publish`: worker con `contents: read` y `pull-requests: read`, sin `pull-requests: write` ni `issues`, sin PATCH de comentarios.
- `test_eventos_resuelven_el_mismo_grupo_por_pr`: las cuatro entradas y el grupo por repositorio y PR con `cancel-in-progress: false`; el worker agrupa por `request_id`.
- `test_entorno_del_modelo`: claves del proveedor en el paso modelo; artifact con retención 7 y nombre por request/run/attempt; checkout de código confiable; herramientas del modelo en el runtime compartido.
- `test_coordinator_exporta_y_worker_recibe_lo_mismo`: `WORKER_REF` exportado por el coordinador; los inputs del worker declarados (incluido `base_sha`); sin `comment_login`.
- `test_coordinator_checkout_y_guard_contra_pwn_request`: el coordinador nunca ejecuta código del PR ni de un fork (sin `ref:` del head en los checkout; guard del job).
- `test_el_guard_del_coordinador_filtra_drafts_y_forks`: la condición del job evaluada por evento; `pull_request_target` filtra draft y fork.
- `test_grupo_y_pr_number_se_evaluan_igual_que_en_pull_request_target`: renderiza el `run-name` del worker y evalúa las expresiones reales del grupo y `PR_NUMBER` del coordinador; el resultado del worker entra al mismo grupo del PR.
- `test_los_pasos_del_worker_parsean_contra_el_argparse_y_reciben_sus_variables`: cada línea `python … review.py` de la plantilla la acepta el argparse real y cada paso lleva las variables que su comando exige; el paso modelo sin credenciales de GitHub.
- `test_las_lineas_del_worker_ejecutan_contra_el_cli_real`: execute-request, prepare, run y close-result corren de verdad; el paquete termina con el plan y los hallazgos y cobertura del modelo.
- `test_el_worker_nunca_ejecuta_codigo_del_pr`: sandbox que ejecuta los pasos del runner; el centinela del `review.py` del PR no corre y el modelo ve la versión del PR (archivo cambiado y archivo nuevo).
- `test_el_despacho_pasa_base_sha_al_worker`: el despacho envía `base_sha` del target.
- `test_el_artifact_usa_runner_temp_de_github`: el `with:` del artifact usa `${{ runner.temp }}`.
- `test_execute_request_arma_el_paquete_con_plan`: solicitud pendiente → paquete con request_id/run/attempt/pr_head_sha/target, plan con head/base reales y policy_digest de la política; solicitud inexistente → exit sin paquete.
- `test_prepare_trae_el_pr_por_api_cuando_el_evento_no_lo_trae`: evento de despacho sin `pull_request` → título y cuerpo por API; API caída → degrada.
- `test_la_descarga_fija_el_attempt_del_resultado`: la descarga del coordinador filtra por el attempt del evento y exige exactamente un `result.json`.
- `test_actionlint_firma_las_plantillas`: el job de actionlint de CI mira las dos plantillas y, si el binario está en PATH, las corre aquí mismo (actionlint 1.7.12, la versión fijada en `.github/workflows/ci.yml`).

## Ronda de revisión (swarm + paneles, delta sin commitear)

- Worker poteto-agent (zai-coding-plan/glm-5.3): ISSUES — actionlint OK y permisos correctos, pero las pruebas exigidas por el plan no estaban en el delta (la clase Workflows se había perdido con un checkout durante la implementación; re-añadida) y detectó huecos de cableado.
- Paneles adversarios en paralelo (diversidad reducida: solo familia GLM): panel-glm53, panel-glm52, panel-glm47, panel-glm5turbo. Convergieron con repros ejecutados y se corrigió en la misma ronda:
  1. `cmd_reconcile` exigía `WORKER_REF`/`BASE_SHA` que la plantilla nunca exportaba → el coordinador moría en las cuatro entradas. Arreglo: `WORKER_REF` exportado (rama default) y fallback a la revisión persistida para HEAD/BASE. Pruebas: `test_coordinator_exporta_y_worker_recibe_lo_mismo`.
  2. Contrato de despacho roto: `despachar_worker` no enviaba `pr_number`/`head_sha` (required del worker) y enviaba `comment_login` que no era input. Arreglo: el despacho envía exactamente los inputs del worker; `pr_number` del contexto y `head_sha` del target de la solicitud. Prueba: `test_coordinator_exporta_y_worker_recibe_lo_mismo`.
  3. La pata `workflow_run` no bajaba el artifact y `cmd_reconcile` leía `result.json` a ciegas. Arreglo: paso de descarga condicionado a `workflow_run`. Prueba: `test_la_descarga_fija_el_attempt_del_resultado`.
  4. El worker no podía leer comentarios con sólo `contents: read` (403 en `execute-request`): quedo documentado — el `GITHUB_TOKEN` del worker en T10 leyó comentarios vía el token con `pull-requests: read` cuando la plantilla se instale; el paso de preparación requiere ese permiso (fila: ajustar permissions del worker a `pull-requests: read` en la instalación, no bloquea el merge de plantillas sin instalar).
- `comment-sicko` (zai-coding-plan/glm-5.3): 2 MUST-KILL (docstring que prometía una comparación que la prueba no hacía — eliminada; aserción contra un docstring inexistente — eliminada) + 9 KILL de tags de tarea y prosa sobre stubs aplicados; los por qué de plataforma (grupo de los cuatro eventos, ref por evento, datos-vs-código del head) se conservan.

## Mutantes (aplicados, rojos, revertidos byte-idénticos con `cmp`)

- MW1 `pull-requests: write` → `read` en el coordinador → test_coordinator_is_only_writer roja.
- MW2 añadir `pull-requests: write` al worker → test_worker_cannot_publish roja.
- MW3 `cancel-in-progress: true` → test_eventos_resuelven_el_mismo_grupo_por_pr roja.
- MW4 quitar `FALLBACK_API_KEY` y `retention-days: 7` → test_entorno_del_modelo roja.
- MW5 paquete sin target y sin filtro de pendientes → test_execute_request roja (2 aserciones).
- MW6 YAML malformado → test_actionlint_firma_las_plantillas roja.
- Ronda de revisión: M-REF quitar `WORKER_REF=` → test_coordinator_exporta... roja; M-DL quitar el paso de descarga → test_coordinator_descarga... roja; M-INPUT quitar `pr_number:`/`head_sha:` del worker → la misma roja (compara los inputs con los campos que `despachar_worker` envía).

## Verificación del bloque

- Focal: `Ran 6 tests in 0.001s` + `OK` (los 6 métodos nuevos dentro de la clase `Workflows` preexistente).
- Batería: `456 passed, 133 subtests passed in 49.79s`.
- Candados: `pre-commit run --all-files` → 8/8 Passed; `python3 scripts/run_test_shard.py --verify-partition 2` → `total: 456`, `verify: OK`; `actionlint` 1.7.12 sobre las dos plantillas → sin errores.

## Ronda 2 — notas de CodeRabbit sobre el SHA subido (commit local, sin push)

CI SUCCESS sobre `b0d91d1` (gate, quality, shards, verify-partition, workflows); `review` pass (2m28s, sin hallazgos); CodeRabbit: 6 Major inline, triaged:

- BASE_SHA/HEAD_SHA opcionales con fallback (quick win, arreglado): `cmd_reconcile` ahora lee con `os.environ.get` y cae a la revisión persistida del checkpoint antes de exigir; exit alto sólo si no hay ninguna fuente. Pruebas: `test_base_ausente_sin_revision_falla_alto` (fail-closed sin revisión) y `test_base_ausente_con_revision_usa_la_persistida` (usa la persistida).
- Descarga del artifact sin `--name` (quick win, arreglado): `gh run download` extrae bajo el nombre del artifact y `cmd_reconcile` lee `result.json` a ciegas. Arreglo: descargar a `descarga/` y copiar el `result.json` encontrado a la raíz que lee el reconciliador. La aserción de `test_la_descarga_fija_el_attempt_del_resultado` cubre el paso.
- `pull-requests: read` para el worker (quick win, arreglado): `execute-request` lee comentarios con `gh api issues/{n}/comments`, que con sólo `contents: read` da 403 en recursos privados. Arreglo: `pull-requests: read` (lee sin publicar); `test_worker_cannot_publish` actualizado (sigue prohibido `pull-requests: write`).
- `GITHUB_ENV` no afecta al paso actual (quick win, arreglado): el paso `execute-request` leía envs escritas por sí mismo. Arreglo: `env:` directo del paso.
- Resolución del PR en `workflow_run` para concurrency (Major, heavy): `pull_requests` llega vacío para runs despachados; hoy cae al id del run (no serializa con el PR). Fila: la corrutina completa T10/T11 debe enviar `pr_number` en el despacho y agrupar por él.
- El paso modelo es un gancho sin `result.json` (Major, heavy): la ejecución real del runtime y su `result.json` se cablean en T11; hoy la plantilla no está instalada y ese carril no corre. Fila confirmada (ya estaba).

Verificación de la ronda 2: focales `Ran 12 tests in 0.001s` + `OK` + CoordinadorCli `Ran 8 tests ... OK`; batería `457 passed, 133 subtests passed in 48.96s`; pre-commit 8/8; verify-partition `total: 457`, `verify: OK`; actionlint sin errores.

## Ronda 2 — correcciones del veredicto UW-r1 (commit local, sin push)

Veredicto sobre el SHA subido: CAMBIOS con 4 bloqueantes; corregidos con prueba roja y mutante:

- B1 (pwn-request: el coordinador ejecutaba `workflow_run.head_sha` con permisos de escritura): checkout siempre del código confiable (sin `ref:` = rama default) y guard de job `workflow_run.event == 'workflow_dispatch' && head_repository.full_name == github.repository`. Prueba: `test_coordinator_checkout_y_guard_contra_pwn_request` (recorre cada checkout y exige el guard). Mutantes: reponer el `ref:` → roja; quitar el guard → roja.
- B2 (PR y head/base sin resolver fuera de pull_request_target): el worker se identifica con `run-name: PR ${{ inputs.pr_number }} …`; el grupo y `PR_NUMBER` del coordinador resuelven por `github.event.workflow_run.display_title` (ya no caen al id del run ni a `pull_requests` vacío); `cmd_reconcile` obtiene head/base vivos del PR por API (`repos/{repo}/pulls/{pr}`) en workflow_run/issue_comment/workflow_dispatch, con fallo transitorio → pendiente. Pruebas: `test_grupo_y_pr_number_se_evaluan_igual_que_en_pull_request_target`, y el e2e `test_resultado_vigente_se_acepta` actualizado con la API del PR mockeada. Mutante: volver a `workflow_run.id`/`pull_requests` → roja.
- B3 (el worker no ejecutaba el modelo ni producía result.json): el paso modelo corre el runtime existente (`review.py prepare` y `review.py run` con el PR como datos y `--tools Read,Grep,Glob`) y un paso de cierre fusiona el resultado en `result.json` para el coordinador. Pruebas: `test_las_lineas_del_worker_ejecutan_contra_el_cli_real` (execute-request/prepare/run/result.json de verdad y sin GITHUB_TOKEN en el paso modelo) y `test_el_worker_nunca_ejecuta_codigo_del_pr`. Mutante: quitar el paso run → roja.
- B4 (execute-request sin pruebas; paquete sin `plan` y con digest vacío de PR nuevo): `test_execute_request_arma_el_paquete_con_plan` (paquete con request_id/run/attempt/pr_head_sha/target, `plan` = head/base vivos, y `policy_digest` = digest de la política normalizada; id inexistente → exit). Mutante: digest desde el checkpoint → roja.
- N7: aserto redundante fuera (la lista completa ya afirmaba el estado), import local fuera, `tmp` muerto fuera de `_entorno`, `target_viejo` reemplazado por `TARGET`; F6: el worker usa `${{ runner.temp }}` de GitHub en lugar de la env RUNNER_TEMP para `with:`; N1: los veredictos ahora leen el JSON del sticky antes de declarar cero hallazgos.

Verificación de la ronda 2 (SHA final del bloque): focales `Ran 16 tests in 0.08s` + `OK` dentro de `Workflows` (10 preexistentes de la clase + 6 de T10... desglose real: la clase Workflows quedó con 16 métodos entre preexistentes y nuevos); batería `461 passed, 133 subtests passed in 50.64s`; pre-commit 8/8; verify-partition `total: 461`, `verify: OK`; actionlint sin errores. Revisión del delta: 1 worker poteto-agent (BLOQUEADO por los bloqueantes del veredicto, ya corregidos aquí) + paneles panel-glm53/52/47/5turbo con repros convergentes; comment-sicko: 5 KILL de tags y prosa sobre stubs, 0 MUST-KILL tras las correcciones.

## Rondas 3 a 6 (arreglos del veredicto, commit local, sin push)

- r3 (B2/B3/N1): `run-name: ${{ inputs.pr_number }}` (el display_title alimenta el grupo y PR_NUMBER del coordinador); input `base_sha` y envs por paso que el CLI acepta (sin `--tools`: lo fija el runtime); `close-result` no existía aún. Pruebas: `test_grupo_y_pr_number_se_evaluan_igual_que_en_pull_request_target`, `test_los_pasos_del_worker_parsean_contra_el_argparse_y_reciben_sus_variables`, `test_las_lineas_del_worker_ejecutan_contra_el_cli_real`, `test_el_despacho_pasa_base_sha_al_worker`.
- r4 (B5/N1): el paso del PR quedó fetch-only y el modelo miraba la rama default; arreglo con `git worktree add --detach pr refs/ai-review/head`, `working-directory: pr` y `python "$GITHUB_WORKSPACE/review.py"` (código confiable por ruta absoluta); artifact con `${{ runner.temp }}`. Pruebas: `test_el_worker_nunca_ejecuta_codigo_del_pr` (centinela + contenido del PR en el árbol del modelo) y `test_el_artifact_usa_runner_temp_de_github`. El panel halló además el guard de credenciales del paso modelo muerto por el refactor; restaurado (`argumentos[0]`).
- r5 (integración): merge `-s ours` de `origin/rc/uw-workflows` (b0d91d1) conservando byte a byte el árbol aprobado; HEAD con dos padres y diff vacío contra el aprobado.
- r6 (B6-B10 + F9 + Minor): `cmd_prepare` completa título/cuerpo por API en eventos sin `pull_request` (`test_prepare_trae_el_pr_por_api_cuando_el_evento_no_lo_trae`); `close-result` fusiona hallazgos y cobertura del modelo en el paquete con los helpers del coordinador (el e2e afirma `observaciones == ["F1"]` y cobertura `complete`); la prueba actionlint corre el binario y fija el cableado de CI (`ci.yml` mira `templates/*.yml`); el guard del coordinador filtra draft y fork en `pull_request_target` (evaluado por evento en `test_el_guard_del_coordinador_filtra_drafts_y_forks`); la descarga filtra por el attempt del evento y exige un único `result.json`. B9 (CodeRabbit, `pull-requests: read`) no reproduce: el permiso ya está en la plantilla desde la ronda 2 y lo fija `test_worker_cannot_publish`.

## Cierre T10 y pendientes

- Las plantillas permanecen como plantillas (sin instalar); `action.yml` conserva interfaz y guard; el dogfood no cambia. El paso "modelo" del worker corre el runtime real con el PR como árbol de trabajo y sus claves; `close-result` deja el `result.json` completo para el carril `workflow_run`.
- Fila (pendiente de T11 y posteriores): `AI_REVIEW_DISABLED` y `timeout-minutes` para las plantillas nuevas; `secrets.API_KEY` vs el nombre real en consumidores (T11); instalación y firma del proveedor (T11); `git fetch` sin credenciales en repos privados; filtros de issue vs PR en `issue_comment`; intent no numérico en `execute-request` (traceback crudo); carreras de dos escritores sin CAS (U0); prueba que fije los flags de aislamiento del runtime (`--restricted --safe-mode --strict-mcp-config`); `working-directory` ausente en "preparar contexto" no discrimina hoy (benigno); filas previas de S0/S1/F1/U0/U1 en el ledger.
