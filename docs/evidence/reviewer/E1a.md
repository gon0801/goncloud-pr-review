# Evidencia de E1a — workflow de medición sin publicación

Fecha: 2026-09-27. Todo local, sin push, sin PR, sin CI, sin corridas pagadas. La apertura del PR de E1a queda para el operador: este bloque crea el workflow, no lo dispara.

## Base y head

- Base: `7e2dfac` (`origin/main` verificada 2026-09-27).
- Commits: `test:` con la prueba focal (rojo primero contra el yml ausente), `feat:` con `.github/workflows/e1-measure.yml`, `docs:` con esta evidencia. SHAs en el LISTO.
- Enmienda r2 (2026-09-27): el árbol medido pasa a ser el head del PR y el revisor se fija fuera del workspace — bloqueante único de Claude sobre `9925f3b`; ver la sección final.
- Enmienda E1aP (2026-09-27): selector de proveedor para la retanda DeepSeek directa; ver la sección final.

## Propósito

`e1-measure.yml` corre el revisor sobre un caso del corpus (un PR y un SHA dados, sin importar si el PR sigue abierto) y deja la salida capturada para que E1b la consolide. Es el prerrequisito de la tanda: sin E1a mergeado no se dispara ninguna corrida pagada.

## Garantía de no publicación (dos capas)

1. Por diseño: el workflow ejecuta `prepare`, `install` y `run`; no existe paso `publish` (ni el substring `publish` en el archivo, lo afirma el test).
2. Por alcance de token: `permissions: contents: read` a nivel workflow, sin `pull-requests: write`; el GITHUB_TOKEN ni siquiera podría comentar.

Además: cada secreto de ruta (`AI_REVIEW_API_KEY`, `DEEPSEEK_API_KEY`) se referencia dos veces — la comparación contra vacío en la validación temprana y el env de su paso `run` — y nunca hay `echo`; una ruta no usa la llave de la otra.

## Inputs (workflow_dispatch, todos requeridos)

- `pr`: número de PR del corpus.
- `head`: SHA de 40 hex de la punta revisada (vive en `refs/pull/<pr>/head`).
- `base`: SHA de 40 hex del estado de main al merge.
- `caso`: etiqueta del caso (p. ej. `pr2`), usada en el grupo de concurrencia y el nombre del artefacto.

Concurrencia: `e1-measure-<caso>` con `cancel-in-progress: false` (las mediciones no se cancelan entre sí).

## Fidelidad con una revisión normal

Misma secuencia que `action.yml` sin `gate` ni publicación: `prepare` → `install` → `run`, invocando `python3 review.py <subcomando>`; mismo cache key de instalación (`ai-review-install-<os>-py<ver>-claude-2.1.282-litellm-1.102.1`), proveedor seleccionable por dispatch entre `opencode-go` y `deepseek` (ambos sirven DeepSeek V4.1 Flash; sin input de modelo), mismo `ATTEMPTS=2` congelado en el corpus y mismo presupuesto por defecto. `prepare` recibe `GITHUB_EVENT_PATH` con el título y cuerpo reales del PR (vía `gh api`), igual que una revisión por `pull_request`. Diferencias deliberadas: sin `gate` (cada dispatch es una medición fresca), `HEAD_SHA`/`BASE_SHA` vienen de los inputs del corpus, y el checkout trae `fetch-depth: 0` más `git fetch origin pull/<pr>/head` si falta el objeto, con `git cat-file -e` de head y base antes de `prepare`.

## Artefactos y retención

`actions/upload-artifact@v4` con `result.json` y `manifest.json` del work dir (`$RUNNER_TEMP/ai-review`), nombre `e1-salida-<caso>-<head corto>`, retención por defecto de Actions. `GITHUB_STEP_SUMMARY` registra sólo caso, head, `subtype` y `turnos` (sin contenido de hallazgos; esos quedan en el artefacto). La copia a `evaluation/reviewer/salidas/` la hará E1b.

## Lo que NO hace

- No corre `gate` (medición fresca por dispatch).
- No corre `publish` y no tiene alcance para hacerlo: publica cero comentarios.
- No dispara `ai-review.yml` (éste sólo corre en `pull_request`).
- Esta enmienda no ejecuta mediciones ni imprime o publica valores de secretos. Cuando se ejecuta una medición, `run` usa la llave para autenticar la solicitud y la transmite al proveedor seleccionado: la validación sólo compara los secretos contra vacío y cada ruta pasa el suyo como env de su paso `run`.

## TDD

Comando focalizado: `python3 -m unittest discover -s tests -p 'test_e1a_workflow.py' -v`. En r1 eran 8 casos (dispatch con inputs, permisos de sólo lectura, sin substring `publish`, secreto único sin `echo`, proveedor fijo, árbol del head con revisor fuera del workspace, concurrencia sin cancelación, artefacto con result+manifest); desde la enmienda E1aP la suite queda en 10 casos (el secreto único pasa a ruta exclusiva, el proveedor fijo pasa a choice+validación+propagación, y se agregan choice con claves de `PROVIDERS` y validación temprana; ver sección final).

1. Rojo: sin el yml → `FAILED (errors=1)` (FileNotFoundError), exit 1.
2. Verde: creado el yml → `Ran 7 tests` en r1, `Ran 8 tests` desde la enmienda r2, `Ran 10 tests` desde la enmienda E1aP, `OK`, exit 0.
3. Mutaciones (demuestran que la prueba discrimina): agregar `pull-requests: write` → `FAILED (failures=1)`, exit 1; agregar un paso `publish` → `FAILED (failures=1)`, exit 1. Ambas revertidas → suite completa en verde, exit 0.

## Comandos y resultados sobre el head final

- `pre-commit run --all-files` → 8 hooks Passed, exit 0.
- `python3 -m unittest discover -s tests -p 'test_e1a_workflow.py' -v` → `Ran 10 tests`, `OK`, exit 0.
- `actionlint .github/workflows/e1-measure.yml` → sin salida, exit 0.
- No se corrió la batería completa local (prohibido).

## Enlace de CI

No aplica en esta ronda: push, PR y CI prohibidos por el encargo. El PR de E1a lo abre el operador.

## TANDA-PENDIENTE (actualizada en la enmienda E1aP)

- Las 20 corridas del revisor vía `workflow_dispatch` de E1a terminaron `success` a nivel Actions, pero 15 artefactos traen `ai_review_error: la revision fallo en todos los intentos (proveedor no disponible por ahora)` (OpenCode Go sin cuota).
- Retanda autorizada y ORDENADA por David (2026-09-27): repetir SOLO esas 15 con `proveedor: deepseek` (API directa, DeepSeek V4.1 Flash, gasto por token autorizado). El workflow ya lo soporta (enmienda E1aP); el único bloqueo restante es que el operador cargue `DEEPSEEK_API_KEY` en GitHub. Nadie leyó, imprimió, copió ni transmitió ninguna llave.
- 0 corridas de CodeRabbit (sólo lectura de comentarios existentes).

## Enmienda r2 — el árbol medido es el head del PR; revisor fijado fuera del workspace (2026-09-27)

Claude marcó un bloqueante sobre `9925f3b`: el checkout no fijaba `ref`, así que en un `workflow_dispatch` el árbol quedaba en main; el paso de fetch sólo traía el objeto del head, no movía el árbol. `review.py` lee del árbol en dos lugares (`grep_files` corre `git grep` de donde salen `callers.txt` y `tests.txt`, y Claude hereda el directorio del workspace para sus Read/Grep/Glob): el modelo habría revisado el diff viejo leyendo archivos que en ese momento no existían, contaminando las 20 corridas pagadas.

Reproducción local (caso pr24 del corpus de E1, base `2b372305…`, head `0ace22fb…`): `prepare` con el árbol en main (9925f3b) vs con el árbol en el head:

- `tests.txt` DIFIERE: en main aparece `tests/test_compare_reviews.py` (creado después, en E0).
- `callers.txt` DIFIERE.
- `diff.patch` y `conventions.md` iguales.

Arreglo (el literal de Claude, dos piezas porque ninguna alcanza sola):

1. El revisor que se ejecuta queda fijado a main, fuera del árbol medido: `review.py` y `prompt.md` se copian a `$RUNNER_TEMP/reviewer` y los pasos invocan `python3 "$RUNNER_TEMP/reviewer/review.py" <subcmd> --prompt "$RUNNER_TEMP/reviewer/prompt.md"`. Poner `ref:` en el único checkout no alcanza: entonces correría el `review.py` viejo de cada PR. Tampoco vale un segundo checkout dentro del workspace: el modelo vería esos archivos con Glob/Grep.
2. El árbol de trabajo se mueve a la punta revisada: tras traer `pull/<pr>/head` y verificar ambos SHAs con `git cat-file -e`, `git checkout --detach "${{ inputs.head }}"`.

Test nuevo `test_mide_el_arbol_del_head_con_revisor_fuera`: afirma el `checkout --detach` a `inputs.head`, la copia del revisor, las tres invocaciones desde `"$RUNNER_TEMP/reviewer/review.py"`, los dos `--prompt` del revisor fijo, y que ninguna línea vuelve a invocar `python3 review.py` desde el workspace. Rojo contra el yml de r1 (`FAILED (failures=1)`, los 7 de r1 en verde); verde con el arreglo (`Ran 8 tests`, `OK`). Mutación quitando el `checkout --detach` → `FAILED (failures=1)`, exit 1; revertida → `Ran 8 tests`, `OK` y actionlint exit 0.

## Enmienda r3 — la prueba del head exige destino y orden del checkout (2026-09-27)

Residual 1 de Claude sobre el PR (F2, Medium): la prueba de r2 sólo exigía el literal `git checkout --detach`; seguía en verde si el destino volvía a main/base o si el paso quedaba después de `prepare` (el bloqueante original de r2), de modo que la frase "afirma el `checkout --detach` a `inputs.head`" de la sección anterior sólo es cierta desde esta ronda. `test_mide_el_arbol_del_head_con_revisor_fuera` ahora exige el literal completo `git checkout --detach "${{ inputs.head }}"`, que `cp review.py prompt.md` precede al checkout y que el checkout precede a `review.py" prepare`. La suite sigue en 8 casos y los conteos de las secciones TDD y de comandos ya lo reflejan.

Rojo demostrado con tres mutaciones, cada una `FAILED (failures=1)` con la suite de r2 en verde: destino `origin/main` (lo caza el literal completo), checkout movido tras `prepare` (lo caza el orden contra prepare) y copia del revisor movida tras el checkout (lo caza el orden de la copia). Revertidas → `Ran 8 tests`, `OK`, exit 0.

## Enmienda E1aP — selector de proveedor para la retanda DeepSeek directa (2026-09-27)

Las 20 mediciones E1 terminaron `success` a nivel Actions pero 15 artefactos traen `ai_review_error` por proveedor sin cuota (OpenCode Go). David ya ordenó repetir SOLO esas 15 con `PROVIDER=deepseek` (API directa, mismo modelo DeepSeek V4.1 Flash, gasto por token autorizado). Este bloque prepara el workflow y NO dispara mediciones; la llave directa aún no existe en GitHub (cargarla es el único bloqueo de la retanda) y nadie leyó, imprimió, copió ni transmitió ninguna llave. La lista `PROVIDERS` de `review.py` quedó intacta (la prueba la lee por AST, no la modifica) y no hay input de modelo.

Cambio sobre la base `96f46d7` (merge del PR #32, que ya incluía la enmienda r3):

1. Input `proveedor`: `type: choice`, `default: opencode-go`, opciones exactamente las claves de `PROVIDERS` (`opencode-go`, `deepseek`). La prueba compara las opciones con las claves derivadas por `ast` de `review.py`, no con una segunda lista escrita a mano.
2. Validación temprana (primer paso, antes incluso del checkout): canoniza el input (sin espacios, minúsculas) y lo chequea contra la allowlist con `exit 1`; por ruta, exige presencia del secreto (`secrets.X != ''`) y sin él falla antes de instalar o correr. El output canónico es `steps.proveedor.outputs.nombre`; nunca se inyecta `inputs.proveedor` directo a `PROVIDER`.
3. Rutas de secreto exclusivas, por decisión de diseño de David sin el idioma `&& ... || ...`: dos pasos `Run` condicionales mutuamente excluyentes sobre el output validado — `opencode-go` usa exclusivamente `secrets.AI_REVIEW_API_KEY` y `PROVIDER: opencode-go`; `deepseek` usa exclusivamente `secrets.DEEPSEEK_API_KEY` y `PROVIDER: deepseek` — de modo que hay una sola invocación efectiva y ningún camino de fallback cruzado. `install` propaga el proveedor validado.

Garantías E1a intactas: sin publicación, `permissions: contents: read`, árbol medido en el head con revisor fijo fuera del workspace, artefactos result+manifest, `ATTEMPTS=2`, concurrencia sin cancelación.

TDD: suite de 8 a 10 casos. Ajustes de contrato: `test_secreto_solo_como_env_de_run` pasó a `test_secreto_por_ruta_sin_fallback_cruzado` (cada llave dos referencias: gate + ruta de su paso; sin `|| secrets.` en el archivo), `test_proveedor_fijo_opencode_go` pasó a `test_propaga_proveedor_validado_y_rutas_excluyentes`, y los conteos del árbol (`--prompt` 2→3, invocaciones ≥3→≥4) siguen a las dos rutas. Nuevos: `test_input_proveedor_es_choice_con_claves_de_providers` y `test_valida_proveedor_antes_de_instalar_o_correr`. Rojo contra el workflow de la base: `Ran 10 tests`, `FAILED (failures=4, errors=2)`, exit 1. Verde: `Ran 10 tests`, `OK`, exit 0. Mutaciones: ruta `deepseek` cruzada a `AI_REVIEW_API_KEY` → `FAILED (failures=1)` (`AssertionError: 3 != 2` en el conteo de la llave), y opción `gpt-4o` agregada al choice → `FAILED (failures=1)` (`Lists differ: ['deepseek', 'gpt-4o', 'opencode-go'] != ['deepseek', 'opencode-go']`); ambas revertidas → `Ran 10 tests`, `OK`, exit 0 y actionlint sin salida, exit 0.
