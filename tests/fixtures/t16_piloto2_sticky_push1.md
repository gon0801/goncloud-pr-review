<!-- ai-review:sticky -->
<!-- ai-review:sha=2527b1254cccf91bdb8a4495318db4b4a41eba17 -->
<!-- ai-review:completion=2527b1254cccf91bdb8a4495318db4b4a41eba17:complete -->
<!-- ai-review:findings={"schema":3,"generation":2,"revision":{"base_sha":"24d63e13d7f6f2aaa6b30977670c2f73a1e0e369","head_sha":"2527b1254cccf91bdb8a4495318db4b4a41eba17","policy_digest":"3ee2cc1dd49a9212c66491e5926ba777de747344486f7f96321e4e086ab3e54c"},"next_id":2,"completion":"complete_claim","command_cursor":0,"request_count":1,"pending_requests":[],"receipts":[],"findings":[{"id":"F1","title":"Ejecución arbitraria de código: eval sobre el contenido del archivo","severity":"Critical","status":{"kind":"open"},"primary_anchor":{"kind":"legacy","path":"piloto/caso_t16.py","line":15},"related_anchors":[],"cause_hint":null,"evidence":[]}]} -->
### Revisión automática · DeepSeek V4.1 Flash · OpenCode Go · 2527b12

**Veredicto:** 1 Critical abierto.

## Nuevos en este push

- 🔴 Critical · `piloto/caso_t16.py:15` · Ejecución arbitraria de código: eval sobre el contenido del archivo · F1

## Siguen abiertos

Ninguno.

## Detalle del revisor

He revisado el diff completo (un único archivo nuevo, `piloto/caso_t16.py`), y verifiqué que no lo importa ningún otro punto del repo ni lo carga dinámicamente ningún script (`scripts/`, workflows, ni descubrimiento de tests: `piloto/` no contiene nada más).


#### 🔴 Critical · `piloto/caso_t16.py:15` · Ejecución arbitraria de código: `eval` sobre el contenido del archivo

- **Qué pasa:** `leer_config` lee el archivo indicado por `ruta` y pasa su contenido a `eval(datos)`. Cualquier archivo que termine leyéndose se interpreta como código Python, con los privilegios del proceso.
- **Por qué importa:** es ejecución arbitraria de código (RCE) sobre datos que en un caso realista provienen de configuración, no de código propio. Si alguien copia este módulo como plantilla (es su propósito declarado como ejemplo de "caso") o lo importa desde otro lado, `leer_config("conf.py")` ejecuta lo que haya en el archivo: borrado de ficheros, exfiltración, lectura de secretos del entorno.
- **Arreglo:** no usar `eval` nunca para datos. Si el formato es JSON, `json.load(open(ruta))`; si es YAML, `yaml.safe_load`; si es config Python, `configparser`/`tomllib`. Requiere validación explícita del formato y del contenido, nunca evaluación.

#### 🟠 High · `piloto/caso_t16.py:9` · `ultimo` siempre lanza `IndexError` (off-by-one)

- **Qué pasa:** `lista[len(lista)]` indexa una posición más allá del final. Para cualquier lista no vacía, `len(lista)` está fuera de rango.
- **Por qué importa:** la función no puede devolver nunca el último elemento; toda llamada falla con `IndexError: list index out of range`. Es un bug determinista, no un caso borde.
- **Arreglo:** `return lista[-1]` (o `lista[len(lista) - 1]`), y decidir explícitamente qué hacer con la lista vacía.

#### 🟡 Medium · `piloto/caso_t16.py:20` · `mediana` no es la mediana para listas de tamaño par (y falla con lista vacía)

- **Qué pasa:** `ordenados[len(ordenados) // 2]` devuelve el elemento superior de los dos centrales. Con `[1, 2, 3, 4]` devuelve `3`, cuando la mediana es `2.5`. Con lista vacía lanza `IndexError`.
- **Por qué importa:** resultado estadísticamente incorrecto para todo conjunto con cantidad par de elementos, que es la mitad de los casos en uso normal. El fallo es silencioso: no hay excepción, solo un número equivocado.
- **Arreglo:** promediar los dos centrales cuando el largo es par y definir el caso vacío (`ValueError` o `None`).

#### 🟡 Medium · `piloto/caso_t16.py:5` · `promedio` lanza `ZeroDivisionError` con lista vacía

- **Qué pasa:** `sum(valores) / len(valores)` divide por cero cuando `valores` está vacío.
- **Por qué importa:** un agregado sobre un conjunto vacío (respuesta sin datos, filtro que no matcheó) aborta el flujo en lugar de devolver un valor definido.
- **Arreglo:** validar el largo y devolver `0` o lanzar un `ValueError` con mensaje propio según el contrato deseado.

#### ⚪ Low · `piloto/caso_t16.py:13` · El archivo abierto en `leer_config` nunca se cierra

- **Qué pasa:** `open(ruta)` no usa `with`, y `archivo.close()` nunca se llama.
- **Arreglo:** `with open(ruta, encoding="utf-8") as archivo:` (y fijar codificación explícita para evitar dependencias de locale).

#### ⚪ Low · `piloto/caso_t16.py:1` · El módulo no tiene ninguna prueba

- **Qué pasa:** no existe test alguno que mencione el archivo (`tests.txt` no encuentra coincidencias y no hay descubrimiento automático sobre `piloto/`).
- **Arreglo:** al ser un fixture del piloto pensado para ejercer el revisor, conviene que los casos (off-by-one, `eval`, mediana par) queden cubiertos por pruebas que fallen si se revierte el arreglo; de lo contrario nada protege estos comportamientos.

<details><summary>Alcance de la revisión</summary>

- Revisados: 1 archivo(s)
- Turnos: 12/60 · tokens entrada 6,555 (+37,760 en caché) · salida 3,664

</details>
