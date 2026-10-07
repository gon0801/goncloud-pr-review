# Evidencia IN: instalador del conjunto coordinado (T11)

Fecha: 2026-10-07. Rama: `rc/in-instalador`. Base verificada: `438ba41`
(origin/main al abrir IN, con UW mergeado); `git merge-base --is-ancestor
179e6a3719b8ff611c820e1575f15d40e36e1ddb HEAD` → `rc=0`. Paso 4 de "Prepara
la ejecución futura": las funciones nombradas de T11 (`scripts/install.sh`,
documentación de instalación en `README.md`) se revisaron antes de editar; el
instalador previo instalaba solo `ai-review.yml` por la API de contents.

Tarea: T11 del plan `docs/superpowers/plans/2026-10-05-revisor-correcciones.md`.
Diseño: "Alcance y decisiones existentes", "Módulos y límites", "Tipos y
firmas propuestas" y "Instalación, activación y retorno".

## Qué cambió

- `scripts/install.sh` (invocación por repositorios conservada):
  - Modo actual: instala `ai-review.yml` como antes; si se pasa `ACTION_SHA`
    fija `uses:` a ese SHA en vez de `@main`; la primera instalación ya no
    muere leyendo `heads/chore/ai-review` inexistente (cae a la punta del
    default para crear la rama). Sobre un árbol que ya contiene el conjunto
    coordinado hace el **retorno**: un commit atómico repone `ai-review.yml`
    (fijado a `ACTION_SHA`) y retira `ai-review-publish.yml` y
    `ai-review-worker.yml`, con fast-forward y reintento sobre la punta nueva
    sin forzar; si no puede leer el árbol, sale alto sin publicar nada.
  - Modo coordinado (`--coordinado`, `ACTION_SHA` obligatorio): transforma las
    plantillas conservando el checkout del consumidor y añadiendo el del CLI
    confiable (`repository: gon0801/goncloud-pr-review`, `ref: ACTION_SHA`,
    `path: ai-review-code`), reescribe las invocaciones a
    `$GITHUB_WORKSPACE/ai-review-code/review.py`, y publica el conjunto
    (coordinador + worker, retirada de `ai-review.yml`) en un único commit
    atómico por la API Git de blobs/árboles/commits con fast-forward y
    reintento sin forzar. `base_tree` y la punta del reintento fallan alto si
    la lectura no está disponible. Los secrets que el conjunto lee
    (`API_KEY`, `FALLBACK_API_KEY`) se nombran en el commit y en el cuerpo del
    PR.
- `tests/test_install.py` (nuevo): doble de API `gh` con estado JSON
  (referencias, commits, árboles anidados con `recursive=1`, blobs, PRs,
  inyección de fallos transitorios y carrera de rama). Seis pruebas:
  `AtomicInstall.test_single_commit_replaces_writer_set`,
  `AtomicInstall.test_retry_preserves_unrelated_changes`,
  `CompatibleRollback.test_el_retiro_atomico_restaura_el_escritor_anterior`,
  `CompatibleRollback.test_un_fallo_transitorio_no_publica_arbol_sin_base`,
  `CompatibleRollback.test_el_retorno_sale_alto_si_no_puede_leer_el_arbol` y
  `CompatibleRollback.test_updates_expanded_schema3_in_current_mode`.
- `docs/reviewer-rollout.md` (nuevo): corte (detener admisión, drenar,
  verificar checkpoint, activar coordinador con el piloto fijado al SHA
  candidato) y retorno (sin volver a `179e6a3`, conserva schema 3 y
  hallazgos, secrets de cada escritor nombrados).
- `README.md`: sección de instalación con el pin por `ACTION_SHA` y el modo
  coordinado.
- Desactivado por defecto: el modo actual sin variables nuevas se comporta
  igual que en la base; el modo coordinado requiere `--coordinado` +
  `ACTION_SHA`; ninguna prueba llama al proveedor real ni instala en repos
  reales (todo con el doble de API).

## Reproductores (rojos antes, verdes después)

Comando focalizado: `python3 -m unittest discover -s tests -p 'test_install.py' -v`
→ rojo antes: `FAILED (failures=2)` (el modo coordinado no existía; la
semilla del rollback ya era verde porque el escritor compatible conservaba el
estado: la prueba fija esa garantía y su mutante demuestra que discrimina);
verde: `Ran 6 tests in 1.888s` + `OK`.

- `AtomicInstall.test_single_commit_replaces_writer_set`: un solo commit
  sobre la punta instala el conjunto y retira `ai-review.yml`, con los
  cambios ajenos intactos; los workflows instalados llevan el checkout doble
  (consumidor + CLI central en `ai-review-code`) y el pin a `ACTION_SHA`;
  el cuerpo del PR y el commit nombran `API_KEY`/`FALLBACK_API_KEY` y no
  `AI_REVIEW_API_KEY`.
- `AtomicInstall.test_retry_preserves_unrelated_changes`: con una carrera
  inyectada, el instalador reintenta sobre la punta nueva, el cambio ajeno
  sobrevive y la referencia nunca se fuerza.
- `CompatibleRollback.test_el_retiro_atomico_restaura_el_escritor_anterior`:
  el retorno repone el escritor y retira el conjunto en un commit atómico sin
  forzar; discrimina la recursión del listado del árbol (el doble modela
  árboles anidados como la API real).
- `CompatibleRollback.test_un_fallo_transitorio_no_publica_arbol_sin_base` y
  `test_el_retorno_sale_alto_si_no_puede_leer_el_arbol`: fallos transitorios
  salen altos sin publicar árboles que borrarían el contenido del consumidor.
- `CompatibleRollback.test_updates_expanded_schema3_in_current_mode`: el
  escritor de retorno actualiza una memoria ampliada schema 3 conservando el
  descarte (F2), la solicitud pendiente (id 2), el cursor, la generación
  (4→5) y los hallazgos (F1 y F2).

## Verificación del bloque

- Focal: `Ran 6 tests in 1.888s` + `OK`.
- Batería: `476 passed, 146 subtests passed in 52.05s`.
- Candados: `pre-commit run --all-files` → 8/8 Passed;
  `python3 scripts/run_test_shard.py --verify-partition 2` → `total: 476`,
  `verify: OK`.

## Ronda de revisión (swarm + paneles + comment-sicko, delta por SHA)

- Swarm poteto-agent (zai-coding-plan/glm-5.3): verificación con mutantes en
  copia temporal (conjunto partido, force en refs, pérdida de
  `pending_requests`) — todos rojos y revertidos.
- Paneles adversarios (diversidad reducida: solo familia GLM): panel-glm53,
  panel-glm52, panel-glm47, panel-glm5turbo. Convergieron con repros
  ejecutados y se corrigieron en la misma ronda:
  1. La transformación dejaba al worker sin checkout del consumidor (la raíz
     sin repo git y `origin` apuntando al repo central): el conjunto
     instalado no podía completar ninguna revisión. Arreglo: checkout doble
     (consumidor + CLI central en `ai-review-code`). Prueba: aserciones de
     ambos checkouts y de los paths reescritos en
     `test_single_commit_replaces_writer_set`.
  2. El retorno documentado no retiraba el conjunto coordinado (quedaban
     escritores múltiples). Arreglo: retiro atómico en el modo actual.
     Prueba: `test_el_retiro_atomico_restaura_el_escritor_anterior`.
  3. Fallos transitorios podían publicar un árbol sin base que borraba el
     contenido del consumidor (verificado contra la API real por el panel:
     `base_tree` nulo equivale a omitirlo). Arreglo: guardas de `arbol_base`
     y de la punta del reintento en ambos modos. Pruebas:
     `test_un_fallo_transitorio_no_publica_arbol_sin_base`,
     `test_el_retorno_sale_alto_si_no_puede_leer_el_arbol`.
  4. La detección del conjunto usaba `git/trees` sin `?recursive=1`: contra
     la API real el retiro atómico era código muerto y el doble lo enmascaraba
     con árboles planos. Arreglo: `?recursive=1` con salida alta y el doble
     modelando anidamiento. Prueba: la del retiro discrimina la recursión
     (mutante sin `?recursive=1` → roja).
  5. El cuerpo del PR del conjunto nombraba `AI_REVIEW_API_KEY`, que el
     conjunto no lee. Arreglo: `API_KEY`/`FALLBACK_API_KEY` nombrados y
     aserciones que fijan los nombres.
- comment-sicko (zai-coding-plan/glm-5.3): 2 MUST-KILL (la atomicidad del
  retorno prometida sin código; los secrets nombrados que el conjunto no lee)
  cerrados por los arreglos de arriba; 13 KILL de docstrings nominales,
  mensajes que restituían el valor esperado y prosa decorativa en datos de
  prueba, aplicados.

## Mutantes (aplicados, rojos, revertidos)

- Conjunto partido en dos commits → `test_single_commit_replaces_writer_set`
  roja (padres != punta previa; total de commits != 2).
- `-F force=true` en el PATCH → `test_retry_preserves_unrelated_changes`
  roja (el doble rechaza la fuerza; el instalador agota reintentos).
- Escritor de retorno sin `pending_requests` →
  `test_updates_expanded_schema3_in_current_mode` roja (0 != 1).
- Checkout único del central (sin el del consumidor) →
  `test_single_commit_replaces_writer_set` roja.
- Retiro inalcanzable (umbral `-ge 99`) → la misma prueba roja (endpoint de
  contents no simulado).
- Guarda de `arbol_base` fuera → `test_un_fallo_transitorio…` roja.
- `?recursive=1` fuera → `test_el_retiro_atomico…` roja.
- Cuerpo del PR con `AI_REVIEW_API_KEY` → roja (assertNotIn).
- Cuerpo sin `FALLBACK_API_KEY` → roja (assertIn).
- Salida alta del fallo de árbol degradada a silencio →
  `test_el_retorno_sale_alto_si_no_puede_leer_el_arbol` roja (razón en stderr).
- Swarm: los tres primeros, mismos rojos con `cmp` de reversión idéntica.

## Cierre T11 y pendientes

- El instalador queda verificable con dobles de API; instalar en repositorios
  reales pertenece a T16. El retorno no usa `179e6a3`.
- Fila: la baja con `sha: null` de un path ausente no está ejercitada contra
  la API real (los sembrados siempre tienen el escritor viejo); probarla en el
  piloto de T16.
- Fila: `git/trees?recursive=1` puede responder `truncated: true` en árboles
  enormes; el instalador no lo revisa (irrelevante en consumidores normales).
- Fila: detener la admisión hoy es por congelación de eventos; la bandera de
  desactivación de las plantillas nuevas sigue pendiente (AI_REVIEW_DISABLED,
  fila de T11).
- Fila: el modo actual conserva el reset con fuerza de la rama propia solo en
  la actualización trivial de una rama ya existente (preexistente de la base,
  fuera del delta; el retorno usa la vía atómica sin fuerza).
- Filas previas de UW y bloques anteriores siguen en el ledger.
