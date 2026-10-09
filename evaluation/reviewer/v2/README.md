# Comparación y cegamiento versionados (v2)

Versión 2 del circuito de evaluación offline. Separa las métricas por producto y
configuración, fija los pares antes de observar resultados y congela el protocolo
de adjudicación. No llama modelos, no consulta la red y no publica comentarios.
El formato histórico de `evaluation/reviewer/` sigue vigente y no cambia.

## Contenido de este directorio

- `partition.json`. Asigna PRs completos y sus push-casos a `ajuste` o
  `reservada`. Todos los pushes de un PR quedan en el mismo grupo.
- `pairing.json`. Fija pares y repeticiones antes de observar resultados nuevos.
  Cada par declara su CaseKey completo, los lados control y variante, y un
  `estado` que dice si sus salidas ya fueron observadas antes del congelamiento.
  Los pares de `push-consecutivo` declaran su caso `<caso>-push-<i>` con la
  CaseKey de la proyección de abajo. La sección `adjudicacion` congela la
  versión de normalización, la lista de jueces y la regla de desempates.
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

## Proyección de push-casos

Para cada caso del corpus con `pushes = [p0, p1, …, pn]` y al menos un push,
además del caso base (head del PR, `sha_anterior` null) existen los casos
`<caso>-push-<i>` para `i = 1..n`, con `head = pushes[i]` y
`sha_anterior = pushes[i-1]`; heredan `repo`, `base` y `tarea` (la categoría
del PR). Los casos sin pushes o con un único push no generan push-casos. Con
el corpus congelado: 20 casos base y 6 push-casos (`pr2-push-1/2/3`,
`pr4-push-1`, `pr9-push-1`, `pr13-push-1`).

## Dependencias T16

Congeladas en T16 f6 y corregidas en f8 (decisión B1 de VEREDICTO-T16-f7,
opción i). El experimento `dependencia-control-push-0` del `pairing.json`
congela, para cada caso del corpus con al menos dos pushes, un caso
`<caso>-dep-push-0` con `head = pushes[0]` y `sha_anterior` null (con un
único push el caso base ya revisa `pushes[0]` y no hay dependencia). Con el
corpus congelado: 4 dependencias (`pr2-dep-push-0`, `pr4-dep-push-0`,
`pr9-dep-push-0`, `pr13-dep-push-0`), una corrida de control por PR, corrida
una sola vez y compartida por las repeticiones 1-3 de su PR.

Esa corrida es el eslabón inicial de la cadena de memoria de los deltas de
push-1: no es un par medido y **no se registra en `observaciones.json` v2**.
Su única salida es el bloque de hallazgos del control, que viaja como
`prev_findings` a los deltas de push-1 y queda retenido en el artefacto
`e1-salida-<caso>-…` de la corrida. Por eso los casos `<caso>-dep-push-0` NO
están en `partition.json`: la partición sólo cubre los 26 casos que generan
observaciones (20 base + 6 push-casos), y el generador ciego v2 exige
`set(particion) == casos(observaciones)`. Si una corrida de dependencia se
registrara como observación v2, el comparador la rechazaría como par con
«contraparte ausente» (variante `ninguno`).

`partition.json` lleva los 26 casos: cada push-caso hereda el grupo de
partición de su PR. `pairing.json` declara cada par de `push-consecutivo` con
su caso `<caso>-push-<i>` y esa CaseKey de proyección. Las observaciones de un
push se capturan con caso `<caso>-push-<i>` y su head/sha_anterior de
proyección, nunca bajo el caso base: el comparador exige que cada observación
repita la CaseKey del caso declarado y el caso base declara el head del PR, así
que una observación de push bajo el caso base se rechaza con salida 2. Las corridas de
las dependencias no generan observaciones adjudicables: no son pares y el
informe v2 no las evalúa.

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
observaciones: los identificadores de todo el inventario (el producto de cada
observación, y su configuración cuando mide 8 caracteres o más) se deduplican
y se aplican de la cadena más larga a la más corta, sin distinguir mayúsculas,
para que un nombre cruzado (el producto de otra observación) tampoco deje
restos (`tool` no corta a `tool-plus`); la configuración corta (por ejemplo
`v1` o `ca`) no se toca para no destrozar el texto. Además se quitan de la
evidencia los metadatos de herramienta: comentarios HTML, bloques
`<details>`, líneas `✅ Addressed in commit`, líneas de badges (todos los
segmentos separados por `|` empiezan por un emoji u otro carácter no
alfanumérico, tras quitar espacios y énfasis) y la marca de severidad propia
(emoji más palabra de severidad, con viñeta, énfasis o marcadores de
encabezado alrededor, y su separador `·` o `|`), que se quita donde aparezca,
no sólo al inicio de la línea; el emoji es el requisito de marca, así que una
palabra de severidad en prosa sin emoji no se toca. La `meta` de la hoja
registra la versión de normalización, los jueces, los desempates y si la
adjudicación usa IA.

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
hallazgo) se acepta sólo si resuelve a una, y una ambigua se rechaza. El
`duplicado_de` de un `duplicate` resuelve contra los hallazgos del caso: la
auto-referencia o la ambigüedad se rechazan.

## Estado

Los dos generadores están entregados y probados offline. La recolección de
observaciones v2 está pendiente de autorización; `pairing.json` registra la
muestra disponible (20 de 30 PRs, seis de diez pares de pushes y cinco pares de
productos en el SHA exacto) con la causa de cada ausencia. Desde r4 cada push es
su propio caso (`<caso>-push-<i>`): mientras la recolección D0 no capture las
observaciones de esos casos, los seis pares de `push-consecutivo` quedan
rechazados con `contraparte ausente` (sus observaciones todavía no existen) y
esa es la salida esperada del comparador, no un defecto. Detalles en
`docs/evidence/reviewer/E2.md`.
