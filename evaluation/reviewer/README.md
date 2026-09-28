# Comparador de revisiones del revisor

Herramienta offline para medir la calidad de revisiones ya capturadas sobre los
mismos commits. No llama modelos, no consulta la red y no publica comentarios:
sólo lee tres archivos JSON y escribe un informe.

## Comando

```
python3 scripts/compare_reviews.py --corpus corpus.json \
    --observations observaciones.json --judgments adjudicaciones.json \
    --output informe.json
```

Salida 0 con informe escrito; salida 2 con mensaje en stderr si algún par
inválido o fila duplicada no pasó la validación.

## Entradas

`corpus.json` define los casos que se miden:

```json
{"casos": [{"caso": "c1", "repo": "o/r", "base": "<sha40>", "head": "<sha40>",
            "producto": "revisor local", "configuracion": "v1", "intento": 1}]}
```

`observaciones.json` trae una o más salidas capturadas por caso, identificadas por
la clave completa `(caso, producto, configuracion, intento)`, con base y head
exactos del corpus, resultado, cobertura declarada, duración en segundos,
turnos, costo en USD (`null` = desconocido) y la lista de hallazgos con
`id`, `titulo`, `ruta` y `resuelto`.

`judgments.json` trae una fila de adjudicación por hallazgo:

```json
{"adjudicaciones": [{"caso": "c1", "hallazgo": "F1", "veredicto": "valid",
                     "defecto": "D1"}],
 "defectos": {"c1": ["D1", "D2"]}}
```

El `veredicto` es `valid`, `false_positive`, `duplicate` o `unresolved`. Con
`duplicate` se nombra el hallazgo original en `duplicado_de`. `defecto`
registra el ID del defecto conocido que el hallazgo detectó. `resuelto_real:
false` en una fila marca que la resolución que declara la observación es
falsa. `defectos` es opcional: sin conjunto de defectos conocidos no hay
recuperación que calcular.

## Qué informa

- Precisión: válidos sobre (válidos + falsos positivos + duplicados). Los
  `unresolved` salen del denominador y el informe dice cuántos excluyó.
- Defectos conocidos: detectados y omitidos, con recuperación sólo cuando el
  conjunto existe; si no existe, el informe dice `desconocido`. Los IDs de
  defecto son por caso: se acreditan y comparan como pares `caso/defecto`, y
  los omitidos se listan así. Un `defecto` acreditado fuera del conjunto de su
  propio caso rechaza la corrida con salida 2 en vez de cruzar crédito entre
  casos.
- Falsos resueltos: hallazgos que la observación marca `resuelto` y cuya fila
  declara `resuelto_real: false`.
- Cobertura declarada: la que cada observación declaró. Una declaración
  `complete` no demuestra que se hayan encontrado todos los defectos.
- Tiempo: duración total y promedio en segundos y turnos totales.
- Costo: suma de los costos conocidos y cuántas observaciones tienen costo
  desconocido. El costo ausente nunca se reemplaza por cero.

## Reglas de oro

- La identidad de una observación es la clave completa `(caso, producto,
  configuracion, intento)`: se aceptan varias observaciones del mismo caso
  cuando la clave difiere, y dos observaciones con la misma clave completa se
  rechazan con salida 2.
- La adjudicación es por `(caso, hallazgo)`: un ID de hallazgo no puede
  repetirse entre observaciones del mismo caso (usa IDs distintos por
  producto); `cobertura_declarada` es una lista determinista de objetos con
  `caso`, `producto`, `configuracion`, `intento` y `cobertura`, sin serializar
  la identidad a una cadena.
- Sólo se comparan salidas del mismo SHA: base y head de la observación deben
  ser exactamente los del corpus, o el comparador rechaza el par.
- No hay verdad de referencia externa: la adjudicación humana es la entrada,
  no un resultado del comparador.
- Sin evidencia se informa `desconocido`; el comparador no inventa ceros ni
  recuperación.
