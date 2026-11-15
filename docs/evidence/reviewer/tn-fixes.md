# Ejecución de TN1–TN7

Base `d1858c0c1dd67b5a726f9ec09318e88a778a6e08` (`origin/main` al arrancar).
Worktree `/var/folders/hp/sc_3c6qs7nvdswl_x3w5sdbr0000gn/T/reviewer-tn-work.0vxcxp/checkout`.
Rama `fix/reviewer-tn1-tn7`. El checkout fuente permanece intacto.

La comparación `git diff d1858c0c1dd67b5a726f9ec09318e88a778a6e08..origin/main -- 'review*.py' action.yml .github templates scripts tests` no produjo diferencias. La auditoría de esa base registra 841 pruebas existentes. Python 3.14.7, `pre-commit`, `gitleaks`, `actionlint`, `ruff` y `gh` están disponibles. `gh api --help` admite `--paginate --slurp`.

## Entregas

| Unidad | Commit | Comprobación | Resultado |
| --- | --- | --- | --- |
| C1 | `fix(action): mantener identidad entre pasos` | `PYTHONPATH=tests:. python3 -m unittest tests.test_review.CableadoIdentidad tests.test_review.Workflows`; `actionlint -color` | 74 tests y actionlint pasan. La prueba nueva falló antes del cambio porque `prepare` y `publish` elegían `titles` mientras `run` elegía `anchors`. |
| C2 | `fix(domain): rechazar metadatos incompatibles antes de aceptar` | `PYTHONPATH=tests:. python3 -m unittest tests.test_review_domain.ObservacionDeEntrada tests.test_review_domain.ReportBoundary tests.test_review.CableadoIdentidad`; `ruff check --select F review_domain.py tests/test_review_domain.py` | 30 tests y ruff pasan. Las 17 variantes inválidas de los tests nuevos fallaron antes de validar; un reporte mixto publica solo el hallazgo válido con cobertura parcial. |
| C3 | pendiente | pendiente | pendiente |
| C4 | pendiente | pendiente | pendiente |
| C5 | pendiente | pendiente | pendiente |
| C6 | pendiente | pendiente | pendiente |
| C7 | pendiente | pendiente | pendiente |

## Integración y revisión

Pendiente. El PR queda en borrador hasta la revisión final de Codex. No merge ni deploy.
