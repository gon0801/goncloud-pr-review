# Diseño de simplificación del revisor para Devin

Estado: implementado; cierre y validación en [evidencia final](evidence/reviewer/simplificacion-cierre.md). Fecha: 2026-11-15.
Base inspeccionada: `bad212f5056427ddbd23909e3856cf4a014b23f3` en `main` local.
Ejecutor D1–D4 y R1–R6: Devin. Cierre de observaciones y revisión: Codex.
Devin ejecuta D1–D4 de corrido, en commits separados; Codex revisa el conjunto al
final. Las unidades y la versión puente son límites técnicos, no aprobaciones
intermedias. Ante un bloqueo de contrato se continúa con el trabajo independiente.
El usuario autorizó posteriormente la implementación y el cierre de todas las
observaciones. No se migraron comentarios ni se desplegó en consumidores.
Ejecución: [plan por entregas](plans/2026-11-15-reviewer-simplification.md).

La estructura final conserva tipos, codec y presupuesto en `review_document.py`;
el formato visible vive en `review_render.py` y el publicador compartido
`publish_checkpoint` vive en `review_github.py`. Los bocetos siguientes registran
el contrato del diseño; la evidencia final detalla la implementación.

## Problema y restricciones comprobadas

La extracción de módulos redujo `review.py` de 4.708 a 1.318 líneas desde
`a07e390`, pero Python de producción en la raíz pasó de 8.463 a 8.548.
El objetivo es eliminar decisiones duplicadas y dependencias del formato visible;
no se fija una cuota de líneas borradas.

| Hallazgo | Evidencia en la base | Consecuencia |
|---|---|---|
| Dos motores de hallazgos | `review.merge_findings` y `review_domain.accept_report` | F1 resuelto + observación abierta + delta vacío: v1 reabre, v3 conserva resuelto. |
| Presentación usada como memoria | `estado_visible`, `encadenar`, búsquedas de encabezados y separadores | Los cambios de texto y el transporte de comandos comparten reglas de parsing. |
| Exportación demasiado amplia | Los dos workflows de worker suben todo `ai-review/` | Una llave ficticia en `litellm.log` sobrevive a `close-result` y queda incluida en el conjunto exportable. No se observó una filtración real. |

La revisión anterior ejecutó `bash scripts/check.sh`: 802 pruebas en verde,
gitleaks, conflictos y actionlint. Esa evidencia corresponde a la base, no a los
fixes futuros.

El historial explica parte de la forma actual. `538bcf7` introdujo aceptación F0
que conservaba el legado con `Keep`; `9c93844` añadió el escritor compatible de
v2/v3. La coexistencia viene de esa migración gradual; esto se deduce de los
commits locales, sin atribuir acuerdos a discusiones de PR no consultadas.
[La arquitectura anterior](reviewer-corrections-architecture.md) eligió memoria
en el comentario para evitar otra base de datos y `contents: write`.
[El coordinador convergente](coordinador-convergente.md) exige un escritor por PR,
comandos idempotentes, cosecha autenticada y persistencia antes del despacho.

Un consumidor externo está representado por la copia congelada
`tests/fixtures/parsear_bot_g.py`: acepta únicamente `schema == 3`. Sus pruebas
están en `ElParserDeGleeElStickyNuevo`. No se modifica esa copia para justificar
un formato incompatible.

## Uso desde los llamadores

Estos ejemplos son bocetos de contratos, no código que deba ejecutarse hoy.

```python
# Publicador directo: el formato antiguo se convierte en la frontera.
memory = memoria_operativa(read_snapshot(sticky_body))
# MemoryReady(snapshot schema3) o MemoryBlocked(motivo, bloque_original).
decision = accept_report(memory.snapshot, plan, validated_report)
# Descartes y permisos conservan su política de modo; la fusión es única.

# Coordinador: transforma datos, luego prepara UNA publicación.
document = read_document(observed.body)
updated = actualizar_presentacion(document, final_snapshot, cambios_visibles)
publication = prepare_publication(updated, storage_budget, secrets)
receipt = publish_checkpoint(publication, observed, github,
                             work_after_commit=plan.trabajo)
dispatch_confirmed(receipt, despachar=dispatch_worker)

# Worker: conserva su contrato de cierre; el workflow exporta un solo archivo.
cmd_close_result(args)  # deja result.json final con paquete y manifest mínimo
# upload-artifact.path = ${runner.temp}/ai-review/result.json
```

`MemoryBlocked`, `DocumentBlocked` y `CapacityExceeded` no permiten acceder a
un estado vacío por defecto. El llamador conserva el comentario y no despacha.
Los ejemplos omiten esos branches de error por brevedad; son parte obligatoria
de la integración.

## Un solo motor de hallazgos

Se reutilizan `Finding`, `Snapshot`, `Observation`, `RepositoryFacts`,
`ReviewPlan`, `ValidatedReport`, `accept_report` y `aplicar_descartes`.
No se añade otra representación de hallazgos ni un segundo algoritmo de merge.

```python
# review_domain.py; firmas propuestas, cuerpos pendientes.
@dataclass(frozen=True)
class MemoryReady:
    snapshot: Snapshot  # normalizado a schema 3; identidad de IDs preservada

@dataclass(frozen=True)
class MemoryBlocked:
    reason: str
    original_block: str | None

def memoria_operativa(load) -> MemoryReady | MemoryBlocked:
    raise NotImplementedError

# Firmas existentes que siguen siendo las operaciones de negocio.
def accept_report(current, plan, report):
    raise NotImplementedError
```

La nueva frontera absorbe `_estado_actual`: `Missing` crea memoria vacía;
`Legacy` usa su `snapshot` convertido a v3 mediante `snapshot_a_v3`; v2 usa la
misma conversión;
v3 conserva solicitudes, recibos, generación y contadores; `Invalid`/`Future`
bloquean. No se inventan revisión, blobs, cobertura completa ni anclas.
`Missing` usa los valores actuales de `_estado_actual`: schema3, generation=1,
revision=None, next_id=1, completion=UNKNOWN, cursor=0 y colecciones vacías.
Los escritores directos pasan a persistir v3 únicamente después de aceptar y
comprobar capacidad. Los lectores v1/v2 permanecen. Si migrar no cabe, se conserva
el cuerpo anterior: nunca se elimina información para obligar la conversión.
El lector legado actual sanea y puede descartar entradas o recortar datos antes
de construir `Legacy.snapshot`. Por eso el control de pérdida se compara también
con el JSON original de `Legacy.block`, no solo con `raw` ya saneado: IDs, estados,
contador, cursor, títulos, rutas, líneas y todas las rutas relacionadas persistidas.
Si no hay conversión fiel, bloquear con motivo sin reasignar IDs ni reescribir
silenciosamente esos datos. Probar entradas descartadas, títulos/rutas largos y
una sexta ruta relacionada. No se reinterpreta la memoria del modelo como memoria
persistida ni se relajan los validadores de entrada del modelo.

La política elegida es la aceptación conservadora del dominio, con estas reglas
explícitas para evitar que Devin intente conservar dos comportamientos:

| Caso | Resultado común requerido |
|---|---|
| Descartado reaparece, con ID o misma identidad | Sigue descartado; el modelo no lo revive. |
| Resuelto + modelo dice abierto + ningún cambio pertinente | Sigue resuelto. Cambio deliberado respecto a v1. |
| Resuelto + modelo dice abierto + cambio pertinente | Se reabre con el mismo ID. |
| Abierto + modelo dice resuelto | Solo se resuelve con cambio pertinente verificado, incluidos archivos de arreglo relacionados. |
| Hallazgo previo omitido | Se conserva; una reversión verificada conserva las reglas actuales del dominio. |
| Observación nueva dice resuelto | Nace abierta, no acredita resolución. Cambio deliberado respecto al descarte silencioso de v1. |
| Dos observaciones reclaman el mismo previo | Conservar política v3: primera actualiza; segunda queda separada con indicación de posible duplicado. |
| Se revierte una ruta con una observación nueva | No resolver automáticamente un ID creado en esta pasada; limitar el cierre por reversión a IDs previos. |
| Modelo sin bloque utilizable / runtime incompleto | No acreditar cobertura completa; conservar memoria y descartes. |

La coincidencia `current`/`titles` y la de `anchors` siguen siendo políticas de
identidad del mismo motor. No se crean switches `legacy_merge` o `compat_mode`.
Para un match por títulos en la misma ruta, una ubicación `AnchorLegacy` puede
actualizar su línea desde la observación; una ancla localizada verificada no se
reemplaza por una legada. Incluir prueba de desplazamiento de líneas, porque
el motor v1 actualiza la línea y el v3 actual retiene el ancla legada anterior.

La resolución por reversiones de anclas relacionadas conserva la semántica v3
existente para previos; no se rediseña la identidad dentro de este trabajo.
Las diferencias documentadas entre comandos directos y coordinados (momento de
aplicación, IDs inexistentes, avisos) continúan en sus adaptadores; unificar la
fusión de hallazgos no autoriza cambiar esa interfaz de usuario.

Se eliminan `merge_findings`, el branch de fusión legada de `build_findings` y
`apply_dismissals` cuando sus callers usen `aplicar_descartes`. Los tests migran
a sus módulos dueños en los puntos tocados; no se conserva una fachada de
compatibilidad solo para evitar actualizar imports de tests.

## Documento estructurado, en el mismo comentario

Se conserva el bloque de hallazgos v3 exactamente compatible con Glee. Se añade
un registro de presentación con versión propia junto a él. Ambos viajan en un
solo cuerpo y un solo PATCH: no hay un segundo comentario ni otro almacén.
La prosa larga se guarda una sola vez, en regiones explícitas del cuerpo visible;
no se duplica dentro del JSON oculto.

```python
# review_document.py, nuevo: tipos y protocolo del documento persistido.
@dataclass(frozen=True)
class SafeProse:
    text: str  # saneado una vez al entrar; no contiene delimitadores activos

@dataclass(frozen=True)
class ReviewView:
    head_sha: str
    provider: str
    new_ids: tuple[str, ...]
    scope: ScopeView  # contadores, hasta 40 exclusiones, consumo y modo
    warnings: tuple[ReviewWarning, ...]  # códigos del programa, no Markdown
    prose: SafeProse

@dataclass(frozen=True)
class ExplanationView:
    finding_id: str
    prose: SafeProse

@dataclass(frozen=True)
class Notice:
    comment_id: int
    kind: NoticeKind  # no-entendido, extra, inexistente, todo-tardio
    finding_ids: tuple[str, ...]

@dataclass(frozen=True)
class ReviewDocument:
    memory: Snapshot
    reviewed: ReviewedHeader | None  # SHA/completion realmente publicados
    covered: CoverageBook | None
    review: ReviewView | None
    explanations: tuple[ExplanationView, ...]
    notices: tuple[Notice, ...]
    failure: FailureView | None  # head fallido + motivo, no avanza reviewed
    historical: SafeProse | None  # vista antigua opaca, sin autoridad

@dataclass(frozen=True)
class PreparedPublication:
    body: str
    state_block: str
    body_digest: str
    generation: int  # la del estado final codificado

def read_document(body: str) -> ReviewDocument | DocumentBlocked:
    raise NotImplementedError

def prepare_publication(document, budget, secrets) -> PreparedPublication | CapacityExceeded:
    raise NotImplementedError

def publish_checkpoint(publication, observed, adapter, *, work_after_commit=()):
    # PublishReceipt con ese trabajo solo al confirmar; si no, Unconfirmed.
    raise NotImplementedError
```

Los nombres `ScopeView`, `ReviewWarning`, `ReviewedHeader`, `CoverageBook`,
`NoticeKind` y `FailureView` se materializan con campos concretos derivados de
`scope_lines`, `compose` y avisos actuales. No son `dict` arbitrarios. Scope guarda
totales y la muestra que se muestra, no el manifest completo; provider es una
clave de `PROVIDERS`; headers requieren SHA válido y complete/partial.

Formato propuesto, con nombres literales estables:

```text
<!-- ai-review:sticky -->
<!-- ai-review:sha=<head> -->                       # solo con revisión publicada
<!-- ai-review:completion=<head>:<estado> -->        # mismo orden actual
<!-- ai-review:covered=<head>:<json> -->             # opcional, formato actual
<!-- ai-review:findings <JSON schema 3 actual> -->
<!-- ai-review:view <JSON version 1> -->
... título, veredicto y secciones generados ...
<!-- ai-review:slot=review -->
... prosa literal saneada ...
<!-- ai-review:end-slot=review -->
... hasta tres explicaciones con slots explanation-F<n> ...
... slot history solo durante la migración de una vista antigua ...
```

El dibujo del bloque findings es esquemático: se usa literalmente el prefijo y
codec actuales, no se cambia su gramática. El registro view contiene
`version: 1`, hash SHA-256 del bloque de estado codificado, metadatos tipados de
review/explanations/notices/failure y la lista de slots con sus hashes. Prosa,
hallazgos, veredicto y títulos generados no se duplican en ese JSON.
También persiste `reviewed` (SHA/completion acreditados o null) y el hash de la
línea covered final, o null si no hay libro. En documentos nuevos `reviewed` es
la autoridad; las líneas SHA/completion son su proyección exacta y el lector
rechaza divergencias. `ReviewView.head_sha` debe coincidir con `reviewed.head_sha`.
El libro debe coincidir con su hash y con ese head. La revisión del dominio puede
ser diferente mientras hay trabajo pendiente: no copiarla a la cabecera ni
invalidar por esa sola diferencia. Un fallo nuevo conserva `reviewed` anterior.
En la importación antigua se usan solo cabeceras válidas; ausencia/cobertura
ilegible no se eleva a complete y se conserva como falta de acreditación.

Reglas del protocolo:

1. View se acepta solo en la posición de cabecera posterior al bloque de memoria.
   Cualquier marcador reservado activo view/slot/end-slot fuera de posición,
   incompleto o huérfano bloquea; solo ausencia total permite importar legado.
   Bloques duplicados, versión desconocida, hash incorrecto, slots ausentes,
   repetidos o no declarados producen `DocumentBlocked`. No se reinterpretan
   como un comentario antiguo. Los slots tienen IDs internos, nunca rutas.
2. Las regiones se leen por delimitadores propios y hashes, nunca por títulos
   españoles, emojis, ` · ` o regex de veredicto. Títulos y estilo se pueden cambiar
   sin modificar el lector. Neutralizar marcadores en prosa antes de delimitarla;
   escapar `-->`/`--!>` reversiblemente dentro del JSON, como el codec actual.
3. Hash de slot: SHA-256 del UTF-8 exacto entre sus dos líneas delimitadoras,
   sin las líneas delimitadoras; fijar una única política de saltos LF en el codec.
   Hash de estado: UTF-8 exacto del bloque final, después de compactación/redacción.
   Son comprobaciones de consistencia; la autenticidad sigue viniendo del login
   del bot y de la validación del worker, no del hash.
4. `reviewed` conserva el SHA y cobertura de la última publicación; ni una
   explicación, un descarte ni un fallo los avanzan. La línea «en curso» se deriva
   de solicitudes pendientes. Nuevos IDs se guardan como IDs, avisos como códigos
   y explicaciones como datos; se conservan los límites actuales de tres avisos,
   tres explicaciones y 8.000 caracteres por explicación.
5. El reducer de presentación consume juntos resultados, explicaciones y avisos
   de una corrida. Se aplica el mismo orden actual: resultados aceptados en orden
   de cosecha, última revisión vigente como revisión visible, explicaciones por
   ID con la última sustituyendo la anterior, avisos de la corrida al final.
   Se renderiza una sola vez. Se eliminan los callbacks `visible` y `encadenar`.

`SafeProse` se construye al recibir texto del modelo o importar texto histórico,
aplicando redacción/escapado en orden fijo. El decoder valida la región persistida
y la devuelve como SafeProse, sin volver a escapar entidades; render solo la
inserta. Tres ciclos leer-renderizar deben conservar el texto byte a byte. No
añadir un booleano `already_escaped` que cada llamador deba recordar.

### Comentarios antiguos y retorno de versión

Sin view, `read_document` importa cabecera y memoria con los lectores existentes
y conserva el resto visible como un slot `history` opaco, saneado y rotulado
«Revisión anterior importada». No extrae nuevos, avisos ni explicaciones leyendo
encabezados antiguos. Los hallazgos actuales se muestran desde el dominio; los
IDs nuevos históricos empiezan vacíos y las explicaciones antiguas siguen
legibles dentro del histórico. Esta es una diferencia visual deliberada y evita
mantener el parser antiguo indefinidamente. Un reporte nuevo utilizable reemplaza
el histórico; comandos y fallos intermedios lo conservan. Si la memoria antigua
es inválida se conserva el comentario entero sin escribir. La importación nunca
infiere completitud, identidad o comandos a partir de texto del modelo.

La migración se hace al siguiente evento que realmente necesita escribir, no
mediante barrido ni al leer. Si no cabe, `Keep`; si el PATCH falla, no se despacha.
La vista se considera migrada solo después de confirmar el cuerpo completo.

Antes de activar el escritor nuevo se entrega una versión puente (entrega D3).
Esta entiende el registro view y sus regiones, pero su publicador antiguo
**rechaza escribir cualquier comentario que ya tenga view**, incluso inválido
o de versión desconocida. Permite leer memoria para diagnóstico. Esa versión es
el destino de retorno soportado tras D4: detiene nuevas publicaciones conservando
datos, no promete seguir revisando. Volver a una versión anterior al puente queda
fuera del retorno soportado: su codec v3 ignora metadatos adicionales.
No se inventa un rollback automático ni un comando que borre view.

### Capacidad, secretos y publicación

Se mantienen 40.000 bytes para el bloque de estado y 60.000 bytes/caracteres para
el comentario entero. Se mide UTF-8 **después** de serializar, escapar y renderizar.
Orden de ajuste: compactación existente de hallazgos cerrados; recorte de prosa
con aviso; omisión del libro de cobertura si es necesario; reducción de prosa de
explicaciones con aviso conservando ID y presencia. No se eliminan hallazgos,
solicitudes, recibos o metadatos obligatorios. Si el fijo no cabe, se conserva el
comentario previo y no se avanza cursor, SHA, generación ni despacho.
El render usa el snapshot realmente codificado si hubo compactación:
`prepare_publication` relee el bloque que devolvió `encode_snapshot` y usa ese
snapshot validado para render y hashes. No necesita otro compactador. Los recortes
incluyen la prosa histórica con aviso; no afectan la memoria que la acompaña.

La redacción de valores conocidos se realiza antes de producir bloques y hashes;
el resultado final debe seguir pasando el lector. No se aplica un reemplazo
posterior que invalide hashes o JSON. Se prueban secretos ficticios tanto en prosa
como en metadatos. La protección de logs de stdout en general no forma parte
de la garantía del artifact mínimo; no se afirma haber auditado proveedores reales.

`publish_checkpoint` deja de confirmar por igualdad exclusiva del bloque de
hallazgos. Relee el comentario autenticado y compara el cuerpo preparado completo
(o hash más igualdad exacta); un cambio solo de presentación también se escribe.
Si el cuerpo ya coincide, confirma sin PATCH. Si cambió respecto a la observación
con la que se calculó la decisión, devuelve no confirmado y deja la convergencia
para el siguiente evento. PATCH/POST incierto no permite despacho. Se conservan
el rechazo de stickies duplicados y la recuperación por relectura.
El digest incluye el cuerpo final completo; no se usa su propio digest dentro
del cuerpo. La ausencia de una vista publicable no se confunde con confirmación.
El trabajo a despachar se pasa explícitamente como `work_after_commit`; cada
solicitud debe coincidir con una solicitud viva del estado codificado. No se
reconstruye como «todas las pendientes». El recibo confirmado devuelve ese trabajo
y la generación de la publicación; cualquier fallo devuelve `Unconfirmed` sin
trabajo. La ruta directa y los cambios solo visuales pasan una tupla vacía.

## Artifact mínimo

En `.github/workflows/ai-review-worker.yml` y `templates/ai-review-worker.yml`,
`upload-artifact.path` será únicamente `${{ runner.temp }}/ai-review/result.json`
y `if-no-files-found: error`. Se conservan nombre por request/run/attempt,
retención y autorización. `close-result` sigue produciendo el paquete final y
su redacción; no se crea otro comando de empaquetado.

`artefacto_del_run` sigue aceptando el archivo raíz nuevo y las carpetas antiguas
con un único `result.json` durante el tránsito de workers. No se debilitan
`authenticate_result` ni el rechazo de múltiples resultados. Tras acotar ambas
exportaciones se retira el saneamiento de `repair.json`/`cierre.json` que existía
solo para subirlos. Permanecen locales, igual que logs, diff y contexto.
`e1-measure.yml` es otro consumidor: conserva su exportación explícita de
`result.json` y `manifest.json`; no se altera ni se usa como precedente para
volver a subir la carpeta del worker de producción.

## Dueños y eliminaciones

| Módulo | Responsabilidad después del cambio |
|---|---|
| `review_domain.py` | Memoria normalizada, identidad, aceptación y transiciones. Conserva codec de hallazgos existente. |
| `review_document.py` (nuevo) | Documento completo, tipos de presentación, importación histórica, delimitadores, consistencia y preparación publicable. |
| `review_render.py` | Render desde datos y saneamiento; sin reconstruir datos desde su propia salida. |
| `review_sticky.py` | Cabecera/libro y adaptación a prev.json; usa lector de documento donde necesite la vista. |
| `review.py` | Adaptador directo: reúne hechos/permisos, llama al único motor y publica el documento. |
| `review_coordinator.py` | Convergencia, autenticación, una publicación, despacho confirmado. Sin parser de presentación ni callbacks de re-render. |

`review_document` puede importar `review_render`; este no debe importar en runtime
al primero (anotaciones bajo `TYPE_CHECKING`, o parámetros de datos). Se evita
un ciclo. No se crean módulos `load/validate/save` para cada fase temporal.
Eliminar del coordinador las implementaciones de `estado_visible`,
`revision_visible`, `encadenar` y los reconocedores de avisos actuales al migrar
todos sus callers. Conservar solo la lógica histórica indispensable en el
importador legado, con pruebas propias. Actualizar scripts consumidores, no
dejarlos usando wrappers obsoletos.

## Decisión de síntesis y alternativas

Se comparan dos formas completas con `architect`/`arena`: A integra presentación
en un checkpoint de nueva versión; B conserva memoria v3 y añade presentación
versionada dentro del mismo documento. Se elige B por la compatibilidad demostrada
con el consumidor de merges y por guardar la prosa una sola vez. De A se toma
que la unidad de confirmación sea el documento entero y que el retorno de versión
falle conservando datos. No se copia un segundo motor de dominio.

El coste aceptado de B es un codec de documento con migración explícita y una
versión puente. A ofrecería un solo bloque autocontenido, pero obliga a cambiar
consumidores externos o duplicar la memoria como proyección compatible; duplicar
prosa también consumiría el presupuesto del sticky. Un almacén Git/base de datos
añadiría permisos y operación que el historial ya descartó. Seguir buscando
encabezados con más regex conserva el problema de propiedad de los datos.

Los modelos de la configuración local no están disponibles en esta sesión:
se usan dos candidatos independientes del modelo anfitrión. No se afirma una
revisión entre proveedores; la síntesis la decide Codex. El plan incluye revisión
de implementación por SHA y evidencias, no aprobación automática por tests verdes.

Comparación de diseño (juicio de Codex, escala 0–2; no medición de rendimiento):

| Criterio | A: checkpoint v4 | B: documento con memoria v3 |
|---|---:|---:|
| Invariantes sin pérdida | 2 | 2 |
| Eliminación de motores/parsers duplicados | 2 | 2 |
| Compatibilidad y retorno | 0: rompe consumidor externo | 1: exige puente |
| Interfaz pequeña y capacidad útil | 1: prosa duplicada/cuotas nuevas | 2: prosa única |
| Verificación y entrega independiente | 1: requiere otro repositorio | 2: fixtures locales existentes |

Se rechaza de A el cambio adicional de resolución por anclas relacionadas y la
omisión de nuevos claims resueltos: se conserva la política v3 declarada en la
tabla, acotando los cambios al contrato necesario. Ambos candidatos revisaron
después el borrador como contrato de ejecución; se incorporaron el transporte
explícito del trabajo, la detección de pérdida previa al saneamiento, el snapshot
efectivo compactado, el bloqueo de marcadores huérfanos y la coherencia de cabecera.

## Riesgos y siguiente paso

Los riesgos que debe resolver la implementación con evidencia son crecimiento
de metadatos cerca de capacidad, importación de comentarios hostiles/antiguos y
la equivalencia de resultados tras normalizar v1/v2. Las decisiones de semántica,
formato y retorno están fijadas arriba; desviaciones materiales vuelven a Codex
con un caso reproducible y una propuesta antes de activar un escritor diferente.

La primera entrega de Devin será el artifact mínimo, independiente de la
migración de memoria, siguiendo D1 del plan.
