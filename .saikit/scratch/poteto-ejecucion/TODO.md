# poteto-ejecucion TODO (feature playbook steps verbatim + phases)

Playbook elegido: feature (multi-phase: FASE 0-3 en secuencia, una fase a la vez).
Nota: progress_card no existe en este gateway (tool_search solo devuelve dashboard);
se usa este TODO como checklist (fallback que el propio poteto-mode indica).
Skills how/why/architect/swarm/interrogate no disponibles aqui; se sigue sin ellas.

## Feature playbook (verbatim)
1. `how` over the affected subsystem. -> skip: skill no disponible; se usa lectura directa del repo.
2. `architect` for parallel design exploration. -> skip: skill no disponible; diseno directo y pequeno.
3. Write the throughput checkpoint as four todo items. (abajo)
4. Delegate code-writing to a subagent... -> skip: sin subagentes con modelos configurados; diff propio con revision separada.
5. Verify on the matching surface. "Inconclusive" or wrong-surface is not a pass. Flag it.
6. Rebase into small, ordered commits. Stack follow-ups.
7. If the design is contested, `interrogate` before shipping. -> skip: skill no disponible.
8. Run **Opening a PR**.

## Throughput checkpoint
- Blocking first steps: FASE 0 piso verde (suite en verde + un solo comando) antes de fan-out. Nada paraleliza hasta el piso.
- Independent workstreams: FASE 0, 1, 2, 3 son secuenciales (cada una escribe sobre la anterior). Sin fan-out.
- Shared mutable state: review.py/review_domain.py/review_context.py los toca una fase a la vez, en serie.
- Smallest safe decomposition: un worker (esta sesion); las fases son unidades verificables chicas, un commit/PR por fase.

## Fases
- [x] FASE 0 piso determinista: PR #116 (rama poteto/fase-0-piso, 2 commits): suite verde + scripts/check.sh unico + pre-push + CI; cierra huecos #84/#86.
- [>] FASE 1 tipos/invariantes (en curso) para no perder hallazgos (complete/partial marker).
- [ ] FASE 2 skill de verificacion (codigo).
- [ ] FASE 3 minima prosa.
- [ ] Reporte final para main.
