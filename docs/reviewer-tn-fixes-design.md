# Diseño TN1–TN7: fronteras consistentes y publicación tipada

Fecha: 2026-11-15. Estado: **diseñado; no implementado**. Base: `d1858c0c1dd67b5a726f9ec09318e88a778a6e08`. Ejecutor futuro: Devin; revisión final antes del merge: Codex. [Plan continuo y entrega](plans/2026-11-15-reviewer-tn-fixes.md). [Hallazgos y reproducciones](reviews/2026-11-15-thermo-post-deploy.md).

Fases de `pstack:architect`: Ground completado; Sketch con dos candidatos y síntesis completado; Agree es esta entrega documental solicitada; Implement reservado a Devin; Scrap solo ante evidencia de incompatibilidad con los invariantes. Los bocetos siguientes son contratos en Markdown, no archivos ejecutables pendientes de rellenar.

## Problema y restricciones del sistema existente

Los siete hallazgos cruzan fronteras que ya tienen dueño: configuración, entrada del modelo, almacenamiento, recuperación y publicación. El cambio termina esas fronteras sin cambiar el almacenamiento ni volver a diseñar el coordinador. Se conservan schema 3, vista versionada, comandos, IDs, default `current`, presupuestos y publicación confirmada por relectura completa.

| Subsistema | Flujo y responsabilidad comprobados |
| --- | --- |
| Directo, `review.py` | Lee memoria → normaliza → recoge Git/permisos → aplica descartes → acepta → construye documento → prepara → publica. Hoy el paso aceptar devuelve un dict con un bloque codificado que el caller vuelve a leer. El adaptador conserva la política propia de Keep. |
| Dominio, `review_domain.py` | Valida observaciones, decide identidad/estado, migra memoria y codifica. `encode_snapshot` puede quitar evidencia de hallazgos cerrados bajo presión; nunca sus anclas o causa. No se duplica esa decisión. |
| Documento, `review_document.py` | Posee vista, saneamiento, codificación completa, ajuste de capacidad y lectura de integridad. El renderer usa el estado efectivamente codificado, incluso después de compactación/redacción. |
| Sticky/render/GitHub | `review_sticky` enmarca cobertura; `review_render` produce texto; `review_github.publish_checkpoint` comprueba cuerpo y solicitudes antes de confirmar. Ningún nuevo escritor. |
| Coordinador | Admite solicitudes, persiste antes del despacho, cosecha artifacts autenticados y decide recuperación. Solo una búsqueda completa puede demostrar ausencia de worker; un callback no demuestra que su run sea el más nuevo. |
| Action y workflows | Los pasos son procesos con entornos separados. `politica_de_revision` interpreta `current → titles` y `anchors`; el YAML debe entregar el mismo input a cada paso. |

Presupuestos actuales: estado v3 **40.000 bytes UTF-8**; comentario **60.000 bytes y 60.000 caracteres**; formato legado **8.000 bytes**; cap inicial de una explicación **8.000 caracteres**. No confundirlos ni cambiarlos en este trabajo.

### Por qué se conserva esta arquitectura

La investigación `pstack:why` usa el historial local y los documentos de diseño, no atribuye acuerdos a PRs no consultados:

- `f53bd033` y `c6e462dd` describen extracciones de módulos sin cambio de comportamiento; explican el origen de la fachada aún consumida por tests. La migración pendiente se termina, no se reemplaza por otra fachada.
- `538bcf7d` documenta identidad por anclas optativa y validación de evidencia; el fix no convierte afirmaciones del modelo en pruebas ni activa anclas por defecto.
- [Arquitectura de correcciones](reviewer-corrections-architecture.md) conserva memoria en el sticky para evitar otro almacén y `contents: write`. [Coordinador convergente](coordinador-convergente.md) exige persistir antes de despachar y reconoce el límite anterior de 100 runs. TN7 retira esa limitación, no crea un índice persistente.
- D3 `4db77cc` y D4 `20e59f0`, [diseño anterior](reviewer-simplification-design.md) y evidencia de cierre exigen conservar vistas tipadas. No se propone retorno operativo a un escritor que las destruya.
- `tests/fixtures/parsear_bot_g.py` representa al consumidor externo de schema 3. Se usa intacto para comprobar compatibilidad.

## Uso desde los llamadores

```python
# Adaptador directo: la aceptación no devuelve JSON ni proyecciones de render.
accepted = build_findings(result, manifest, sticky, repo, pr, login, comments)
if isinstance(accepted, DirectAccepted):
    candidate = review_document.document_from_result(
        previous_document, accepted.snapshot,
        {**result, "model_ok": accepted.model_ok}, manifest, provider, head,
        accepted.new_ids, covered=covered_book,
    )
    prepared = review_document.prepare_publication(candidate, budget, secrets)
    if isinstance(prepared, review_document.StateCapacityExceeded):
        # Una sola vuelta al documento ORIGINAL, con failure state-kept.
        # Misma política que domain.Keep, nunca volver al candidato rechazado.
        prepared = preparar_conservado(previous_document, prepared, ...)
else:  # domain.Keep
    prepared = preparar_conservado(previous_document, accepted, ...)
# La escritura y reviewed=true siguen dependiendo de publish_checkpoint.
```

`preparar_conservado` ilustra una rama/helper privado del adaptador, no otro API de publicación; incluye la salida sin escritura cuando no existe sticky válido. Se puede implementar con control de flujo directo. El coordinador continúa llamando `prepare_publication(plan.document, ...)` después de reconciliar, sin aceptar el reporte otra vez.

```python
# Coordinador: la búsqueda desconocida no se convierte en lista vacía completa.
history = corridas_por_solicitud(repo, pr)
if isinstance(history, RunSearchUnknown):
    # Sin entrega ni sin_corrida derivados de esa búsqueda; pendientes intactas.
    return [], [], False
# Atribuir callback por ID, comprobar run más nuevo/attempt y confianza,
# y cosechar mediante las operaciones existentes.
```

```python
# E1 y tests: usar directamente el dueño, con presupuesto explícito.
from review_domain import parse_model_findings, read_snapshot, serialize_findings
load = read_snapshot(previous_body)
block = serialize_findings(legacy_state, max_bytes=400)
```

## Tipos, firmas y mapa de propiedad

Firmas esquemáticas; `...` representa argumentos existentes, no una obligación de añadir un servicio o copiar estos cuerpos al runtime.

```python
# review.py: los únicos datos consumidos por la publicación directa.
@dataclass(frozen=True)
class DirectAccepted:
    snapshot: review_domain.Snapshot
    new_ids: tuple[str, ...]
    model_ok: bool

def build_findings(...) -> DirectAccepted | review_domain.Keep:
    raise NotImplementedError

def aceptar_reporte_directo(...) -> DirectAccepted | review_domain.Keep:
    raise NotImplementedError

# review_domain.py: una regla de escape, utilizada por tres codecs existentes.
def escape_hidden_json(serialized: str) -> str:
    raise NotImplementedError

def observation_de_entrada(entry) -> Observation | ObservationRejected:
    raise NotImplementedError

# review_document.py: identifica SOLO desborde inicial anterior a redacción.
@dataclass
class StateCapacityExceeded(domain.CapacityExceeded):
    pass

def prepare_publication(document, budget=None, secrets=()) -> (
    PreparedPublication | domain.CapacityExceeded | DocumentBlocked
):
    # StateCapacityExceeded es subtipo de CapacityExceeded.
    raise NotImplementedError

@dataclass(frozen=True)
class ExplanationSlot:
    index: int  # generado internamente desde enumerate(explanations)

ProseSlot = Literal["review", "historical", "failure"] | ExplanationSlot

def _reduce_prose(document, slot: ProseSlot, block, budget) -> ReviewDocument:
    raise NotImplementedError

# review_coordinator.py: tipos locales, nunca nuevos campos persistidos.
@dataclass(frozen=True)
class RunSearchComplete:
    by_request: dict[int, tuple[dict, ...]]

@dataclass(frozen=True)
class RunSearchUnknown:
    reason: str

def corridas_por_solicitud(repo, pr) -> RunSearchComplete | RunSearchUnknown:
    raise NotImplementedError
```

Los dicts de runs quedan dentro del adaptador existente; no cruzan al dominio. `review_domain` no importa documento/sticky; sticky puede importar el escape del dominio; documento usa ambos; render no importa documento en runtime. No se agrega módulo genérico `utils`, índice de runs ni clase de servicio. `EncodedCheckpoint` mantiene su contrato actual: la relectura interna de `_render` ya obtiene el estado efectivo y sigue siendo válida. La única conversión eliminada por TN2 es la usada para intercambiar aceptación entre funciones del CLI.

## C1 / TN4 — Configuración única en entornos separados

Asignar `FINDING_IDENTITY: ${{ inputs.finding_identity }}` en prepare, run y publish. Mantener `politica_de_revision` como intérprete único y el default `current`. No usar export de shell de un paso esperando heredarlo en otro. No cambiar el modo coordinado ni agregar otro flag.

Cierre: cargar YAML real y construir entornos independientes para input omitido/current/anchors; comparar política y digest en el flujo completo. La entrada inválida falla antes de llamar al modelo. La prueba con anclas debe comprobar identidad y descartes, no solo valores de env. La documentación del input debe coincidir con su comportamiento efectivo.

## C2 / TN6 — Validar antes de construir observaciones

En `observation_de_entrada`, aceptar `cause_hint` y `symbol_hint` declarados únicamente como `str | None`. Validar hints de anclas primarias y relacionadas declaradas de forma consistente, sin empezar a persistir campos que antes se ignoraban. No usar `str(valor)` para volver válido un dato inválido. Reutilizar `ObservationRejected(motivo=...)` con el campo identificado; mantener las validaciones persistidas como segunda frontera, sin copiar el motor de aceptación.

`parse_model_findings` ya valida antes de eliminar metadatos, por lo que el rechazo de forma también aplica a `current`. Es un cambio deliberado para entradas malformadas; las entradas válidas conservan semántica. El reporte mixto sigue aceptando observaciones válidas bajo las reglas de parcialidad existentes. Una observación inválida no crea/reemplaza un hallazgo, no consume IDs por aceptación ni acredita cobertura; no obliga a revertir descartes autorizados independientes. Conservar `rechazadas_forma`, el aviso de reporte no utilizable y la cobertura pendiente en ambos adaptadores.

Cierre: matriz de ausencia/null/cadenas/número/bool/lista/objeto, reporte solo inválido y mixto, previo abierto/resuelto/descartado. Probar hasta documento publicable en modo directo y cosecha coordinada: sin excepción, hallazgos previos preservados, cobertura no falsamente completa y sin acreditar entradas rechazadas en el libro. No inventar una prohibición nueva de publicar una revisión parcial.

## C3 / TN5 — Una regla de escape reversible

`escape_hidden_json` recibe JSON ya serializado y neutraliza los tres delimitadores mediante escapes JSON reversibles. Fijar esta forma canónica: `<!-- → \\u003c!--`, `--> → --\\u003e`, `--!> → --!\\u003e` (en el JSON emitido, cada escape tiene una sola barra invertida). No usar escape HTML ni saneamiento de prosa en rutas de identidad. El helper se invoca directamente desde escritor v3 del dominio, `_json` del documento y `libro_a_linea`; no añadir wrappers que solo reenvíen.

Cada dueño conserva framing, opciones `ensure_ascii`, límites y validación. Los lectores siguen aceptando representaciones antiguas. Los codecs de escritura histórica v1/v2 mantienen su contrato y no se reescriben como parte de esta migración. Los cambios de bytes de payloads con delimitadores son intencionales; identidad, valores decodificados y hashes del cuerpo final deben ser correctos. No exigir bytes idénticos para una vulnerabilidad que precisamente cambia el escape.

Cierre: tres delimitadores y combinaciones en estado/vista/libro, Unicode, backslashes literales y tres ciclos de escritura/lectura sin doble escape. La salida no contiene delimitadores crudos dentro del payload. Con Python 3.14, `HTMLParser` no debe ver tags ni texto del JSON como contenido visible. En intérpretes cuyo parser no reconoce `--!>`, la aserción directa sobre delimitadores sigue siendo obligatoria: su silencio no demuestra seguridad. Recalcular tamaños después del escape y verificar el parser externo sin modificar su fixture.

## C4 / TN7 — Descubrimiento completo o desconocido

Consultar `repos/{repo}/actions/workflows/ai-review-worker.yml/runs?per_page=100` **sin filtros** `event`, `branch`, `status`, `created` o similares. Paginar todas las respuestas con `gh api --paginate --slurp` y filtrar localmente evento, PR y solicitud. La API limita a 1.000 resultados las búsquedas con esos filtros; paginar una URL con esos filtros no elimina ese límite. [Documentación oficial de runs](https://docs.github.com/en/rest/actions/workflow-runs#list-workflow-runs-for-a-workflow). `--slurp` envuelve las páginas en una única matriz JSON. [Manual de gh api](https://cli.github.com/manual/gh_api).

Contrato de búsqueda:

1. El proceso de listado tiene timeout total de **60 s**, pasado a `review_github.sh`/`subprocess.run`; no reintentos internos ilimitados. Al agotarse devuelve Unknown. No imponer un límite de páginas que se interprete como fin correcto.
2. Validar matriz de páginas, `workflow_runs`, `total_count` entero no negativo y forma mínima de registros; IDs/attempts positivos y datos suficientes para clasificar. Un registro inequívocamente ajeno puede ignorarse; un registro malformado que podría ocultar un worker relevante hace la búsqueda Unknown. Deduplicar por run ID; un duplicado o totales cambiantes que impidan demostrar cobertura completa produce Unknown. No convertir salida parcial tras error en Complete.
3. Complete exige proceso exitoso, respuestas válidas y agotamiento consistente: número de IDs únicos igual al total coherente observado, o un mecanismo de fin igualmente verificable documentado en la implementación. Para esta entrega usar la igualdad contra totales coherentes; no dejar abierta la decisión. Ordenar runs por ID descendente como hoy. Esto es evidencia observada, no un snapshot transaccional de GitHub.
4. Consultar el run avisado por ID con timeout de **15 s**. Comprobar evento, workflow, título/PR/solicitud y attempt, manteniendo después la autenticación actual de ref/SHA/artifact. Un error/404 es desconocido; un run verificado de otro PR/workflow es rechazo demostrado. Un callback no encontrado en un listado supuestamente completo convierte la evidencia combinada en desconocida; no autoriza un atajo para publicarlo.
5. Seleccionar el más nuevo confiable con las reglas actuales. Un run nuevo en curso impide consumir el artifact de otro viejo completado. Refrescar por ID el run seleccionado antes de cosechar para usar su attempt vigente; reutilizar el lookup del callback si corresponde a ese mismo run. Un aviso de attempt 1 nunca marca terminado attempt 2. Callback de solicitud podada/desconocida conserva las reglas existentes; no puede sustituir una solicitud viva distinta.
6. Timeout, red, JSON inválido, total inconsistente o refresh fallido no generan `sin_corrida`. Con búsqueda global Unknown se difieren todas las entregas dependientes de ese historial; con refresh fallido de un run se difiere esa solicitud. No se marca el worker como rechazado solo por falta de evidencia. Mantener pendientes y registrar la razón saneada; se vuelve a intentar en un evento posterior o recuperación manual existente.
7. Solo Complete y ausencia real permiten redespachar pendientes no fallidas. Las solicitudes fallidas conservan su política actual. Los comandos y solicitudes nuevas explícitamente admitidas pueden avanzar por persistencia/confirmación normales; no usan ausencia inferida. No añadir cron, almacenamiento ni garantía de ejecución exactamente una vez.

Cierre: más de 100 y más de 1.000 runs con el requerido fuera del prefijo; mezcla de PR/eventos; ausencia real; callback antiguo frente a run nuevo; attempt desfasado; trust inválido; página interrumpida, timeout, JSON defectuoso y totales en movimiento. Medir el efecto: dispatch y aceptación de artifact, no solo tamaño de un dict. Los artifacts ambiguos y expirados mantienen los rechazos actuales. El precio de Unknown es diferir trabajo, nunca inventar ausencia para asegurar progreso.

## C5 / TN2 — De aceptación tipada a preparación

`build_findings` y `aceptar_reporte_directo` devuelven `DirectAccepted` o el `Keep` existente. `MemoryBlocked` se adapta a Keep con su motivo. Eliminar `block`, `merged`, `completion`, `no_acreditados` y su cálculo cuando solo sostienen el dict antiguo; `new_ids` y `model_ok` sí tienen consumidores. El caller entrega `accepted.snapshot` a `document_from_result`. No codificar ni releer en esa frontera.

La codificación inicial dentro de `prepare_publication` devuelve `StateCapacityExceeded` únicamente cuando el estado original, aun compactado por el codec, no cabe **antes de redactar secretos**. El desborde de la segunda codificación, tras redacción, sigue devolviendo `domain.CapacityExceeded` ordinario, igual que el cuerpo. No inferir el origen comparando límites numéricos. El coordinador ya trata la clase base como fallo de publicación y conserva ese comportamiento con el subtipo; el directo distingue el subtipo primero.

| Caso | Comportamiento que se conserva |
| --- | --- |
| Keep o desborde inicial de candidato | Volver al documento original; agregar/reemplazar `FailureView(state-kept)` sin filtración de descartes/cursor/estado tentativos. Preparar fallback una sola vez. |
| Sin sticky previo válido ante Keep | No crear sticky; summary saneado; `reviewed=false`; salida actual de conservación. |
| Fallback conservado cabe | Publicarlo con el escritor confirmado; mantener memoria/cabecera/vista/libro previos, más el fallo. No marcar el head nuevo como revisado; conservar la salida normal actual. |
| Estado cabe antes de redacción, pero luego desborda | Ruta existente de aviso de capacidad, `reviewed=false`, exit 1; no convertir en Keep. |
| Cuerpo desborda o fallback también desborda | Aviso de capacidad existente cuando sea posible, checkpoint preservado, salida de fallo; sin recursión de fallback. |
| Documento futuro/corrupto o estado redactado inválido | Respetar las salidas actuales de bloqueo/no escritura; nunca empezar desde memoria vacía. |
| Escritura/relectura incierta | `Unconfirmed`; sin confirmación del head ni trabajo. |

`encode_snapshot` conserva su compactación determinista: solo evidencia de hallazgos cerrados bajo presión. La lectura dentro del codec/render y la relectura del documento completo son fronteras legítimas y permanecen. `document_from_result` deriva cabecera/IDs/avisos de campos que esa compactación no cambia; el renderer obtiene el estado efectivo del bloque. Comprobar esta coherencia mediante un caso que solo quepa compactado, incluyendo redacción. No ampliar `EncodedCheckpoint` con otra copia mutable del estado para eliminar una lectura de integridad existente.

Cierre: matriz v1/v2/v3 con IDs, estados, cursores, solicitudes, cobertura y prosa; estado compacto, overflow inicial/tras redacción/cuerpo, fallback sin espacio y memoria futura/corrupta/ausente. Comparar cuerpo, summary, código de salida y `reviewed`; las diferencias permitidas son únicamente las correcciones funcionales anteriores.

## C6 / TN3 — Reducir prosa una sola vez

Separar saneamiento inicial y reducción de presupuesto dentro de `review_document`. `_with_notice` devuelve siempre documento: eliminar sus ocho ramas imposibles. `_reduce_prose` recibe solo uno de los slots finitos, obtiene su texto, añade el aviso correcto, busca longitud y reemplaza de forma explícita. Un `match` local sobre tipos/literales es suficiente. Una closure local para `_fit_slot` es admisible; no un registro de callbacks, reflexión, `getattr` o rutas de atributos arbitrarias.

Orden contractual, conservando el momento de cada aviso:

1. Normalizar/codificar estado; redacción actual; sanear prosa y campos visibles.
2. Omitir libro con rutas que contienen secretos; emitir aviso. Validar máximos de explicaciones/avisos con la semántica actual; cap de explicación a 7.999 caracteres más elipsis cuando excede 8.000.
3. Omitir libro que supera su límite propio. Renderizar; si el contenido fijo con todas las prosas vacías tampoco cabe con libro, omitirlo anticipadamente.
4. Reducir prosa de revisión y luego histórico, solo mientras siga desbordando.
5. Omitir libro restante cuando todavía no cabe.
6. Reducir explicaciones en orden de tupla y después prosa de fallo.
7. Comprobar bytes/caracteres; si no cabe, CapacityExceeded. Si cabe, relectura integral y PreparedPublication con summary derivado.

Conservar `rstrip`, elipsis, fallback de longitud cero, búsqueda binaria, warning de revisión versus notice, deduplicación y política de últimos tres avisos. Los índices de ExplanationSlot son internos; no se reordena ni elimina la tupla durante este recorrido. La reducción nunca cambia memoria, IDs, rutas o solicitudes.

Cierre: cuerpo y summary idénticos antes/después del refactor sobre C5 para la misma entrada/presupuesto; multibyte, tres avisos, tres explicaciones, histórico, fallo, libro y caso sin ReviewView. Secuencia persistida adicional: comentario válido → fallo de capacidad → mismo fallo (sin aviso/PATCH redundante) → reporte no utilizable (conserva aviso) → reporte utilizable (retira aviso de capacidad, conserva avisos de comandos). Repetir desborde de estado comprueba que `state-kept` no se acumula.

## C7 / TN1 — Migrar antes de borrar

Inventariar imports, `review.nombre`, parches y Python embebido en workflows. Migrar a `review_context`, `review_domain`, `review_sticky`, `review_render`, `review_config` o `review_github` según propiedad real. Cambiar los parches al lookup que de verdad usa el consumidor; preferir llamadas calificadas al módulo dueño para evitar mantener exports accidentales.

Eliminar `review.__all__`, imports usados únicamente como compatibilidad, `parse_findings_block`, wrapper `serialize_findings` y constante local de presupuesto legado cuando ya no tengan consumidores. Leer legado con `domain.read_snapshot` y su variante Legacy; usar presupuesto explícito del codec en pruebas. Mantener funciones que sí orquestan el adaptador directo. E1 importa `parse_model_findings` del dominio; su seed y exportación no cambian.

Cierre: inventario posterior sin consumidores del API retirado; pruebas de módulos afectados y seed E1 con mismos IDs/anclas/estado/modo/diff. No introducir aliases equivalentes, `__getattr__`, otra fachada de tests ni copies del algoritmo legado. El test del CLI puede seguir importando el CLI para probar sus comandos reales.

## Síntesis, alternativas y riesgos aceptados

Se compararon dos formas completas: **A**, terminar contratos en dueños existentes con resultado directo tipado; **B**, mover aceptación, descartes y fallback a una operación nueva `review_document.prepare_report`. Se eligió A: conserva la separación entre decisión del adaptador y codec, añade menos contratos y deja el coordinador estable. De B se toma el refresco del run elegido por ID para comprobar attempt y la distinción explícita entre desbordes inicial/post-redacción. Ambos candidatos identificaron la necesidad de snapshot efectivo; se conserva la lectura de frontera existente en lugar de ampliar el resultado del codec.

| Criterio de diseño (juicio, 0–2) | A | B |
| --- | --- | --- |
| Semántica de conservación y confianza explícita | 2 | 2 |
| Menos superficie nueva para cerrar siete hallazgos | 2 | 1 |
| Propiedad coherente con dominio/documento/adaptadores | 2 | 1 |
| Compatibilidad y verificabilidad por unidades | 2 | 2 |
| Elimina capas y duplicación sin framework | 2 | 1 |

B oculta más decisiones al caller, pero introduce presentación/descartes/disposición como contratos nuevos y dos entradas de preparación con políticas distintas en el codec. No aporta suficiente beneficio para esta tarea. También se rechazan índice persistente de runs (schema/operación extra), lookup solo por callback (no prueba cuál run es más nuevo) y pipeline genérico de slots (esconde decisiones detrás de callbacks).

Criba de red flags: un dueño por invariante, ninguna fachada nueva, ningún escritor adicional, callbacks solo locales, ninguna lista persistida duplicada. Python no impide importar internals: no prometer encapsulación que el lenguaje no ofrece. No hay cuota de líneas; evitar crecimiento que solo reparta la complejidad. Los modelos configurados de otros proveedores no están disponibles entre los agentes de esta sesión: se usaron dos candidatos independientes del anfitrión; no se afirma diversidad de modelos ni juez entre proveedores. Codex hizo la síntesis; la revisión documental independiente consta en la [evidencia del diseño](evidence/reviewer/tn-fixes-design.md).

Se acepta el costo de enumerar historia y diferir cosecha cuando no puede acreditarse completitud, a cambio de evitar redispatch basado en datos incompletos. Se acepta un subtipo de capacidad para conservar una diferencia operativa existente. Se acepta cambiar la representación de delimitadores para cerrar el escape, manteniendo valores y lectores. Ninguna de estas decisiones requiere una consulta rutinaria a Gon.

Riesgos que la implementación debe resolver con evidencia: historial que excede el timeout (Unknown explícito), redacción que expande estado, diferencias en el parser HTML por versión, parches de tests que aún dependan de la fachada y prosa al límite con avisos. Si una corrección exige otro schema o cambiar reglas de aceptación/confianza, Devin registra el conflicto y continúa lo independiente; no inventa una migración. El primer paso implementable es C1 del plan, después de fijar base y worktree. El siguiente paso de Codex tras recibir el trabajo será revisar el conjunto por SHA, no mergear por tests verdes.
