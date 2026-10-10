<!-- >>> QUALITY-KIT CALIDAD SECTION START -- managed by quality-kit's init-repo.ps1. Do not hand-edit between these markers; re-running init-repo.ps1 will refresh this block cleanly. -->
## Calidad (quality-kit)

Candados de commit instalados (pre-commit):
- base (limpieza de archivos)
- ruff (lint + formato Python)

Comandos para correr los candados a mano:
- `pre-commit run --all-files` (todos los candados de commit)

ADVERTENCIA: se probaron estos runners de Python y todos fallaron al verificarlos: unittest (codigo de salida 1: t/homebrew/Cellar/python@3.14/3.14.7/Frameworks/Python.framework/Versions/3.14/lib/python3.14/unittest/loader.py", line 341, in discover
    raise ImportError('Start directory is not importable: %r' % start_dir)
ImportError: Start directory is not importable: '/Users/dn/dev/goncloud-pr-review/tests') -- el candado de pre-push NO se instalo a proposito. Corre las pruebas a mano hasta confirmarlas y volve a correr init-repo.ps1.

Reglas de hierro:
1. Si un candado falla, se arregla el problema real -- JAMAS se usa `--no-verify` ni se saltea un candado.
2. Cada bug arreglado incluye, en el mismo cambio, una prueba que lo habria atrapado.
8. CI: la bateria completa corre en jobs paralelos cuya union es la bateria (con candado); si un job pasa de ~10 min se shardea (bash: `SAIKIT_SHARD=i/N`; pytest: `-n auto`; jest/vitest: `--shard`), nunca se recorta.
   Carril por clasificacion de ARCHIVOS (nunca por titulo ni etiqueta): docs/chore/cierre = fast solo si TODO el cambio esta en la allowlist versionada de documentos de planificacion/evidencia/cierre (bots + lead); codigo/config y cualquier ruta no clasificada = gate con bateria completa; mixto = bateria completa + checks documentales. Fallo, cancelacion o clasificacion ausente JAMAS pasan como fast.
   Checks de docs/ledger en un job propio de segundos. Cierres de ledger de un bloque = un PR.

Flujo de verificacion:
- Durante la implementacion, corre solo las pruebas focalizadas del comportamiento modificado.
- Agrupa los hallazgos de revision y corrigelos en una sola ronda por bloque. Solo un hallazgo bloqueante (seguridad, datos, regla innegociable, comportamiento pedido roto o prueba que no discrimina), con el comando que lo reproduce, reabre el ciclo; si el mismo bloqueante vuelve en dos rondas seguidas, decide el operador.
- Ejecuta Ruff y las pruebas focalizadas despues del ultimo cambio del bloque.
- Ejecuta la bateria completa una sola vez, por SHA final del bloque de CODIGO, preferentemente en CI mediante PR; los bloques exclusivamente fast pagan los checks documentales.
- Si commit, push o CI ya validaron tests, Ruff o pre-commit sobre ese SHA, no los repitas manualmente.
- No vuelvas a ejecutar CI si el commit verificado no cambio.
- Un bloqueante nunca va a una fila del plan ni se mergea abierto: se corrige o decide el operador. Lo no bloqueante que no se corrige va a una fila del plan (Plans.md o el tracker del repo) y se nombra en el PR; una observacion tardia no bloqueante no reabre el ciclo.
- Despues del deploy, ejecuta una sola vez el checklist del repo y no repitas evidencia valida sin un cambio que pueda invalidarla.
<!-- >>> QUALITY-KIT CALIDAD SECTION END -->
