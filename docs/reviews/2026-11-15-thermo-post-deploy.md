# Segunda revisión thermo-nuclear tras el despliegue

**Veredicto: REQUEST_CHANGES — no pasa todavía el criterio estricto de mantenibilidad.**

SHA inspeccionado: `d1858c0c1dd67b5a726f9ec09318e88a778a6e08`.
Base de la simplificación: `bad212f5056427ddbd23909e3856cf4a014b23f3`.
Skill aplicado: `/Users/dn/.agents/skills/thermo-nuclear-code-quality-review/SKILL.md`.

## Alcance consolidado

La primera pasada cubrió el delta de simplificación. Esta ampliación revisa el
código productivo actual, sus fronteras y la operación local sobre el SHA indicado.
El veredicto consolidado es **REQUEST_CHANGES: siete hallazgos abiertos**, tres
estructurales y cuatro funcionales reproducidos. Las 841 pruebas existentes pasan;
eso no cubre los cuatro casos nuevos. No hay cambios de código posteriores al
último cierre que expliquen estos hallazgos como una nueva regresión de despliegue.

| Área | Revisión realizada |
| --- | --- |
| Entrada y configuración | `review.py`, `review_config.py`, `review_commands.py`; modo directo, comandos, identidad, permisos y aceptación |
| Dominio y persistencia | `review_domain.py`; tipos, migraciones, codecs, reconciliación, identidad, solicitudes y frontera del modelo |
| Publicación | `review_document.py`, `review_render.py`, `review_sticky.py`, `review_github.py`; capacidad, contenido oculto, conservación, escritura y relectura |
| Contexto y ejecución | `review_context.py`, `review_run.py`; selección del diff, exclusiones, contexto, tiempos, reintentos, fallback y procesos |
| Coordinación | `review_coordinator.py`; admisión, cosecha, confianza del artifact, recuperación, publicación y despacho |
| Integración | `action.yml`, workflows y plantillas de worker/coordinador, CI, Quality y E1; variables, permisos y contratos entre pasos |
| Herramientas | Instalación, hooks, secretos, comprobaciones, partición de tests, verificación del sticky y scripts de comparación/adjudicación; lectura de análisis histórico D0 |
| Evidencia ejecutada | Batería completa, revisión de secretos, actionlint, análisis estático y cuatro reproducciones adversariales locales |

Se revisaron los once módulos productivos `review*.py`. La batería completa
comprueba los tests existentes; no se afirma una inspección manual línea por línea
de todas las pruebas, fixtures, corpus y documentos históricos. No se hicieron
llamadas pagadas al modelo, una auditoría de proveedores externos ni un nuevo
ensayo de despliegue real. La evidencia de despliegue anterior conserva su alcance.
Esta es una revisión cerrada sobre un SHA y un alcance explícitos, no una prueba
matemática de ausencia de defectos.

## TN1 — Medium: permanece una fachada de compatibilidad sin completar la migración de llamadores

Ubicación: `review.py:104`, `review.py:185`, `review.py:195`;
`.github/workflows/e1-measure.yml:150`.

`review.py` sigue exportando 51 nombres; 46 no se usan dentro del módulo.
Incluye funciones y constantes cuyo dueño ya es `review_context`,
`review_domain` o `review_sticky`. También conserva `parse_findings_block`
y `serialize_findings` como adaptadores del formato legado, sin llamadas
internas. El comentario del propio módulo declara que la superficie se mantiene
para tests y adaptadores. E1 todavía importa `parse_model_findings` desde el CLI.

**Impacto:** los consumidores siguen aprendiendo una API del módulo antiguo en
lugar de sus dueños. La extracción queda parcialmente anulada: cambios de tests,
presupuestos y contratos deben seguir contemplando la fachada y sus puntos de
parcheo. Es deuda heredada que permanece, no un segundo algoritmo de aceptación
nuevo. Bajo el skill, mantener esta indirection evitable impide la aprobación.

**Corrección:** migrar imports y parches de los llamadores conocidos a los módulos
canónicos; retirar exports y wrappers que ya no tengan consumidores. Preservar
la lectura del formato legado en la frontera de almacenamiento, donde sí se
necesita. No reemplazar la fachada por otra fachada.

**Comprobación de cierre:** inventario de imports y usos de `review.*`; ejecutar
los tests de contexto/dominio cuyos parches se migren y la prueba del seed E1.
Comparar IDs, anclas y estado resultantes del seed, no solo que el import funcione.

## TN2 — Medium: el modo directo conserva un contrato intermedio serializado que ya no necesita

Ubicación: `review.py:840`, `review.py:857`, `review.py:936`, `review.py:966`.

`aceptar_reporte_directo` recibe una decisión con `Snapshot`, la codifica y
devuelve un diccionario con `block`, `merged`, `completion` y `no_acreditados`.
Su único consumidor productivo, `cmd_publish`, vuelve a leer `block` para
recuperar el `Snapshot` y entregarlo a `document_from_result`; después
`prepare_publication` lo vuelve a codificar. Los campos `merged`, `completion`
y `no_acreditados` de ese diccionario se consumen en pruebas, pero no en ese
publicador: el renderer ya obtiene esos datos del estado canónico.

**Impacto:** la ruta tipada se interrumpe con una representación heredada y un
recorrido Snapshot → JSON → Snapshot dentro del mismo proceso. También
reparte la decisión de capacidad entre aceptación y preparación. Las pruebas
mantienen vivo un contrato que la publicación real ya no usa completo.

**Corrección:** devolver un resultado explícito de aceptación/conservación con
el estado tipado y los datos realmente necesarios. Llevar la serialización a
la preparación de publicación. Conservar deliberadamente la política de no
sobrescribir memoria ante desborde y el comportamiento de los avisos; no basta
con borrar la primera comprobación de capacidad.

**Comprobación de cierre:** migrar las aserciones a estado y salida observable;
comparar IDs, descartes, resolución, cobertura y comentarios publicados para
v1/v2/v3. Probar estado que desborda, cuerpo que desborda y resultado inválido;
deben conservar el checkpoint y no afirmar una publicación confirmada.

## TN3 — Medium: el ajuste de capacidad repite el mismo mecanismo por cada clase de prosa

Ubicación: `review_document.py:460`, especialmente `552`, `570`, `590` y `612`.

`prepare_publication` tiene 181 líneas. Repite selección del texto, aviso,
recorte con elipsis, sustitución y render para revisión, histórico,
explicaciones y fallo. `_fit_slot` comparte la búsqueda binaria, pero el
recorrido de reducción sigue copiado por categoría. Además, las líneas 514,
525, 530, 548, 573, 586, 597 y 615 comprueban `CapacityExceeded` después de
`_with_notice`, aunque ese helper solo devuelve `ReviewDocument`.

**Impacto:** extender o corregir el recorte obliga a seguir varias versiones
del mismo recorrido y errores que ya no pueden ocurrir. Es una oportunidad
concreta de eliminar ramas y responsabilidades repetidas, no una objeción
al tamaño por sí solo. Las diferencias de prioridad y avisos sí son semántica
necesaria y deben permanecer explícitas.

**Corrección:** separar saneamiento del ajuste de presupuesto, compartir el
recorrido de reducción de prosa con acceso explícito a los slots, mantener el
orden actual de recortes y omisión del libro, y eliminar las ocho ramas muertas.
Evitar reflexión o un framework genérico que sustituya duplicación por magia.

**Comprobación de cierre:** comparar cuerpos y summaries antes/después en los
casos de capacidad existentes, con texto multibyte, tres avisos, tres
explicaciones, histórico y fallo. Todo cuerpo emitido debe releerse; ni el
estado ni sus solicitudes pueden truncarse.

## TN4 — Medium: el input de identidad no llega a todos los pasos de la action

Ubicación: `action.yml:77`, `action.yml:114`, `action.yml:126`;
`review_config.py:67`.

`finding_identity=anchors` solo se asigna al entorno del paso `run`.
Preparación y publicación usan el valor por defecto. Al cargar el YAML real y
resolver la política con el entorno de cada paso se obtiene
`prepare=titles`, `run=anchors`, `publish=titles`.

**Impacto:** quien selecciona explícitamente anclas recibe políticas distintas
entre generación y aceptación/publicación, y no la identidad solicitada. El
modo `current` por defecto no reproduce este desajuste. Las pruebas que asignan
la variable globalmente no prueban el cableado real de la action.

**Corrección:** propagar la misma configuración a los tres pasos, manteniendo
una sola interpretación canónica de la política.

**Comprobación de cierre:** probar los entornos reales de los pasos para ambos
inputs y verificar que preparación y publicación conservan la política y la
identidad de hallazgos de extremo a extremo.

## TN5 — Medium: el libro de cobertura permite cerrar el comentario HTML oculto

Ubicación: `review_sticky.py:43`, especialmente `51–52`.

`libro_a_linea` escapa `-->` y `<!--`, pero no `--!>`. Con una ruta como
`src/x--!><h1>Injected title</h1>.py`, `prepare_publication` produce un documento
que `read_document` considera válido. Sin embargo, el parser HTML reconoce un
encabezado visible y expone el resto del JSON del libro. Los otros escritores de
JSON oculto ya contemplan este cierre alternativo.

**Impacto:** una ruta puede alterar la presentación y revelar metadatos que
debían quedar ocultos. Se reprodujo la ruptura estructural con `HTMLParser` de
Python 3.14; no se afirma ejecución de JavaScript ni una explotación observada
en GitHub. La validación de integridad actual no detecta este caso.

**Corrección:** usar escape reversible y canónico para el JSON de todos los
comentarios ocultos, incluido el libro de cobertura.

**Comprobación de cierre:** verificar `-->`, `--!>` y `<!--` en estado, vista y
libro; no debe aparecer contenido HTML visible y la relectura debe conservar
exactamente las rutas y los valores originales.

## TN6 — Medium: metadatos inválidos del modelo atraviesan aceptación y rompen la codificación

Ubicación: `review_domain.py:2005`, `review_domain.py:2088`.

En modo `anchors`, un hallazgo válido con `cause_hint: 42` pasa por
`parse_model_findings`, `modelo_utilizable`, `observation_de_entrada`,
`validar_reporte` y `accept_report`. Al codificar el snapshot resultante se
lanza `ValueError: estado v3 ilegal: cause_hint inválido: 42`.
La frontera de entrada admite un valor que el contrato persistido prohíbe.

**Impacto:** una respuesta JSON del modelo que se considera utilizable puede
interrumpir la publicación. El modo por defecto elimina estos metadatos; el
caso reproducido aplica a la ruta de anclas.

**Corrección:** validar los metadatos opcionales contra el contrato persistido
en la frontera del modelo y devolver rechazo explícito antes de aceptar un
estado imposible de codificar. No ocultar el fallo con una captura genérica.

**Comprobación de cierre:** cubrir cadena válida, ausencia/None y valores
numéricos, listas y objetos; el resultado inválido debe tener un rechazo
controlado y conservar la memoria anterior.

## TN7 — Medium: la recuperación interpreta una página incompleta como ausencia de worker

Ubicación: `review_coordinator.py:347`, `review_coordinator.py:463`,
`review_coordinator.py:656`.

`corridas_por_solicitud` consulta las primeras 100 corridas del workflow sin
paginación y filtra por PR después. Si esas 100 pertenecen a otros PR y el
worker de una solicitud viva está en la siguiente página, `cosechar` devuelve
la solicitud en `sin_corrida`. `decidir` programa de nuevo esa misma solicitud.
La reproducción simula precisamente ese historial y confirma un solo acceso a
la API y el segundo despacho, pese a existir un worker en curso fuera de página.

**Impacto:** con suficiente actividad del repositorio se puede repetir trabajo
y cancelar el worker anterior por su grupo de concurrencia. También se puede
rechazar como desconocido un aviso cuyo run quedó fuera de la primera página.
No se afirma haber observado este incidente en producción.

**Corrección:** consultar por ID el run avisado y resolver la búsqueda de las
solicitudes vivas con paginación o un índice fiable; una búsqueda incompleta
no debe equivaler a ausencia confirmada. Conservar la comprobación de confianza
del worker y del intento al recuperar los resultados.

**Comprobación de cierre:** historial de más de 100 corridas entre varios PR,
worker vivo fuera de la primera página y aviso de un run antiguo; no debe haber
un despacho duplicado ni rechazo por una mera limitación de la consulta.

## Mejoras verificadas y tamaño

- Un único `accept_report`; se eliminaron los motores de fusión duplicados.
- Un único `publish_checkpoint`, con relectura del cuerpo completo y comparación
  de la solicitud viva completa antes de confirmar trabajo.
- Presentación tipada y transporte separados; no reaparece el parser de títulos
  Markdown como fuente de estado.
- Worker y plantilla exportan solo el resultado cerrado.
- `review.py`: 1.318 → 1.063 líneas; coordinador: 1.568 → 1.083;
  render: 606 → 381. El codec nuevo tiene 934 líneas. Ningún módulo productivo
  revisado cruza desde menos de 1.000 a más de 1.000 en este delta.
- Python `review*.py` versionado de la raíz: 8.548 → 8.711 líneas, +163.
  Se cuentan líneas físicas, incluidos comentarios y blancos; no tests ni docs.
  Este dato no mide calidad ni contradice la reducción anterior del archivo
  monolítico: muestra por qué no se debe equiparar esa reducción con eliminación
  neta de código del proyecto. El tamaño no determina el rechazo de esta revisión.

## Validación consolidada

- `bash scripts/check.sh`: **841 pruebas en 103,171 s, OK**; comprobación final
  `check: OK`. Incluye gitleaks, conflictos, actionlint y partición determinista.
- `ruff check --select F review*.py scripts/*.py`: **All checks passed**.
- Las 107 pruebas de la primera pasada quedan subsumidas por la batería completa
  ejecutada en esta ampliación; ya no se depende solo de evidencia anterior.
- Cuatro reproducciones locales: **los cuatro defectos se reprodujeron**. Son
  sondas que afirman la presencia del fallo, no nuevas pruebas de regresión verdes.
  Código: `docs/reviews/thermo-post-deploy-repros.py`.
  Salida: `docs/reviews/thermo-post-deploy-repros.json`.
  Ejecutar con Python 3.14 y PyYAML:
  `python3 docs/reviews/thermo-post-deploy-repros.py`.
- Log completo de checks de esta sesión:
  `/tmp/reviewer-thermo-complete-check.log`.

No se modificó implementación, no se llamó al modelo ni se publicó nada en
GitHub. Se deja un informe consolidado con ubicación, impacto, corrección y
comprobación de cierre por hallazgo. Para aprobar hay que resolver TN1–TN7 y
revisar el impacto de los cambios; volver a ejecutar únicamente los 841 tests
actuales no basta. La auditoría no garantiza que futuras revisiones nunca
encuentren otro defecto.
