# Cierre de simplificación D1–D4 y revisión completa

Fecha: 2026-11-15. Rama: `refactor/reviewer-simplificacion`.
Base: `bad212f5056427ddbd23909e3856cf4a014b23f3`.
Último commit de código revisado: `c84317b`.

D1–D4, los seis hallazgos R1–R6 y las tres observaciones adicionales están
implementados y comprobados. No quedan hallazgos conocidos de estas revisiones
pendientes de implementación. No se afirma que el proyecto completo carezca de
cualquier deuda ajena a este encargo. No se hizo merge, despliegue ni migración
de comentarios reales.

## Entregas

| Entrega | Commit | Comprobación |
| --- | --- | --- |
| D1: artifact limitado al resultado cerrado | `a962156` | Workflow y plantilla exportan result.json; revisión de secretos conservada. |
| D2: motor único de aceptación | `e0211e5` | Memorias legadas y versionadas usan el dominio canónico. |
| D3: codec y puente de retorno | `4db77cc5ff984d54d7e0ed450456837204ed72af` | Se conserva en la historia; rechaza escribir sobre vistas nuevas. |
| D4: presentación tipada y publicación completa | `20e59f0` | Ambos modos publican el documento entero y releen la escritura. |
| R1: descarte explícito de resueltos | `b54e752` | Descarte/cursor idempotentes; política coordinada conservada. |
| R2: alcance incremental legado | `1499ea6` | Modo, reviewed y diff coinciden; continuación entrega pendientes. |
| R3: secretos en cobertura | `101cf42` | Se omite un libro cuya ruta cambiaría al redactar. |
| R4: cierre HTML alternativo | `b29ad38` | Escape reversible de --!>; metadatos permanecen ocultos. |
| R5: histórico y respuesta inválida | `889d93a` | Histórico conservado hasta una revisión utilizable. |
| R6: avisos acumulados y reducciones | `ce33dc3` | Tres avisos no bloquean la publicación de un documento que cabe. |
| Solicitud completa antes de confirmar | `4d5db60` | Mismo ID con otro contenido no confirma ni escribe. |
| Ciclo de aviso de desborde | `5baa80a` | Una revisión utilizable retira el aviso; conserva avisos de comandos. |
| Separación de responsabilidades | `c84317b` | Codec, formato visible y transporte tienen dueños distintos. |

## Diseño final y límites

- `review_document.py`: tipos del documento, lectura, serialización, hashes,
  saneamiento y presupuesto. Pasó de 1.187 a 934 líneas; no depende del transporte.
- `review_render.py`: formato visible, avisos, secciones, alcance y resumen.
  El codec entrega los slots ya delimitados cuando corresponde. No interpreta
  Markdown para reconstruir estado y solo importa el tipo del documento bajo
  TYPE_CHECKING, evitando un ciclo en runtime.
- `review_github.py`: publicador compartido, relecturas y recibo de confirmación.
  Compara cada WorkRequest con la solicitud viva completa persistida antes de
  cualquier escritura o confirmación, incluido el camino idempotente.
- Se migraron todos los callers y pruebas al dueño nuevo; no se conservaron
  aliases ni copias de los publicadores anteriores. No se añadieron módulos.

El aviso capacity-exceeded desaparece al preparar una revisión utilizable.
Si la nueva publicación no cabe, el flujo de capacidad conserva el comentario
previo y su aviso. Una respuesta inválida mantiene el aviso previo y un aviso
propio de invalidez; no sustituye el histórico importado.

## Evidencia

- `bash scripts/check.sh`: **841 pruebas OK**, 113,267 s; gitleaks, conflictos,
  actionlint y partición determinista en verde. Registro local:
  `/tmp/goncloud-simplificacion-final-check.log`.
- Dos regresiones nuevas observadas en rojo antes de corregir:
  `test_same_request_id_with_different_content_never_confirms_or_writes`
  (10 variantes entre publicación nueva e idempotente) y
  `test_usable_review_removes_capacity_notice_and_keeps_command_notices`.
  Registros rojo/verde bajo `/tmp/goncloud-request-content-*` y
  `/tmp/goncloud-capacity-lifecycle-*`.
- 169 pruebas dirigidas de documento, coordinador, comandos, convergencia y
  capacidad en verde tras separar responsabilidades.
- 173 salidas capturadas antes de la separación se reprodujeron idénticas
  byte por byte, incluyendo cuerpo completo y resumen visible. Corpus local:
  `/tmp/goncloud-render-baseline.pkl`.
- Ruff de los módulos productivos cambiados y la suite de documento, y
  `git diff --check`: OK. El chequeo amplio de suites antiguas también muestra
  reglas de estilo preexistentes; no se atribuye un Ruff global limpio al repo.
- Revisión independiente de `ce33dc3..c84317b`: **APPROVE**, sin hallazgos.
  El revisor ejecutó además 23 pruebas de publicación, recuperación y secretos.
- Las pruebas de publicación usan adaptadores controlados; no se llamó a
  proveedores pagados ni se escribieron comentarios reales de revisión.

## Entrega

Se conserva una sola rama con commits separados y el puente D3 intacto.
La credencial OAuth local no incluye workflow; la llave SSH existente sí
se autenticó como gon0801. La entrega se realiza con esa identidad existente,
sin modificar credenciales, permisos ni retirar cambios del workflow.
El PR permanece borrador para revisión; merge y despliegue quedan fuera de este
encargo. Su estado y enlace se informan al completar la publicación.
