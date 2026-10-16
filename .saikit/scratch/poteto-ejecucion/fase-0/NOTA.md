# FASE 0: piso determinista (evidencia)

Base: origin/main @ cc7edd9. Rama: poteto/fase-0-piso.

## Hallazgo 1 (bug real, causa raiz): install.sh rompe el `uses:` en macOS
- `scripts/install.sh:342` usaba `\/` en el REEMPLAZO de `${content//.../...}`.
- En bash 5 (ubuntu/CI) `\/` colapsa a `/`; en bash 3.2 (macOS) queda literal.
- Efecto: el retiro al modo directo (camino del revert #86) instalaba
  `uses: gon0801\/goncloud-pr-review@<sha>` (ref invalida) en macOS.
- CI nunca lo vio (solo corre ubuntu). Sin comando unico local ni pre-push,
  nadie lo corria en Mac.
- Fix: la barra llega por variable (`$ref`); patron intacto, vale en 3.2 y 5.x.
- Prueba: `tests/test_install.py::CompatibleRollback::test_el_retiro_atomico_restaura_el_escritor_anterior`
  (ya existia: reproduce en 2 s, estaba en rojo solo en macOS).
- Antes: `scripts/install.sh` con `\/` (copia en install.sh.antes).
- Despues: 19/19 OK (fix-install-19ok.log).

## Hallazgo 2: el `__init__.py` que faltaba (corregido tras revision F5)
- Precision: `unittest discover -s tests` (plano) SI corre en 3.14 sin
  `__init__.py` (probado en main pristino: 627 pruebas, 1 fallo = el bug
  de install.sh). Lo que muere sin `__init__.py` es la forma con top-level
  explicito (`discover -s tests -t .`: "Start directory is not importable"),
  que es la que usa el runner de quality-kit; por eso no pudo instalar el
  pre-push en el Mac.
- Fix: `tests/__init__.py` vacio (habilita ambas formas). IDs estables
  (`test_x.Clase...`), sharding intacto (314+313=627, verify OK).

## El comando unico: `bash scripts/check.sh`
1. Elige python >= 3.10 (el de sistema en macOS es 3.9 y no sirve).
2. `pre-commit run --all-files` (candados quality-kit; falta => error con pista).
3. `run_test_shard.py --verify-partition 2` (union == bateria).
4. Bateria completa (`unittest discover -s tests`).
- Evidencia: check-verde-627ok.log (pre-commit OK, 627 OK, `check: OK`).
- CI (`ci.yml`: shards 0/2+1/2 con union lock + actionlint) queda como esta:
  el comando local corre el superconjunto (toda la bateria + pre-commit).

## Pre-push
- `.githooks/pre-push` (fuente versionada) + `scripts/install-hooks.sh`
  (un comando lo enlaza al hooks dir de ESTE checkout).
- Instalado en este worktree. Sin `--no-verify`: lo rojo se arregla, no se salta.

## Huecos #84/#86 cerrados
- #84 metio el writer coordinado; #86 revirtio al directo. El camino de retiro
  (directo con ACTION_SHA) estaba roto en macOS sin que CI lo viera: ese era el
  hueco. Ahora lo cubre la prueba de retiro dentro del piso (corre siempre).
- Los invariantes no-perder-hallazgos del coordinado (#88) corren en verde
  dentro de la misma bateria (test_review_coordinator.py OK en check-verde).

## Archivos del cambio
- `scripts/install.sh` (fix `\/`, +4/-1)
- `tests/__init__.py` (nuevo, vacio)
- `scripts/check.sh` (nuevo, el comando unico)
- `scripts/install-hooks.sh` + `.githooks/pre-push` (nuevos)
- `.saikit/scratch/poteto-ejecucion/fase-0/` (esta evidencia)

## Incidente (declarado): el pre-push no corrio en los dos primeros pushes
- El gancho se instalo como symlink en el hooks dir del worktree
  (`.../.git/worktrees/prr-poteto/hooks/`), que este git ignora en
  worktrees enlazados: lo probe con un gancho `exit 1` y el push paso
  igual (inc. un tag de prueba, ya borrado del remoto).
- Ademas el `cd` del gancho estaba mal (`dirname $0` lleva al gitdir).
- Fix (via FASE 1): `cd "$(git rev-parse --show-toplevel)"` +
  `scripts/install-hooks.sh` con `core.hooksPath` por worktree
  (`extensions.worktreeConfig`), verificado con sonda: el push se
  bloquea con HOOK-PROBE-RAN. No toca otros checkouts.

## Incidente 2 (declarado): GIT_DIR filtrado rompe 60+ pruebas bajo el gancho
- El primer push con gancho real (FASE 1) freno bien, pero en rojo:
  61 errores + 2 fallos. Causa raiz: git exporta GIT_DIR al gancho y los
  `git` de las pruebas en repos temporales mueren con
  "this operation must be run in a work tree".
- Fix: barrido de TODO `GIT_*` arriba de `scripts/check.sh` (el hospedador
  a veces filtra techo de descubrimiento u otras; el barrido nominal no
  alcanzaba). Un solo lugar, protege todas las entradas.
- El gancho por worktree murio en el intento: `extensions.worktreeConfig`
  rompe este clon (bare=true en config comun) y el hooks dir del worktree
  lo ignora git. Gancho final: uno condicional en los ganchos comunes
  (solo corre donde hay scripts/check.sh; resto sale 0). `.githooks/`
  se elimina por archivo muerto.
- La prueba que lo habria atrapado no existe como unitaria: la verificacion
  es el propio push con el gancho en verde (camino real con GIT_DIR).

## Revision del revisor sobre #116 (F1-F5, una ronda)
- F1 Medium (cd del gancho en worktrees): ya resuelto en el stack (gancho
  comun condicional + show-toplevel, verificado con pushes reales en verde).
- F2 Low (instalador pisa gancho previo): fix en `scripts/install-hooks.sh`
  (respaldo con fecha antes de reemplazar).
- F3 Low (check.sh sin actionlint): corre si esta instalado; si no, aviso y
  el job workflows de CI queda como respaldo documentado.
- F4 Low (la prueba solo discrimina en bash 3.2): anotado: la prueba corre
  en verde en todas partes; solo FALLA (atrapa el bug) en bash 3.2/macOS.
  CI-ubuntu no puede atrapar esta clase; el candado real es el pre-push en Mac.
- F5 Low (causa del `__init__.py`): correccion arriba en Hallazgo 2.
