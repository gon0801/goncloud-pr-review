# T16: segunda ronda del piloto coordinado (detenida)

Fecha: 2026-10-10. Continúa `T16-piloto.md`. La ronda 1 dejó el modo
coordinado sin capa visible; esta ronda lo reactivó en el repo central con la
capa visible (#79), el respaldo de proveedor (#81), el bloque sin cierre (#82)
y los pendientes de activación (#83).

## Instalación

- Instalador: `ACTION_SHA=9d84183062419f79cc03ae05dd95372887b6aaaf
  scripts/install.sh --coordinado gon0801/goncloud-pr-review`. Abrió el #84
  (head `ea893ff`), con el worker fijado a `ref: 9d84183…`. Antes no existía
  `chore/ai-review`, así que la rama salió de la punta de `main`.
- El operador mergeó el #84 con squash (`24d63e1`): el modo coordinado quedó
  activo en el central.
- PR de prueba: #85 (`piloto/t16-casos-2`), con el mismo `piloto/caso_t16.py`
  de la ronda 1 (división por cero, índice fuera de rango y `eval`).

## Casos

| Caso | Resultado | Evidencia |
|---|---|---|
| 1. Push → worker → publicador | Comentario visible con veredicto, hallazgos, cobertura y SHA; bloque de memoria primero; costo en el resultado. **Pierde 5 de 6 hallazgos.** | Push `2527b12`; worker 38017166195 ($0.1433, 12 turnos, opencode-go, 6 observaciones); publicador 38017235160; comentario 6092753914 con solo F1 Critical |
| 2. Descarte concurrente | `ai-review: descartar F1` (comentario 6092784703) mientras corría el worker 38017336748 ($0.1210, 10 turnos). Estado final consistente: F1 descartado con recibo, `command_cursor=6092784703`, sin solicitudes pendientes, generación 3; el visible se re-renderiza con «Descartados (1)». **Pierde 5 de 6 otra vez, y el `eval` descartado vuelve como F2 con otro título.** | Push `dd2482c`; publicadores 38017343264, 38017351876, 38017392629 |
| 3. Explicar | No ejercido (piloto detenido) | — |
| 4. Re-revisar el mismo SHA | No ejercido (piloto detenido) | — |
| 5. Casos T05 | No ejercidos (piloto detenido) | — |
| 6. Retorno | Ejecutado por el operador en el #86 (abajo). La ruta del instalador estaba rota tras el squash merge (#87). | `cc981f3` |

## Defectos

1. **Pérdida de hallazgos (bloqueante, motivo del paro).** `accept_report`
   comparaba cada observación también contra los hallazgos creados en el mismo
   reporte. El worker entrega anclas legadas; sin ancla verificada,
   `match_finding` toma el único previo no descartado del archivo, que a partir
   de la segunda observación era el F recién creado. Ese id no estaba en
   `por_id` y un `continue` la tiraba. Repro con las 6 observaciones reales:
   6 → 1. La ruta directa usa `merge_findings` y no tenía el defecto. Fila
   T16-piloto-2 pérdida, arreglada con fixture de las 12 observaciones reales.
2. **El gate del revisor directo no leía la memoria v3 (F2 de #86).** Tras el
   retorno, `cmd_gate` solo entendía el bloque legado: con la memoria del #85
   el modelo recibía «Sin hallazgos previos». Arreglado en el mismo PR.
3. **La identidad no viajaba al worker (UW-r6 N3).** El worker parseaba con su
   propio `FINDING_IDENTITY`. Hoy el coordinador opera en `current`, así que
   esto no causó la pérdida; ahora la identidad viaja por la solicitud.
4. **El worker revisa sin memoria (abierto, bloquea la ronda 3).** Nadie
   escribe `prev.json` en el worker (lo hace `gate`, que no corre ahí): el log
   dice `full/no-prev`, el modelo emite `F-new` y reescribe los títulos. Con
   identidad por título, cada título reescrito entra como «posible duplicado»
   y un descartado con otro título reaparece. Es la causa del segundo efecto
   del caso 2.
5. **Retorno del instalador tras un squash merge (#87).** `chore/ai-review`
   quedó en `ea893ff`, divergente de `main`; el commit de retorno salía sobre
   esa punta y su merge chocaba (modify/delete en `ai-review.yml`), dejando el
   conjunto coordinado en `main`. Comprobado con `git merge-tree`.
6. **Drenado incompleto en el retorno.** Ver abajo.

## Retorno (#86, operador)

- **Paso 1, detener y drenar.** El último run del publicador terminó a las
  02:45:04Z y el #86 se mergeó a las 02:46:24Z. Pero el worker 38017921567,
  despachado a las 02:42 para el #87, seguía en vuelo: terminó tras el merge y
  su aviso al publicador falló con `HTTP 422: Workflow does not have
  'workflow_dispatch' trigger`. Su resultado se perdió. El procedimiento ahora
  pide comprobar runs `in_progress` y `queued` del worker justo antes de
  mergear.
- **Paso 3, checkpoint.** Después del merge, las dos memorias se leen como
  válidas y tienen un solo comentario con el marcador:
  - #85: schema 3, generación 3, `complete_claim`, 2 hallazgos (F1 descartado,
    F2 abierto), sin solicitudes pendientes.
  - #87: schema 3, generación 1, `unknown`, sin hallazgos, una solicitud
    `pending` huérfana (la del worker perdido). El revisor directo la conserva
    por diseño; no se despacha porque no hay coordinador.
- El #86 repone `ai-review.yml` idéntico al de antes de #84 (`git diff
  24d63e1^ -- .github/workflows/` vacío).

## Costo

Dos corridas del worker en el #85, con opencode-go: $0.1433 (12 turnos) y
$0.1210 (10 turnos), **$0.2643** en total. El worker del #86 y
el del #87 corrieron durante la ventana activa y no se suman al piloto.

## Decisión

El modo coordinado no se despliega. La ronda 3 exige los arreglos de
T16-piloto-2 en `main` (pérdida, gate, identidad, memoria del worker,
instalador) y repetir los seis casos completos.
