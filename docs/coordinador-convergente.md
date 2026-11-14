# Coordinador convergente

## Problema

GitHub guarda un run en curso y uno pendiente por grupo de concurrencia; un
tercero reemplaza al pendiente. Hoy cada run del coordinador atiende solo su
evento, así que el evento reemplazado se pierde: un push que nadie admite, un
resultado del worker que nadie publica (la solicitud queda `pending` para
siempre) o un comando que nadie aplica. Además un `descartar todo` retenido se
aplica tarde a hallazgos que no existían, y cualquier comentario crea un sticky
sin texto visible.

Restricciones que se conservan: un solo escritor por PR (el grupo), la memoria
en el sticky (schema 3), `authenticate_result` sin aflojar, el worker sin
permisos de escritura y el texto no confiable lejos del shell, rutas,
`GITHUB_ENV` y el estado.

## Uso

Cualquier run del coordinador deja el PR en el mismo estado, sin importar qué
evento lo despertó:

| Evento | Antes | Ahora |
|---|---|---|
| comentario `ai-review:` | solo comandos | comandos, cosecha, admisión |
| push | solo admitir el head del evento | comandos, cosecha, admisión |
| aviso del worker | solo publicar ese run | comandos, cosecha, admisión |
| recuperación manual | re-despachar | comandos, cosecha, revisión nueva |
| comentario sin `ai-review:` o en un issue | corre el script | no corre (filtro del `if:`) |

## Forma

Datos:

- `Snapshot.findings_born_after: int = 0`: mayor id de comentario que ya
  existía cuando aparecieron los hallazgos más nuevos. Se persiste solo si es
  mayor que 0, así los bloques viejos no cambian. Un `descartar todo` de un
  comentario con id menor o igual se rechaza con aviso visible.
- Invariante: a lo sumo una solicitud de revisión viva por PR. Crear una
  (push, admisión, `revisar`, recuperación) poda las demás; el grupo
  compartido del worker ya cancela sus runs, así que ninguna corrida cancelada
  pertenece a una solicitud viva.
- Cada solicitud se despacha una sola vez. Reintentar es crear otra.
- `CorridaDelWorker(request_id, run_id, attempt, terminada, conclusion, meta)`:
  el run más nuevo y confiable de cada solicitud, leído de la lista de runs del
  worker. El `run-name` del worker pasa a `PR <n> solicitud <id>` para que un
  run sin artifact también se atribuya a su solicitud.

Orden de un run del coordinador (`cmd_reconcile`):

1. Lee comentarios, sticky y estado. Head y base: los del evento en un push;
   en el resto, los vivos por la API del PR (llamada que ya existía), junto
   con abierto, borrador y repo del head.
2. Comandos: `procesar_comandos` sobre todos los comentarios sin procesar
   (el cursor lo hace idempotente), en cualquier evento.
3. Cosecha: para cada solicitud `pending` (y la del run avisado) busca su
   corrida. Con artifact del attempt vigente lo baja, lo autentica igual que
   hoy y lo acepta. Terminada sin artifact (o el run avisado, cuyo `avisar`
   corre después de `execute`): `ReportFailed` reintentable con motivo, que se
   ve como el aviso de fallo de siempre. En curso o sin corrida: espera. Un
   run no confiable (otro workflow, otra rama, SHA fuera de main) o un
   artifact que no autentica no cambia el estado y deja el run del
   coordinador en rojo, como hoy.
4. Admisión: el head (el del evento en un push; el vivo en el resto, solo si
   el PR está abierto, no es borrador y viene del mismo repo) que no está
   revisado ni tiene una solicitud de revisión viva recibe una solicitud
   nueva. La recuperación manual siempre crea una. Un resultado vencido ya no
   crea trabajo: solo se descarta, y la admisión decide.
5. Si aparecieron IDs de hallazgo nuevos, relee comentarios y fija
   `findings_born_after`.
6. Publica una vez: nada si el estado no cambió, y nunca crea un sticky sin
   texto visible. Una revisión pendiente sin revisión publicada muestra la
   línea "Revisión en curso". Después despacha.

Plantillas: el job del coordinador filtra comentarios (`issue.pull_request` y
`startsWith(comment.body, 'ai-review:')`) y la concurrencia pasa al job, para
que un run filtrado no ocupe ni reemplace el lugar pendiente del grupo. Sale
el disparador `workflow_run` (un run despachado con `GITHUB_TOKEN` no lo
dispara) y el paso de descarga: el script baja lo que cosecha.

Llamadas a la API añadidas por run, solo cuando hay solicitudes pendientes: 1
lista de runs del worker; por corrida candidata, 1 lista de artifacts; por
artifact, la descarga (`gh run download`, la de hoy); la confianza del SHA (1
más 1 por SHA ancestro, como hoy). Hoy el aviso pedía además los metadatos del
attempt; ahora salen de la lista. Cuando aparecen hallazgos nuevos, 1 relectura
de comentarios.

## Decisión de síntesis

Delegado sin arena: comparé dos formas completas a mano.

- A (elegida): convergencia por run, con cosecha por lista de runs.
- B: dejar cada evento como está y añadir un barrido programado (cron) que
  re-despache lo atorado. Pierde: otro disparador por repo, latencia de
  minutos, y sigue perdiendo comandos y pushes entre barridos.

De B se toma solo que la recuperación manual sigue existiendo como martillo.

## Tradeoffs aceptados

- Aceptamos re-renderizar el cuerpo con `estado_visible` también en el push a
  cambio de que el sticky nunca quede vacío.
- Aceptamos que reabrir o marcar listo un head ya revisado ya no lo re-revise
  (`ai-review: revisar` lo hace) a cambio de que repetir un evento no despache
  dos veces.
- Aceptamos rechazar un `descartar todo` retenido en cuanto hubo hallazgos
  nuevos, aunque fueran pocos, a cambio de no tener que fechar cada hallazgo.
- Aceptamos que un fallo de explicación siga sin aviso visible (como hoy).

## Alternativas consideradas

- Lista de artifacts del repo en vez de lista de runs: los ids de solicitud se
  repiten entre PRs y un run sin artifact no se puede atribuir.
- Pedir el `request_id` en el aviso del worker: atribuye el run avisado, pero
  no un fallo cuyo aviso también se perdió.
- Head vivo también en el push: arregla el re-run de un push viejo, pero
  depende de que la API del PR ya muestre el head nuevo cuando llega el evento
  (sin confirmar).

## Riesgos y suposiciones

- Concurrencia por job: un job con `if:` falso no entra al grupo. Inferido de
  la documentación de `jobs.<id>.concurrency`; se confirma en vivo.
- Un run recién despachado aparece en la lista en segundos. Si no, la
  solicitud espera al siguiente evento (no se re-despacha ni se marca fallida).
- Un worker en curso al actualizar las plantillas conserva el `run-name` viejo:
  su resultado no se cosecha y la solicitud espera a `revisar` o al siguiente
  push.
- Residuo de la carrera del `descartar todo`: un comentario escrito entre la
  relectura del paso 5 y el PATCH (menos de un segundo) se aplica a los
  hallazgos nuevos.
