# Medición T16: evidencia de corridas (f9 y f10)

Evidencia versionada de la medición completa de T16 (decisión N2 de
VEREDICTO-T16-f9: los artefactos de GitHub vencen, esta copia no).

- Código del revisor ejecutado: tag `t16-candidato-e0898ff` =
  `e0898ffbb305547bc8175ec64ca1573b7e3c337b` (rama `rc/t16-piloto`).
- Proveedor: deepseek (`deepseek-v4.1-flash vía deepseek directo`, `ATTEMPTS=2`).
- Workflow: `e1-measure.yml`, sin publicación (permisos `contents: read`).
- Muestra: el pairing congelado `evaluation/reviewer/v2/pairing.json`
  (10 controles de producto, 18 controles de push, 18 deltas delta-d0,
  4 dependencias de `pushes[0]`).

## Contenido

- `corridas-f9/<corrida>/result.json` y `manifest.json`: las 50 corridas de la
  medición completa (fase 9, 2026-10-09). El campo `result` de `result.json`
  (texto de hallazgos) se quitó para no inflar el árbol; el `result.json`
  completo vive en el artefacto `e1-salida-…` de cada run ID (retención
  GitHub hasta 2027-01-07) y en `/tmp/t16-f9` mientras exista.
- `corridas-f10/<corrida>/…`: las 12 corridas de la repetición de los 6 pares
  de repetición 2 en el orden congelado `variante-control` (fase 10). Misma
  decisión de recorte de texto.
- `ledger-f9.jsonl` y `ledger-f10.jsonl`: una fila por corrida con par, brazo,
  run ID, conclusión, costo, duración, turnos, modo del manifest y si el
  protocolo se cumplió.
- Los `run ID` de cada corrida son la clave contra GitHub Actions.

## Costo real

- Fase 9 (50 corridas): **$89.3827**.
- Fase 10 (12 corridas, repetición de los pares de rep 2): **$19.6463**.
- Medición f9+f10: **$109.029**. Total T16 incluyendo la corrida unitaria de
  costo de f3 ($1.6149, run 37840785742): **$110.6439**.
- Decisión del análisis: `docs/evidence/reviewer/D0-C0.md` (D0 vuelve a
  current; C0 sin decidir).

## Desvío registrado (B1 de VEREDICTO-T16-f9)

En la fase 9, los 6 pares de la repetición 2 corrieron control antes que delta,
al revés del orden congelado (`variante-control`). Esas 12 corridas NO se
borran: quedan aquí como desvío reemplazado, marcadas por sus run IDs
(37877147828, 37878851748, 37877156451, 37879140906, 37877447623, 37879515870,
37877765765, 37879837105, 37878039107, 37880148673, 37878479438, 37880521695).
La fase 10 repitió esos 6 pares en el orden congelado con los mismos bloques
fuente; para el análisis de D0, la repetición 2 válida de cada par de push es
la de `corridas-f10`.
