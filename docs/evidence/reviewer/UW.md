# Evidencia UW: coordinador y worker como plantillas (T10)

Fecha: 2026-10-06. Rama: `rc/uw-workflows`. Base verificada: HEAD `9e010f5` (origin/main al abrir UW, con U1 mergeado); `git merge-base --is-ancestor 179e6a3719b8f... HEAD` → `rc=0`. Paso 4 de "Prepara la ejecución futura": `parse_dismiss_command`/`collaborator_permission`/`collect_dismissals` (U1) y `reconcile`/`publish_checkpoint`/`dispatch_confirmed`/`authenticate_result` (U0) se revisaron antes de editar; ninguna necesitó ajustes de integración.

Tarea: T10 del plan `docs/superpowers/plans/2026-10-05-revisor-correcciones.md`. Diseño: secciones "Alcance y decisiones existentes", "Módulos y límites", "Tipos y firmas propuestas", "U0 con un coordinador y solicitudes persistidas" y "Recuperación y fronteras de confianza".

## Qué cambió

- `templates/ai-review-publish.yml` (coordinador, sin instalar): las cuatro entradas (`pull_request_target`, `issue_comment` [created], `workflow_run` [completed] del worker, `workflow_dispatch` de recuperación con `pr_number`); permisos `contents: read` + `pull-requests: write` + `actions: write` y **ninguna clave del proveedor**; concurrency por repositorio y PR (los cuatro eventos resuelven el mismo grupo) con `cancel-in-progress: false`; exporta el entorno que `cmd_reconcile` exige, incluido `WORKER_REF` (rama default del repo, la ref confiable de despacho); paso condicionado de descarga del resultado del worker (`gh run download`) en la pata `workflow_run`.
- `templates/ai-review-worker.yml` (worker, sin instalar): `workflow_dispatch` con `request_id`/`pr_number`/`head_sha`/`coordinator_run_id`; permisos sólo `contents: read` (sin escritura de comentarios); concurrency por `request_id`; checkout del código confiable (nunca el head del PR como código) con `persist-credentials: false`; el PR se trae como datos (`git fetch` a un ref suelto); paso modelo con las claves del proveedor (las herramientas Read/Grep/Glob las fija el runtime compartido en `review.py`); resultado como artifact `ai-review-result-request-<id>-run-<run>-attempt-<attempt>` con retención 7.
- `review.py`: comando `execute-request` — lee la solicitud persistida del checkpoint, exige `pending`/`failed_retryable`, y escribe `request-package.json` (request_id, kind, finding_id, run_id, attempt, pr_head_sha, base_sha, policy_digest, target, observaciones vacías, cobertura unknown) como datos; no publicar, no ejecutar contenido del PR; el CLI lo registra sin tocar el flujo por defecto. `cmd_reconcile` completa HEAD/BASE desde la revisión persistida cuando el evento no las trae; `despachar_worker` envía `request_id`, `pr_number` y `head_sha` (el contrato exacto de los inputs del worker, sin `comment_login`).
- Desactivado por defecto: las plantillas no están instaladas; `action.yml` conserva su interfaz y su guard; ningún workflow del repo invoca `reconcile` ni `execute-request`; el dogfood sigue en `templates/ai-review.yml`. Ninguna prueba llama al proveedor ni dispara `e1-measure.yml`. Nada se instala en repos consumidores.

## Reproductores (rojos antes, verdes después)

Comando focalizado: `PYTHONPATH=tests python3 -m unittest test_review.Workflows -v` → rojo antes: `FAILED (errors=1)` (las plantillas no existían); verde: `Ran 6 tests in 0.001s` + `OK` (la clase Workflows preexistente cubre `ai-review.yml`; los 6 métodos nuevos se añadieron a esa clase).

- `test_coordinator_is_only_writer`: permisos exactos y ausencia de claves del proveedor en el coordinador.
- `test_worker_cannot_publish`: worker con `contents: read`, sin `pull-requests`/`issues`, sin PATCH de comentarios.
- `test_eventos_resuelven_el_mismo_grupo_por_pr`: las cuatro entradas y el grupo por repositorio y PR con `cancel-in-progress: false`; el worker agrupa por `request_id`.
- `test_entorno_del_modelo`: claves del proveedor en el paso modelo; artifact con retención 7 y nombre por request/run/attempt; checkout de código confiable; herramientas del modelo en el runtime compartido.
- `test_coordinator_exporta_y_worker_recibe_lo_mismo`: `WORKER_REF` exportado por el coordinador; los inputs del worker declarados; sin `comment_login`.
- `test_coordinator_descarga_el_resultado_del_worker`: la pata `workflow_run` baja el resultado.
- `test_execute_request_arma_el_paquete_del_worker`: solicitud pendiente → paquete con request_id/run/attempt/pr_head_sha/target/policy_digest; solicitud inexistente → exit sin paquete.
- `test_actionlint_firma_las_plantillas`: actionlint 1.7.12 (la versión fijada en `.github/workflows/ci.yml`) sobre ambas plantillas.

## Ronda de revisión (swarm + paneles, delta sin commitear)

- Worker poteto-agent (zai-coding-plan/glm-5.3): ISSUES — actionlint OK y permisos correctos, pero las pruebas exigidas por el plan no estaban en el delta (la clase Workflows se había perdido con un checkout durante la implementación; re-añadida) y detectó huecos de cableado.
- Paneles adversarios en paralelo (diversidad reducida: solo familia GLM): panel-glm53, panel-glm52, panel-glm47, panel-glm5turbo. Convergieron con repros ejecutados y se corrigió en la misma ronda:
  1. `cmd_reconcile` exigía `WORKER_REF`/`BASE_SHA` que la plantilla nunca exportaba → el coordinador moría en las cuatro entradas. Arreglo: `WORKER_REF` exportado (rama default) y fallback a la revisión persistida para HEAD/BASE. Pruebas: `test_coordinator_exporta_y_worker_recibe_lo_mismo`.
  2. Contrato de despacho roto: `despachar_worker` no enviaba `pr_number`/`head_sha` (required del worker) y enviaba `comment_login` que no era input. Arreglo: el despacho envía exactamente los inputs del worker; `pr_number` del contexto y `head_sha` del target de la solicitud. Prueba: `test_coordinator_exporta_y_worker_recibe_lo_mismo`.
  3. La pata `workflow_run` no bajaba el artifact y `cmd_reconcile` leía `result.json` a ciegas. Arreglo: paso de descarga condicionado a `workflow_run`. Prueba: `test_coordinator_descarga_el_resultado_del_worker`.
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
- Descarga del artifact sin `--name` (quick win, arreglado): `gh run download` extrae bajo el nombre del artifact y `cmd_reconcile` lee `result.json` a ciegas. Arreglo: descargar a `descarga/` y copiar el `result.json` encontrado a la raíz que lee el reconciliador. La aserción de `test_coordinator_descarga_el_resultado_del_worker` cubre el paso.
- `pull-requests: read` para el worker (quick win, arreglado): `execute-request` lee comentarios con `gh api issues/{n}/comments`, que con sólo `contents: read` da 403 en recursos privados. Arreglo: `pull-requests: read` (lee sin publicar); `test_worker_cannot_publish` actualizado (sigue prohibido `pull-requests: write`).
- `GITHUB_ENV` no afecta al paso actual (quick win, arreglado): el paso `execute-request` leía envs escritas por sí mismo. Arreglo: `env:` directo del paso.
- Resolución del PR en `workflow_run` para concurrency (Major, heavy): `pull_requests` llega vacío para runs despachados; hoy cae al id del run (no serializa con el PR). Fila: la corrutina completa T10/T11 debe enviar `pr_number` en el despacho y agrupar por él.
- El paso modelo es un gancho sin `result.json` (Major, heavy): la ejecución real del runtime y su `result.json` se cablean en T11; hoy la plantilla no está instalada y ese carril no corre. Fila confirmada (ya estaba).

Verificación de la ronda 2: focales `Ran 12 tests in 0.001s` + `OK` + CoordinadorCli `Ran 8 tests ... OK`; batería `457 passed, 133 subtests passed in 48.96s`; pre-commit 8/8; verify-partition `total: 457`, `verify: OK`; actionlint sin errores.

## Cierre T10 y pendientes

- Las plantillas permanecen como plantillas (sin instalar); `action.yml` conserva interfaz y guard; el dogfood no cambia. El paso "modelo" del worker queda como gancho del runtime del proveedor (aislado, con sus claves y sin poder publicar); `result.json` del carril `workflow_run` requiere ese paso completo — fila antes de T11.
- Fila (no toca esta ronda): la concurrency de `workflow_run` cae al id del run cuando `pull_requests` llega vacío (mitigación: enviar `pr_number` en el despacho y agrupar por él — requiere la corrutina completa T10/T11); `AI_REVIEW_DISABLED`/`timeout-minutes` y gate de forks/drafts para las plantillas nuevas; `secrets.API_KEY` vs el nombre real en consumidores (T11); `git fetch` sin credenciales en repos privados; ejecutar `review.py` vía `action.yml` en consumidores; filtros de issue vs PR en `issue_comment`; intent no numérico en `execute-request` (traceback crudo); carreras de dos escritores sin CAS (U0); filas previas de S0/S1/F1/U0/U1 en el ledger.
