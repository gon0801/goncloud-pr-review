# Ejecución de TN1–TN7

Base `d1858c0c1dd67b5a726f9ec09318e88a778a6e08` (`origin/main` al arrancar).
Worktree `/var/folders/hp/sc_3c6qs7nvdswl_x3w5sdbr0000gn/T/reviewer-tn-work.0vxcxp/checkout`.
Rama `fix/reviewer-tn1-tn7`. El checkout fuente permanece intacto.

La comparación `git diff d1858c0c1dd67b5a726f9ec09318e88a778a6e08..origin/main -- 'review*.py' action.yml .github templates scripts tests` no produjo diferencias. La auditoría de esa base registra 841 pruebas existentes. Python 3.14.7, `pre-commit`, `gitleaks`, `actionlint`, `ruff` y `gh` están disponibles. `gh api --help` admite `--paginate --slurp`.

## Entregas

| Unidad | Commit | Comprobación | Resultado |
| --- | --- | --- | --- |
| C1 | `b78d17cd9e5c75d46e23c106e0863d3eb673c94e` | `PYTHONPATH=tests:. python3 -m unittest tests.test_review.CableadoIdentidad tests.test_review.Workflows`; `actionlint -color` | 74 tests y actionlint pasan. La prueba nueva falló antes del cambio porque `prepare` y `publish` elegían `titles` mientras `run` elegía `anchors`. |
| C2 | `77dbd8ae4098671651b758c6306db5337fa02dba` | `PYTHONPATH=tests:. python3 -m unittest tests.test_review_domain.ObservacionDeEntrada tests.test_review_domain.ReportBoundary tests.test_review.CableadoIdentidad`; `ruff check --select F review_domain.py tests/test_review_domain.py` | 30 tests y ruff pasan. Las 17 variantes inválidas de los tests nuevos fallaron antes de validar; un reporte mixto publica solo el hallazgo válido con cobertura parcial. |
| C3 | `1a0745626eae2fb64ceb8fbcc90d1dbb9f9aa551` | `PYTHONPATH=tests:. python3 -m unittest tests.test_review_document tests.test_cobertura_acumulada tests.test_review_domain.Schema3Compatibility tests.test_review_domain.CheckpointCapacity`; `ruff check --select F review_domain.py review_document.py review_sticky.py tests/test_review_document.py` | 102 tests y ruff pasan. La reproducción anterior mostraba un `<h1>` salido del libro; el caso nuevo falló con `--!>` antes del cambio y ahora acredita tres ciclos reversibles en estado y libro. La vista comparte el mismo escape. |
| C4 | `f360c969d3b78b95d78b01e28f37d4d5e30d42b8` | `PYTHONPATH=tests:. python3 -m unittest tests.test_review_coordinator tests.test_coordinador_convergente tests.test_cancelar_worker_superado`; `ruff check --select F review_coordinator.py tests/test_review_coordinator.py tests/test_coordinador_convergente.py tests/test_cancelar_worker_superado.py` | 69 tests y ruff pasan. La reproducción de base relanzaba el worker de la página dos. Ahora la simulación de 1.101 runs no genera `sin_corrida`; un timeout, JSON inválido, total variable o historia parcial mantienen la solicitud pendiente. El callback viejo no cosecha el run nuevo en curso. Un run sin título atribuible se difiere como Unknown en lugar de declararse rechazo. |
| C5 | `25372d08bf615fc241b10447bbdf6070d8a45a96` | `PYTHONPATH=tests:. python3 -m unittest tests.test_review_simplificacion tests.test_review_document tests.test_review.PersistenciaSinPerdida tests.test_review.DesbordeSinMemoriaPrevia tests.test_review.ConservacionAlDesbordar tests.test_coordinador_convergente` (101 tests); `tests.test_review.Workflows` más ruta de comandos, capacidad y rollback (59 tests); `tests.test_review_domain` (126 tests); `ruff check --select F review.py review_document.py` | Todos pasan. `DirectAccepted` conserva `Snapshot`, IDs nuevos y `model_ok`; `Keep` evita sobrescribir memoria. El desborde anterior a redacción conserva el documento original y el posterior sigue la ruta de aviso de capacidad. Se migraron consumidores de prueba de dict a estado tipado; los dobles de `gh api` incorporaron paginación y lookup de C4. La batería completa inicial detectó 17 fallos de fixtures y aserciones, corregidos en esos tests; debe repetirse en integración. |
| C6 | `046fa04508f16bd9d791fa032ad98c5656e12469` | `PYTHONPATH=tests:. python3 -m unittest tests.test_review_document` (43 tests); `ruff check --select F review_document.py` | Pasan. La prueba `CodecDocumento.test_salida_caracterizada_antes_de_reducir_prosa` fijó antes del refactor hashes SHA-256 de body y summary para seis escenarios: simple, multibyte, historia, tres explicaciones, fallo sin review y libro omitido. Después de introducir `ProseSlot` y `ExplanationSlot`, todos los hashes permanecen idénticos; el presupuesto de bytes y caracteres y el orden de reducción conservan los valores anteriores. |
| C7 | `9f0b70e4c29a4961f7ac6c0793684b3fbdce94fa` | `PYTHONPATH=tests:. python3 -m unittest tests.test_review tests.test_review_context tests.test_review_domain tests.test_e1a_workflow tests.test_review_simplificacion` (513 tests); `PYTHONPATH=. python3 scripts/migrate_review_facade.py` (segunda ejecución sin cambios); `ruff check --select F review*.py scripts/*.py`; `git diff --check` | Pasan. El inventario AST y el de archivos versionados no encontraron consumidores de `review.parse_findings_block`, `review.serialize_findings` ni de exports de contexto/dominio. El seed E1 ejecuta el Python embebido real de YAML, numera F1/F2 y mantiene modo incremental, diff y rutas; ahora importa del dominio. Se retiraron `__all__`, wrappers y constante local; los parches cambiaron al módulo que usa el CLI. |

## Integración y revisión

Código comprobado en `9f0b70e4c29a4961f7ac6c0793684b3fbdce94fa`. `bash scripts/check.sh` terminó en `check: OK`: pre-commit pasó gitleaks y conflictos, actionlint validó workflows y templates, la partición cubrió 425 + 425 pruebas sin duplicados y la batería completa pasó 850 pruebas. `ruff check --select F review*.py scripts/*.py` pasó. `python3 scripts/run_test_shard.py --verify-partition 2` confirmó los mismos 850 tests. `PYTHONPATH=tests:. python3 -m unittest tests.test_verify_sticky` pasó 10 tests del parser externo. Las nueve pruebas adicionales respecto de las 841 de base cubren defectos y caracterización; se actualizaron expectativas y fixtures existentes sin borrar tests.

El código no cambió después de la batería. La única modificación posterior es este registro y `tn-fixes-pr.md`; pre-commit pasó de nuevo antes del commit documental. La revisión final de Codex fue aprobada tras cerrar R1–R3; véase [el registro de aprobación](tn-fixes-approval.md). No hubo merge, auto-merge, deploy ni cambio de pins.

## Correcciones tras la revisión del PR #173

El informe `docs/reviews/2026-11-15-tn-fixes-final.md` revisó
`2396be5571d5c0ca2f584478f4f5a5c421ca9f34` y pidió tres correcciones.
El informe y sus sondas permanecen en el checkout principal; esta sección
registra las correcciones en la rama del PR.

| Hallazgo | Commits | Comprobación y resultado |
| --- | --- | --- |
| R1 / C4 | `ad01778b11782fa635d497f4b8ba13a5b35139bf`, `4c76a82db16bb3a45fa8a13e37cac7b9f91946a6` | La prueba del `cmd_reconcile` real falló antes del cambio con un run histórico de otro PR. Ahora un listado íntegro con runs legacy terminados de otro PR y del PR actual permite recoger el resultado moderno tanto por callback como por recuperación posterior. Un run legacy propio aún activo, un título desconocido y una búsqueda incompleta conservan `RunSearchUnknown`. Los controles existentes mantienen la autenticación de artifact, ref, SHA y attempt, además del orden del run actual. Pasan 71 pruebas de coordinador. |
| R2 / C6 | `5f154accedfe19bb0163f9b9d730a849eefb196d` | Se retiraron las cinco comprobaciones imposibles tras `_with_notice`. Pasan 69 pruebas de documento y capacidad. La matriz determinista de 48 casos del informe produjo cero diferencias de cuerpo, summary o resultado entre el SHA revisado y `4c76a82db16bb3a45fa8a13e37cac7b9f91946a6`. Los controles de presupuesto de `_render` y `_capacity` siguen intactos. |
| R3 / C7 | `5e79a75f7ebbdc80824156eb6469f4db1b84dcb3` | `ERROR_KEY` y `PARTIAL` se importan de `review_config` y `review_domain`. El codemod obtiene propietarios de definiciones de primer nivel, no de imports transitivos. Dos pruebas nuevas fallaron antes del cambio y ahora comprueban propietario y segunda ejecución sin cambios. Pasan 62 pruebas de migración, cierre y documento. |

La batería integrada sobre el código `4c76a82db16bb3a45fa8a13e37cac7b9f91946a6`
terminó en `check: OK`: pre-commit, gitleaks, conflictos, actionlint,
partición de 427 + 427 sin duplicados y 854 pruebas completas. Las cuatro
pruebas añadidas respecto de las 850 previas cubren el historial mixto,
incertidumbre y selección del dueño; no se eliminaron pruebas. Pasaron
`ruff check --select F review*.py scripts/*.py`,
`git diff --check d1858c0c1dd67b5a726f9ec09318e88a778a6e08..HEAD`
y 10 pruebas del parser externo en `tests.test_verify_sticky`.
El estado de CI para el nuevo SHA se comprueba después de publicar.

Estado de cierre: Codex aprobó `dfbf47f8a846bd79d5e31f53c2b85f30d6c947b4`. El [informe final](../../reviews/2026-11-15-tn-fixes-rereview.md) sustituye el veredicto anterior y enlaza las comprobaciones independientes.
