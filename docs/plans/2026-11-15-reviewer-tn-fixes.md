# TN1–TN7: ejecución continua de Devin, revisión final de Codex

Devin: implementa este plan completo de corrido. Gon no participa en decisiones rutinarias; Codex revisará el resultado antes del merge. Estado: **diseñado, pendiente de implementación**. Este plan y [el diseño](../reviewer-tn-fixes-design.md) son la autorización y el contrato; no dependen de otro archivo de aprobaciones. El informe fuente es [la auditoría consolidada](../reviews/2026-11-15-thermo-post-deploy.md).

Repositorio: `gon0801/goncloud-pr-review`; checkout de referencia `/Users/dn/dev/goncloud-pr-review`; rama base `main`; SHA auditado `d1858c0c1dd67b5a726f9ec09318e88a778a6e08`. En otra máquina usa el clon del mismo repositorio. Destinos de despliegue: fuera de alcance. Rama única: `fix/reviewer-tn1-tn7`; un PR borrador; un commit por C1–C7, más documentación inicial y correcciones finales justificadas. No aplastar antes de revisar.

## Roles y autorización

| Operación | Responsable y decisión |
| --- | --- |
| Implementar C1–C7, pruebas y correcciones derivadas | Devin: aprobado, sin pausas entre entregas |
| Crear worktree, rama, commits, push normal y PR borrador de esta rama | Devin: aprobado durante la implementación |
| Resolver errores de tests, imports y conflictos de esta rama | Devin: aprobado dentro del contrato |
| Revisar código, evidencia y contratos sobre el SHA final | Codex: obligatorio antes del merge; esta revisión todavía no existe |
| Merge, auto-merge, deploy, actualizar pins o consumidores | Devin: negado; esta entrega termina en revisión |
| Cambiar permisos OAuth, credenciales, protecciones, esquema o semántica de identidad | Negado en este plan |

Prohibido: pedir confirmación rutinaria al humano, omitir checks para avanzar, presentar un bloqueo como terminado, incorporar cambios ajenos, publicar secretos o tocar producción. **No limpies: nada de `rm -rf` ni borrados recursivos, ni bajo `/tmp`; usa `mktemp -d` y deja lo que crees.** No borres historial ni fixtures para obtener verde.

## Arranque: las dos primeras acciones

1. En el checkout fuente registra identidad y estado sin modificarlo:
   ```sh
   REVIEWER_TN_SOURCE=$(git rev-parse --show-toplevel)
   git rev-parse --show-toplevel
   git rev-parse HEAD
   git status --short
   git symbolic-ref refs/remotes/origin/HEAD
   ```
   Lee `AGENTS.md`, diseño, plan e informe. Retén `REVIEWER_TN_SOURCE` para copiar esos documentos después de crear el worktree en el paso 2. Los archivos de auditoría que pertenecen a esta entrega son únicamente `docs/reviews/2026-11-15-thermo-post-deploy.md`, `docs/reviews/thermo-post-deploy-repros.py` y `docs/reviews/thermo-post-deploy-repros.json`, además del diseño y este plan. No copiar el directorio entero.
2. Actualiza referencias y crea el worktree aislado desde `origin/main`:
   ```sh
   git fetch origin main
   REVIEWER_TN_BASE=origin/main
   REVIEWER_TN_WORK=$(mktemp -d "${TMPDIR:-/tmp}/reviewer-tn-work.XXXXXX")
   git worktree add -b fix/reviewer-tn1-tn7 "$REVIEWER_TN_WORK/checkout" "$REVIEWER_TN_BASE"
   cd "$REVIEWER_TN_WORK/checkout"
   ```
   Si fetch falla, resuelve primero la fila de arranque sin red antes de crear el worktree. Registra la ruta; todos los comandos siguientes parten de esa raíz. Copia ahora el paquete explícito desde `REVIEWER_TN_SOURCE`, únicamente donde falte; si un destino ya existe, compara y conserva la versión vigente, sin sobrescribir cambios:
   ```sh
   mkdir -p docs/plans docs/reviews docs/evidence/reviewer
   for REVIEWER_TN_DOC in docs/reviewer-tn-fixes-design.md docs/plans/2026-11-15-reviewer-tn-fixes.md docs/reviews/2026-11-15-thermo-post-deploy.md docs/reviews/thermo-post-deploy-repros.py docs/reviews/thermo-post-deploy-repros.json; do
     if [ ! -e "$REVIEWER_TN_DOC" ]; then cp "$REVIEWER_TN_SOURCE/$REVIEWER_TN_DOC" "$REVIEWER_TN_DOC"; fi
   done
   ```
   La evidencia del diseño es opcional: copiar también `docs/evidence/reviewer/tn-fixes-design.md` cuando esté disponible y falte en destino. Registra base y ruta en la evidencia de ejecución y haz el commit documental inicial con las rutas explícitas antes de C1. Comprueba `git merge-base --is-ancestor d1858c0c1dd67b5a726f9ec09318e88a778a6e08 HEAD` y compara `git diff d1858c0c1dd67b5a726f9ec09318e88a778a6e08..HEAD -- 'review*.py' action.yml .github templates scripts tests`. Base sin cambios relevantes: reutiliza las 841 pruebas de la auditoría; no necesitas otra batería inicial. Rama existente o base divergente: sigue la tabla de atascos.

## Reglas de ejecución

1. El contrato vigente es este plan más el diseño; el plan de simplificación anterior es histórico. Ante diferencias, preserva invariantes y registra la decisión, sin reabrir fases completadas de aquel plan.
2. Orden **C1 → C2 → C3 → C4 → C5 → C6 → C7 → integración**. C1–C4 son técnicamente independientes; C6 sigue C5 para reutilizar su clasificación de capacidad; C7 migra consumidores sobre los contratos definitivos. Una sola persona/agente modifica la rama a la vez.
3. Por unidad: reproduce el defecto relevante, implementa, ejecuta los tests indicados, revisa el diff y registra evidencia. Continúa a la siguiente unidad sin esperar a Codex. Un test rojo bloquea esa unidad; corrígelo, no lo ignores. Puedes avanzar en una unidad independiente cuando otra tenga un bloqueo externo o de contrato documentado.
4. Usa Python >=3.10, `pre-commit`, gitleaks, actionlint y ruff. Verifica con `python3 --version`, `command -v pre-commit`, `command -v actionlint`, `command -v ruff`, `gh --version`; `gh api --help` debe incluir `--paginate` y `--slurp`. Instalación de herramientas de desarrollo por medios existentes permitida; no añadir dependencias productivas.
5. Evidencia única en `docs/evidence/reviewer/tn-fixes.md`: base completa, C1–C7, commit de cada unidad, comandos, resultados reales, cambios deliberados y pendientes. Actualízala al completar cada unidad. No inventes conteos ni llames test verde a una sonda que demuestra un fallo. Los cuatro repros de la auditoría afirman el comportamiento defectuoso: conviértelos en pruebas del comportamiento corregido, no los uses como gate verde final.
6. No hagas `git add .`: añade rutas explícitas de la entrega. Preserva `.claude-code-harness.config.yaml`, los otros archivos de `docs/reviews/`, `mktree-test/`, `mt2/` y cualquier cambio ajeno. Los comentarios, fixtures y corpus históricos no se reescriben.
7. Devin informa en su conversación de trabajo al terminar cada unidad y al encontrar un bloqueo real; durante una unidad larga, como máximo cada 30 minutos. Sin mensajes externos ni bots nuevos. Al reanudar, lee evidencia, rama, estado y `git log`; no repitas unidades ya acreditadas sin un cambio que invalide su evidencia.

## Entregas y archivos

Todos los C permiten actualizar `docs/evidence/reviewer/tn-fixes.md` y sus pruebas relevantes. El commit documental inicial incluye solo el paquete de cinco archivos nombrado en Arranque y, si está disponible, `docs/evidence/reviewer/tn-fixes-design.md`. El cierre permite también `docs/evidence/reviewer/tn-fixes-pr.md` y las correcciones pertinentes a este diseño/plan. Ninguno permite cambios fuera del contrato general.

| Unidad | Implementación permitida | No toca |
| --- | --- | --- |
| C1 / TN4 | `action.yml`; `tests/test_review.py` o prueba dedicada de cableado | Defaults, prompt, providers, protocolos del worker |
| C2 / TN6 | `review_domain.py`; pruebas de frontera e integración directa/coordinada | Reglas de matching, persistencia nueva, descarte autorizado |
| C3 / TN5 | `review_domain.py`, `review_document.py`, `review_sticky.py`; pruebas de codecs/cobertura | Formato v1/v2, interpretación de rutas, consumidor externo |
| C4 / TN7 | `review_coordinator.py`, `review_github.py` solo si E/S lo requiere; tests del coordinador | Almacenes nuevos, schema, permisos, políticas de confianza |
| C5 / TN2 | `review.py`, `review_document.py`; consumidores afectados en coordinador y tests | Motor de aceptación, presupuestos, formato de almacenamiento |
| C6 / TN3 | `review_document.py`; pruebas de capacidad y render | Orden de reducción, contenido visible salvo fixes declarados |
| C7 / TN1 | `review.py`, imports/parches en tests/scripts, seed de `.github/workflows/e1-measure.yml`, documentación de API | Fixtures congelados, corpus, comportamiento y exports de E1 |

### C1 — Identidad consistente entre pasos

Promesa: la opción `anchors` se aplica durante toda la revisión — ruta: action `prepare → run → publish`.

Aplica el contrato §C1 del diseño. Verifica entornos independientes cargados del YAML real para input omitido, `current`, `anchors` e inválido; prueba política/digest y resultado final, no la existencia de una cadena. Conserva el default y la configuración coordinada.

Gate: `PYTHONPATH=tests:. python3 -m unittest tests.test_review.CableadoIdentidad tests.test_review.Workflows` más el módulo nuevo si se añade. Cierra cuando los tres pasos coincidan y el recorrido de anclas conserve IDs/descartes sin fusionar hallazgos distintos. Commit: `fix(action): mantener identidad entre pasos`.

### C2 — Frontera tipada del modelo

Promesa: un metadato malformado no derriba la publicación ni borra hallazgos previos — ruta: entrada del modelo → aceptación → publicación.

Aplica §C2. Cubre `cause_hint` y los hints que realmente se transportan a anclas; ausencia, null, cadena vacía/texto, número, booleano, lista y objeto. Rechaza la observación inválida, conserva las válidas del reporte mixto y deja cobertura parcial cuando corresponde. Mantén la memoria anterior y el rechazo visible, sin convertir valores inválidos a texto ni capturar genéricamente el fallo de codificación.

Gate: `PYTHONPATH=tests:. python3 -m unittest tests.test_review_domain.ObservacionDeEntrada tests.test_review_domain.ReportBoundary tests.test_review.CableadoIdentidad` más las nuevas regresiones de publicación. Commit: `fix(domain): rechazar metadatos incompatibles antes de aceptar`.

### C3 — JSON oculto seguro y reversible

Promesa: las rutas no pueden salir del comentario oculto — ruta: cobertura, memoria y vista del sticky publicado.

Aplica §C3. Prueba `-->`, `--!>`, `<!--`, Unicode y barras invertidas en los tres bloques. Parser HTML: ningún contenido del payload aparece fuera del comentario. Relectura: conserva exactamente rutas/valores; hashes se calculan sobre la salida final. Cubre escritura/lectura repetida y límites después del escape.

Gate: `PYTHONPATH=tests:. python3 -m unittest tests.test_review_document tests.test_cobertura_acumulada tests.test_review_domain.Schema3Compatibility tests.test_review_domain.CheckpointCapacity`. Commit: `fix(codec): unificar escape de comentarios ocultos`.

### C4 — Recuperación sin falsa ausencia

Promesa: un worker existente no se relanza porque quedó fuera de la primera página — ruta: evento de recuperación del coordinador.

Aplica §C4. Casos: páginas 2 y posteriores a 1.000 runs, varios PR, callback viejo con run más nuevo, intento distinto, run ajeno, fallo a mitad de paginación, JSON inválido, timeout y listado completo realmente vacío. Mantén confianza por repo/workflow/ref/SHA/attempt y rechazo de artifacts ambiguos. Una búsqueda desconocida conserva solicitudes y no genera `sin_corrida`.

Gate: `PYTHONPATH=tests:. python3 -m unittest tests.test_review_coordinator tests.test_coordinador_convergente tests.test_cancelar_worker_superado`. Todas las llamadas a GitHub de las pruebas son simuladas. Commit: `fix(coordinator): distinguir historial completo de búsqueda incierta`.

### C5 — Aceptación directa tipada

Promesa: el modo directo conserva memoria y avisos ante desborde con menos conversiones internas — ruta: `cmd_publish`.

Aplica §C5. Migra en la misma entrega los tests que consumen el dict antiguo. Elimina encode/readback del intercambio interno de aceptación; conserva la relectura obligatoria en la frontera de publicación. Prueba legado/v2/v3, estado que solo cabe compactado, desborde inicial, desborde tras redacción, cuerpo demasiado grande, sticky ausente/inválido/futuro y confirmación incierta. Compara estado, cabecera, summary, salida `reviewed` y código de salida.

Gate: `PYTHONPATH=tests:. python3 -m unittest tests.test_review_simplificacion tests.test_review_document tests.test_review.PersistenciaSinPerdida tests.test_review.DesbordeSinMemoriaPrevia tests.test_review.ConservacionAlDesbordar tests.test_coordinador_convergente`. Commit: `refactor(publish): transportar aceptación tipada hasta el documento`.

### C6 — Una reducción de prosa con orden explícito

Promesa: el comentario conserva los mismos avisos y prioridades al quedarse sin espacio — ruta: preparación de publicación con presupuesto limitado.

Aplica §C6. Guarda antes del refactor salidas caracterizadas de la versión ya corregida en C5 y compara cuerpo/summary después. Cubre Unicode multibyte, tres avisos, tres explicaciones, histórico, fallo, libro y ausencia de revisión visible. No dupliques el algoritmo antiguo en tests. Elimina las ocho ramas inalcanzables y evita callbacks/reflexión que escondan las mismas decisiones.

Gate: `PYTHONPATH=tests:. python3 -m unittest tests.test_review_document tests.test_capacidad_pr20 tests.test_review.ConservacionAlDesbordar`. Cuerpos y summaries idénticos a C5 para esta transformación; estado y solicitudes nunca se recortan. Commit: `refactor(document): concentrar reducción de prosa`.

### C7 — Migrar consumidores y retirar la fachada

Promesa: sin promesa observable.

Aplica §C7. Inventaría referencias y parches, incluidos scripts y E1. Migra al dueño canónico, usa presupuesto explícito en pruebas y retira `__all__`, wrappers y imports mantenidos solo por compatibilidad. Conserva las funciones que sí son del adaptador directo. No sustituirlo por aliases, `__getattr__` ni otra fachada de tests.

Gate: `PYTHONPATH=tests:. python3 -m unittest tests.test_review tests.test_review_context tests.test_review_domain tests.test_e1a_workflow tests.test_review_simplificacion`; inventario posterior sin consumidores del API retirado. E1 debe producir el mismo seed, IDs, modo y diff. Commit: `refactor(api): retirar fachada tras migrar consumidores`.

## Integración, entrega y revisión

Ejecuta una vez sobre el resultado integrado; repite después solo lo invalidado por cambios o fallos:

```sh
bash scripts/check.sh
ruff check --select F review*.py scripts/*.py
git diff --check "$REVIEWER_TN_BASE"...HEAD
git diff --stat "$REVIEWER_TN_BASE"...HEAD
git status --short
git rev-parse HEAD
```

`check.sh` debe terminar en `check: OK`, con gitleaks, conflictos, actionlint y toda la batería. Las 841 pruebas son la base, no una cuota: documenta añadidas, migradas y eliminadas con motivo. Ningún hallazgo se cierra únicamente por un conteo verde. El parser externo congelado debe seguir leyendo el cuerpo v3 producido. Actualiza documentación afectada y la evidencia; termina con árbol limpio dentro del worktree.

Prepara `docs/evidence/reviewer/tn-fixes-pr.md` como cuerpo base del PR: problema/resultado, C1–C7 y commits, pruebas, casos adversariales, riesgos, enlace a evidencia y **“Pendiente revisión final de Codex; no mergear”**. Después de obtener el SHA final, adjúntalo al cuerpo remoto del PR usando una copia temporal de este cuerpo base y `gh pr edit --body-file`, sin otro commit autorreferente. El archivo de evidencia registra el SHA de código comprobado y los commits de unidades ya existentes. No intentes incluir en un commit su propio SHA: el SHA final se registra después en el cuerpo remoto del PR y en el mensaje de entrega. Cuando el commit de evidencia sea solo documental, muestra el diff respecto al código comprobado para reutilizar las pruebas; CI correrá sobre la punta del PR. Tras commit de la evidencia final:

```sh
git push -u origin fix/reviewer-tn1-tn7
gh pr create --draft --base main --head fix/reviewer-tn1-tn7 --title 'fix(review): cerrar TN1–TN7' --body-file docs/evidence/reviewer/tn-fixes-pr.md
gh pr view fix/reviewer-tn1-tn7 --json url,headRefOid,isDraft,baseRefName
gh pr checks fix/reviewer-tn1-tn7
```

Un PR existente se reutiliza y su cuerpo se actualiza con `gh pr edit fix/reviewer-tn1-tn7 --body-file docs/evidence/reviewer/tn-fixes-pr.md`. Corrige fallos de CI dentro de alcance y vuelve a publicar; no habilites auto-merge. Reporta URL, SHA completo y el estado real de cada check. Los checks pendientes aún no son evidencia verde.

**Cola:** 1) Devin completa C1–C7 + checks; 2) Codex revisa el conjunto y el impacto en flujos vecinos; 3) Devin corrige los hallazgos de esa revisión en la misma rama; 4) Codex revisa los cambios y emite veredicto sobre el SHA final. No hay merge ni deploy dentro de este runbook. Todo cambio posterior de código invalida la aprobación anterior; un cambio solo documental también se presenta con su diff para ratificación, nunca se asume aprobación transferida.

La revisión de Codex cubrirá explícitamente los siete criterios del diseño, contratos entre pasos, entradas hostiles, memoria/capacidad, autenticación, recuperación, rutas directa/coordinada, secretos, compatibilidad externa y simplificación efectiva. No será solo revisar el diff final ni repetir tests. Informe previsto: `docs/reviews/2026-11-15-tn-fixes-final.md`, con SHA y veredicto. Ante un fallo queda `REQUEST_CHANGES`; no se fusiona por “tests verdes”.

## Cuando algo se atora

| Situación | Acción autónoma |
| --- | --- |
| Falta un documento de este paquete | Recupera la copia exacta del checkout fuente o adjunto; no inventes el contrato. Continúa solo trabajo cuyo contrato esté disponible y registra lo faltante. |
| Fetch inicial falla / arranque sin red | Usa `REVIEWER_TN_BASE=origin/main` si `git rev-parse --verify origin/main` resuelve; registra frescura remota `unknown`. Si esa referencia falta, usa `REVIEWER_TN_BASE=d1858c0c1dd67b5a726f9ec09318e88a778a6e08` solo si `git cat-file -e "$REVIEWER_TN_BASE^{commit}"` pasa. Sin ninguna base verificable, arranque bloqueado: no crear una historia inventada. |
| Rama/worktree ya existe | Inspecciona `git worktree list` y `git log fix/reviewer-tn1-tn7`; reanuda la entrega identificada. No sobreescribas una rama ajena ni crees PR duplicado. |
| `origin/main` avanzó | Compara contra el SHA auditado, integra cambios pertinentes y registra base real. Revalida las áreas afectadas; no reviertas avances ajenos. |
| SHA auditado no es ancestro | Verifica repositorio e historia; conserva el trabajo y reporta divergencia. No ejecutar cherry-picks o resets a ciegas. |
| Falta herramienta | Instala dependencia de desarrollo con el método normal disponible; sin sustituir checks ni cambiar dependencias productivas. Si es imposible, deja ese check pendiente, no aprobado. |
| Red, cuota API, autenticación o push rechazado por `workflow` | Completa todos los commits y pruebas locales. Usa únicamente una credencial ya autorizada disponible; no amplíes permisos ni retires el workflow para pasar. Registra error saneado, rama y SHAs, continúa lo independiente y entrega `ENTREGA_REMOTA_BLOQUEADA` si persiste. |
| Test o CI rojo | Reproduce, corrige dentro del alcance y repite el gate afectado; no borrar pruebas ni saltar hooks. Fallo externo queda pendiente documentado. |
| Nueva regresión causada por estos cambios | Corrígela y añade la comprobación que la detecta; revisa también sus consumidores. |
| Nuevo defecto preexistente ajeno a TN1–TN7 | Registra impacto y reproducción sin ampliar silenciosamente el proyecto; si impide un criterio de cierre, queda bloqueador explícito para Codex. |
| El contrato requiere cambiar schema, seguridad o semántica | No improvisar migración: documenta caso mínimo y alternativa, conserva cambios revisables y sigue las unidades independientes. No declarar C1–C7 completo. |
| Sesión interrumpida | Recupera ruta registrada, `git status`, `git log` y evidencia; continúa desde el último gate acreditado, sin reset ni limpieza. |
| PR/checks inaccesibles | Guarda evidencia local y estado remoto `unknown`; una comprobación local no se presenta como CI remoto. |
| Codex pide cambios | Corrige en la misma rama, registra nuevos SHA y pruebas, solicita nueva revisión del resultado; no mergear. |

## Inventario, presupuesto y cierre

Siete hallazgos, siete unidades, una rama y un PR. Sin llamadas pagadas al modelo ni escrituras de prueba en repos consumidores. No se prepara cuota de proveedor; las reproducciones usan datos locales y GitHub simulado. El tiempo/costo real se reporta si está disponible, nunca se estima como gasto observado. No se fija una cuota de líneas borradas. Sin horario de merge porque no hay merge autorizado en esta fase.

**Contrato final de Devin:** última línea `LISTO_PARA_REVISION_CODEX <sha-completo> <url-del-pr>` únicamente si C1–C7 y los checks requeridos están completos; de lo contrario `INCOMPLETO <unidades-pendientes> <motivo>` o `ENTREGA_REMOTA_BLOQUEADA <sha-completo> <motivo>`. No usar “terminado al 100 %”. El código permanece sin fusionar hasta el veredicto posterior de Codex.

## Clases de comando

| Clase | Uso autorizado |
| --- | --- |
| `gh` | Leer PR/checks y crear/actualizar el PR borrador de esta rama; sin merge, secrets, permisos ni dispatch de pruebas reales |
| `red externa` | Fetch/push de esta rama, herramientas de desarrollo y CI normal del PR; no despliegue ni llamadas pagadas al modelo |

## Adaptación del runbook y estado del diseño

Este repositorio no usa el tablero, launcher, Telegram ni merge-kit de `goncloud-openclaw`: no se introducen para ejecutar estos fixes. El progreso es la evidencia versionada y la conversación de Devin; el loop y la prohibición de merge están escritos aquí. La petición actual es **solo diseñar**: Codex no crea la rama, PR o procesos de ejecución ahora. Devin realiza esas operaciones cuando ejecute el encargo. El usuario puede iniciar el trabajo entregando este plan y el diseño juntos; no hay aprobaciones intermedias.
