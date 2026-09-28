# Evidencia de E1a — workflow de medición sin publicación

Fecha: 2026-09-27. Todo local, sin push, sin PR, sin CI, sin corridas pagadas. La apertura del PR de E1a queda para el operador: este bloque crea el workflow, no lo dispara.

## Base y head

- Base: `7e2dfac` (`origin/main` verificada 2026-09-27).
- Commits: `test:` con la prueba focal (rojo primero contra el yml ausente), `feat:` con `.github/workflows/e1-measure.yml`, `docs:` con esta evidencia. SHAs en el LISTO.
- Enmienda r2 (2026-09-27): el árbol medido pasa a ser el head del PR y el revisor se fija fuera del workspace — bloqueante único de Claude sobre `9925f3b`; ver la sección final.

## Propósito

`e1-measure.yml` corre el revisor sobre un caso del corpus (un PR y un SHA dados, sin importar si el PR sigue abierto) y deja la salida capturada para que E1b la consolide. Es el prerrequisito de la tanda: sin E1a mergeado no se dispara ninguna corrida pagada.

## Garantía de no publicación (dos capas)

1. Por diseño: el workflow ejecuta `prepare`, `install` y `run`; no existe paso `publish` (ni el substring `publish` en el archivo, lo afirma el test).
2. Por alcance de token: `permissions: contents: read` a nivel workflow, sin `pull-requests: write`; el GITHUB_TOKEN ni siquiera podría comentar.

Además: el secreto `AI_REVIEW_API_KEY` se referencia una sola vez, como env `API_KEY` del paso `run`; no hay `echo` del secreto.

## Inputs (workflow_dispatch, todos requeridos)

- `pr`: número de PR del corpus.
- `head`: SHA de 40 hex de la punta revisada (vive en `refs/pull/<pr>/head`).
- `base`: SHA de 40 hex del estado de main al merge.
- `caso`: etiqueta del caso (p. ej. `pr2`), usada en el grupo de concurrencia y el nombre del artefacto.

Concurrencia: `e1-measure-<caso>` con `cancel-in-progress: false` (las mediciones no se cancelan entre sí).

## Fidelidad con una revisión normal

Misma secuencia que `action.yml` sin `gate` ni publicación: `prepare` → `install` → `run`, invocando `python3 review.py <subcomando>`; mismo cache key de instalación (`ai-review-install-<os>-py<ver>-claude-2.1.282-litellm-1.102.1`), mismo proveedor fijo (`opencode-go`, DeepSeek V4.1 Flash), mismo `ATTEMPTS=2` congelado en el corpus y mismo presupuesto por defecto. `prepare` recibe `GITHUB_EVENT_PATH` con el título y cuerpo reales del PR (vía `gh api`), igual que una revisión por `pull_request`. Diferencias deliberadas: sin `gate` (cada dispatch es una medición fresca), `HEAD_SHA`/`BASE_SHA` vienen de los inputs del corpus, y el checkout trae `fetch-depth: 0` más `git fetch origin pull/<pr>/head` si falta el objeto, con `git cat-file -e` de head y base antes de `prepare`.

## Artefactos y retención

`actions/upload-artifact@v4` con `result.json` y `manifest.json` del work dir (`$RUNNER_TEMP/ai-review`), nombre `e1-salida-<caso>-<head corto>`, retención por defecto de Actions. `GITHUB_STEP_SUMMARY` registra sólo caso, head, `subtype` y `turnos` (sin contenido de hallazgos; esos quedan en el artefacto). La copia a `evaluation/reviewer/salidas/` la hará E1b.

## Lo que NO hace

- No corre `gate` (medición fresca por dispatch).
- No corre `publish` y no tiene alcance para hacerlo: publica cero comentarios.
- No dispara `ai-review.yml` (éste sólo corre en `pull_request`).
- No lee el valor de ningún secreto fuera del env del paso `run`.

## TDD

Comando focalizado: `python3 -m unittest discover -s tests -p 'test_e1a_workflow.py' -v` (8 casos: dispatch con inputs, permisos de sólo lectura, sin substring `publish`, secreto único sin `echo`, proveedor fijo, árbol del head con revisor fuera del workspace, concurrencia sin cancelación, artefacto con result+manifest).

1. Rojo: sin el yml → `FAILED (errors=1)` (FileNotFoundError), exit 1.
2. Verde: creado el yml → `Ran 7 tests` en r1, `Ran 8 tests` desde la enmienda r2, `OK`, exit 0.
3. Mutaciones (demuestran que la prueba discrimina): agregar `pull-requests: write` → `FAILED (failures=1)`, exit 1; agregar un paso `publish` → `FAILED (failures=1)`, exit 1. Ambas revertidas → suite completa en verde, exit 0.

## Comandos y resultados sobre el head final

- `pre-commit run --all-files` → 8 hooks Passed, exit 0.
- `python3 -m unittest discover -s tests -p 'test_e1a_workflow.py' -v` → `Ran 8 tests`, `OK`, exit 0.
- `actionlint .github/workflows/e1-measure.yml` → sin salida, exit 0.
- No se corrió la batería completa local (prohibido).

## Enlace de CI

No aplica en esta ronda: push, PR y CI prohibidos por el encargo. El PR de E1a lo abre el operador.

## TANDA-PENDIENTE (intacta)

- 20 corridas del revisor vía `workflow_dispatch` de E1a (una por caso del corpus, con `pr`/`head`/`base`/`caso`), DeepSeek V4.1 Flash, secreto del repo nunca leído. Requiere E1a mergeado + orden de David.
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
