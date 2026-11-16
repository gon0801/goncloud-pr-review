# Aprobación de TN1–TN7

Codex revisó el PR #173 con `pstack:thermo-nuclear-code-quality-review` y aprobó
el SHA `dfbf47f8a846bd79d5e31f53c2b85f30d6c947b4` después de cerrar R1, R2 y R3.
El merge `5381ed0582cf98dde8a7863486a775992af62c76` contiene el mismo árbol Git.

- R1: el historial antiguo conocido ya no bloquea resultados actuales. Se
  reprodujeron tres páginas con 201 runs y recuperación con y sin callback.
  Un run antiguo propio activo conserva el pendiente hasta poder resolverlo.
- R2: retiradas las cinco ramas imposibles posteriores a `_with_notice`.
  Los 48 casos de capacidad comparados conservan cuerpos, summaries y resultados.
- R3: los consumidores usan los módulos propietarios. El codemod reconoce
  definiciones, en vez de depender de imports transitivos.

La revisión ejecutó 854 pruebas, revisión de secretos, actionlint y partición
de tests. Los cinco checks remotos del SHA aprobado terminaron correctamente.
Las tres sondas del publicador directo conservaron memoria y cabecera ante
desbordes de estado, redacción y cuerpo.

Esta aprobación cubre los cambios C1–C7 del PR y sus interacciones revisadas.
La activación del CLI se entrega por separado, cambiando juntos los pins del
publicador y del worker según `docs/reviewer-rollout.md`.

## Informe y checks del SHA aprobado

El [informe final de Codex](../../reviews/2026-11-15-tn-fixes-rereview.md)
contiene el veredicto APPROVE, su alcance y las sondas independientes. Sustituye
el REQUEST_CHANGES de la primera revisión, que se conserva como antecedente.

| Check | Conclusión | Ejecución |
| --- | --- | --- |
| test (shard 0/2) | success | [CI](https://github.com/gon0801/goncloud-pr-review/actions/runs/43776779128) |
| test (shard 1/2) | success | [CI](https://github.com/gon0801/goncloud-pr-review/actions/runs/43776779128) |
| verify-partition | success | [CI](https://github.com/gon0801/goncloud-pr-review/actions/runs/43776779128) |
| workflows | success | [CI](https://github.com/gon0801/goncloud-pr-review/actions/runs/43776779128) |
| gate | success | [Quality](https://github.com/gon0801/goncloud-pr-review/actions/runs/43776779120) |
| reconcile | skipped | [Borrador](https://github.com/gon0801/goncloud-pr-review/actions/runs/43776777434) |

El skip del borrador no es una prueba de integración. La ejecución real del
CLI activado en el central se comprobó en el [worker del PR #175](https://github.com/gon0801/goncloud-pr-review/actions/runs/43778428379):
artifact `14187806201`, únicamente `result.json`, `model_ok=true` y comentario
releído con documento v1 íntegro, memoria schema 3 y cobertura completa sobre
`3f7d7bb0036f464c192d498605fd14a7328b3def`.
