# FASE 2: skill de verificacion (codigo)

Rama: poteto/fase-2-verificacion (apilada sobre poteto/fase-1-cobertura).
Sin skillverif disponible aqui: se entrega codigo + pruebas.

## Que es
`scripts/verify_sticky.py`: verifica un comentario fijo publicado contra
sus invariantes, con el cuerpo por archivo, argumento o stdin (el que lo
usa trae el cuerpo con `gh api ... --jq .body`; la herramienta no toca red
ni secretos). Solo stdlib, reusa la frontera FASE 1.

Chequeos, falla cerrado:
1. cabecera MARKER/sha/completion en orden (`reviewed_completion`).
2. bloque legible (ni ausente ni corrupto ni futuro).
3. coherencia marcador/snapshot (`marker_of(snapshot.completion)`).
4. sha esperado opcional.

## Pruebas
`tests/test_verify_sticky.py` (7 casos, subprocess real): legado
coherente/incoherente, v2 completo/parcial, sin bloque, bloque roto,
sha esperado ok/mal. 7/7 OK.

## Archivos
- `scripts/verify_sticky.py` (nuevo), `tests/test_verify_sticky.py` (nuevo).
- Esta evidencia.
