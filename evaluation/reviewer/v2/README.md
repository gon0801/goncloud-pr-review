# Comparación y cegamiento versionados (v2)

Versión 2 del circuito de evaluación offline. Separa las métricas por producto y
configuración, fija los pares antes de observar resultados y congela el protocolo
de adjudicación. No llama modelos, no consulta la red y no publica comentarios.
El formato histórico de `evaluation/reviewer/` sigue vigente y no cambia.

## Contenido de este directorio

- `partition.json`. Asigna PRs completos a `ajuste` o `reservada`. Todos los
  pushes de un PR quedan en el mismo grupo.
- `pairing.json`. Fija pares y repeticiones antes de observar resultados nuevos.
  Cada par declara su CaseKey completo, los lados control y variante, y un
  `estado` que dice si sus salidas ya fueron observadas antes del congelamiento.
  La sección `adjudicacion` congela la versión de normalización, la lista de
  jueces y la regla de desempates.
- `observaciones.json` (cuando exista recolección). Formato v2 con las claves
  completas que describen los dos scripts de abajo.
- `adjudicacion-ciega.json` y `ciego-correspondencia.json`. Salidas del generador
  versionado. La correspondencia es sólo para el operador.

## Claves

```
CaseKey         = (caso, repo, base, head, tarea, sha_anterior)
ObservationKey  = (CaseKey, producto, configuracion, repeticion, intento)
FindingKey      = (ObservationKey, hallazgo)
PairKey         = (CaseKey, experimento, repeticion)
```

`sha_anterior` es `null` cuando el caso no es un experimento incremental. Una
observación debe repetir el CaseKey declarado en el corpus, sin diferencias.

## Generar la hoja ciega v2

```
python3 scripts/build_adjudicacion_ciega.py --versionado \
    --raiz-v2 evaluation/reviewer/v2
```

Lee `observaciones.json`, `partition.json` y `pairing.json` de `--raiz-v2` y
escribe ahí mismo `adjudicacion-ciega.json` y `ciego-correspondencia.json`. El
modo histórico (`--raiz`) no cambia. Validaciones con salida 2 y mensaje en
stderr: caso sin partición, partición con casos de más o de menos, pairing sin
sección `adjudicacion`, `normalizacion` distinta de `1`, o juez con tipo fuera
de `humano` e `ia`.

La hoja normaliza la severidad a una escala común y guarda la severidad
original en la correspondencia. Los dos vocabularios de origen caen en la
misma escala: el revisor declara `Critical`/`High`/`Medium`/`Low` y la captura
de comentarios declara `Minor`/`Major`/`Critical`/`Trivial`/`Nitpick`.

| severidad declarada | normalizada |
|---|---|
| `trivial`, `nitpick`, `minor`, `low` | `baja` |
| `major`, `medium` | `media` |
| `critical`, `high` | `alta` |
| ausente o desconocida | `no declarada` |

El diagnóstico y la evidencia se redactan contra el inventario completo de
observaciones: cada mención del producto, y de la configuración cuando mide
8 caracteres o más, se reemplaza por `[redactado]`, de la cadena más larga a
la más corta, para que un nombre cruzado (el producto de otra observación)
también desaparezca; la configuración corta (por ejemplo `v1` o `ca`) no se
toca para no destrozar el texto. Además se quitan de la evidencia los
metadatos de herramienta: comentarios HTML, bloques `<details>`, líneas `✅
Addressed in commit` y badges. La `meta` de la hoja registra la versión de
normalización, los jueces, los desempates y si la adjudicación usa IA.

## Comparar con pares versionados

```
python3 scripts/compare_reviews.py --corpus corpus.json \
    --observations observaciones.json --judgments adjudicaciones.json \
    --pairing pairing.json --output informe.json
```

El corpus v2 declara el CaseKey por caso. Sin `--pairing` el comparador corre el
modo histórico con su formato de siempre. Con `--pairing`, el informe no trae
precisión global: cada grupo `(producto, configuracion)` informa hallazgos,
precisión con denominador explícito, defectos con o sin conjunto de referencia,
intentos (los fallidos se conservan con su duración y costo), y solicitudes que
suman los reintentos. Una observación con `duracion_s`, `turnos` y `costo_usd`
todos ausentes (por ejemplo, comentarios existentes) no es una ejecución: los
bloques de intentos, solicitudes y costo se calculan sólo sobre ejecuciones, el
grupo informa cuántas no lo son en `sin_ejecucion`, y esas observaciones siguen
contando en observaciones, hallazgos y precisión. Los pares con contraparte
ausente, con campos del caso que difieren de los declarados o con control y
variante que resuelven a la misma observación se rechazan con motivo, y sus
observaciones quedan en `sin_pareja`. Una fila de adjudicación con la clave
completa resuelve contra una única observación; una fila histórica (sólo caso y
hallazgo) se acepta sólo si resuelve a una, y una ambigua se rechaza. Un
`duplicate` cuyo `duplicado_de` es el propio hallazgo se rechaza.

## Estado

Los dos generadores están entregados y probados offline. La recolección de
observaciones v2 está pendiente de autorización; `pairing.json` registra la
muestra disponible (20 de 30 PRs, seis de diez pares de pushes y cinco pares de
productos en el SHA exacto) con la causa de cada ausencia. Detalles en
`docs/evidence/reviewer/E2.md`.
