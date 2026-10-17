# Continuidad tras una revisión incompleta

## Problema

Una revisión parcial publica el SHA del intento. El siguiente push usa ese SHA para seleccionar únicamente archivos nuevos o modificados. Un archivo que quedó pendiente puede desaparecer del alcance si no cambió en ese push.

El arreglo conserva el comentario fijo, los IDs de hallazgos, los descartes y la deduplicación de eventos. No cambia el modelo ni el presupuesto.

## Comportamiento esperado

| Revisión anterior | Siguiente push |
|---|---|
| Completa, con memoria válida | Revisión incremental (hoy: forzada a completa, decisión T16) |
| Parcial, con libro de cobertura válido | Continuación: solo los archivos del diff cuyo blob ya no coincide con el libro (nuevos, cambiados y pendientes) |
| Parcial, sin libro o con libro inválido | Revisión completa, conservando hallazgos y descartes |
| Sin cobertura verificable en la metadata | Revisión completa para establecer el estado |
| Falla de infraestructura | Se conserva la revisión anterior y su cobertura |

Ejemplo: el bot no termina de revisar `app.py`. El siguiente push solo cambia `other.py`. La siguiente revisión debe incluir ambos archivos. Cuando esa revisión termina completa, el próximo push puede volver al modo incremental.

Reenviar el mismo evento sigue sin repetir el intento. Un re-run manual en el MISMO head tras una parcial con libro válido también continúa (revisa lo pendiente); en cualquier otro caso el re-run pide revisión completa, como antes.

## Forma

El publicador guarda un marcador de cobertura asociado al SHA del intento. Su valor es `complete` o `partial`. La ausencia de un marcador válido significa cobertura desconocida.

El libro de cobertura es una línea oculta propia del comentario fijo, `<!-- ai-review:covered=<sha>:<json {ruta: blob}> -->`, junto al marcador de completion. Cada entrada registra el blob del HEAD con que el bot revisó el archivo COMPLETA. Los topes (400 entradas, 30000 bytes) y cualquier desvío de formato anulan el libro; las rutas con `-->` viajan escapadas dentro del JSON. Cobertura y hallazgos siguen siendo metadata independiente: el libro no altera IDs, descartes ni deduplicación.

Cuando la revisión es parcial, el modelo declara lo que quedó sin revisar en `<!-- ai-review:unreviewed=["ruta", ...] -->` justo antes de COVERAGE (prompt.md). Un archivo leído a medias cuenta como sin revisar. Cubiertos de la pasada = alcance de la pasada − sin revisar − excluidos por presupuesto o encoding. Si la pasada es parcial y el bloque falta o no parsea, nada cuenta como cubierto (fail-closed). Si el modelo declara complete, todo el alcance de la pasada queda cubierto. La publicación recalcula el libro: entradas previas cuyo blob sigue intacto más lo cubierto ahora; los archivos borrados salen del libro. Sin turnos, bloque de hallazgos ausente, excluidos por presupuesto o encoding degradan el marcador a partial como siempre; el marcador sale complete solo cuando el libro cubre cada archivo revisable del diff completo con su blob actual.

`gate` lee esa metadata y la entrega en `prev.json` junto con el SHA, los hallazgos y el libro. `prepare` solo permite la continuación cuando la cobertura anterior es parcial, hay libro válido y la base y la política coinciden con las de la memoria; en ese caso el alcance son los archivos del diff cuyo blob actual no coincide con el libro, la memoria de hallazgos viaja como en una recuperación parcial y la resolución de hallazgos sigue atada al delta real (un hallazgo solo pasa a resuelto si su archivo cambió, nunca por estar en el alcance). Las comprobaciones existentes de rebase y re-run siguen vigentes: tras una revisión COMPLETA el siguiente push sigue siendo `forced-full-t16` (decisión D0); el modo de continuación solo existe después de una parcial.

En la recuperación completa se conserva la lista real de archivos cambiados desde el intento anterior. Esa lista controla si un hallazgo puede pasar a resuelto y si su archivo volvió al contenido base. El alcance completo de la revisión no cuenta como evidencia de que un archivo haya cambiado.

La cobertura y los hallazgos son independientes. Una revisión parcial puede encontrar problemas útiles; esos hallazgos se conservan. Una falla del proveedor no cambia el estado de la revisión anterior.

## Decisión de diseño

Se compararon dos diseños con una evaluación independiente:

- Estado completo o parcial del último intento. Añade una condición de elegibilidad para revisión incremental.
- SHA del último intento y SHA de la última revisión completa. Permite recuperar desde un punto anterior, pero exige distinguir el rango de archivos por revisar del rango usado para resolver hallazgos.

Se eligió el primero por su menor superficie de cambio. Del segundo se conserva la separación explícita entre cobertura y estado de hallazgos. No se introduce una cola por archivo.

El principio Model the Domain separa cobertura, intento y hallazgos. Laziness Protocol limita el arreglo al estado necesario para decidir el alcance.

## Límites aceptados

Una revisión completa después de un corte repite parte del trabajo. Con el libro de cobertura, la repetición se limita a lo pendiente: una parcial con libro válido continúa por lo que falta, en el mismo push siguiente o en un re-run del mismo head. Si el PR sigue superando el presupuesto en cada continuación, puede volver a quedar incompleto; el arreglo no programa reintentos automáticos. Tras una revisión completa, el push siguiente sigue siendo una revisión completa (decisión T16/D0).

Los comentarios anteriores al libro de cobertura requieren una revisión completa en el siguiente push. La ausencia de una advertencia visible no demuestra cobertura completa: el libro es la única cobertura acumulada reconocida. Un parcial que omite el bloque `unreviewed` (o lo entrega malformado) no avanza el libro, aunque el texto diga lo contrario. Un marcador parcial con libro que ya cubre todo produce una continuación sin alcance que acredita complete sin releer nada: la cobertura registrada manda sobre el marcador.

El tope del libro (400 entradas, 30000 bytes) lo dimensiona el límite de 65000 caracteres del comentario de GitHub, no el brief (que sugería 2000 entradas); un diff con más archivos revisables cae a revisión completa en cada push.

El estado completo refleja el protocolo de cobertura del revisor. No demuestra que se hayan detectado todos los errores.

## Pruebas de aceptación

1. Publicar una revisión parcial y preparar el siguiente push incluye el archivo que quedó pendiente aunque no cambie.
2. Conservar los IDs y descartes durante esa recuperación. Un hallazgo en un archivo sin cambios sigue abierto aunque el modelo lo declare resuelto. Un archivo revertido al contenido base resuelve su hallazgo anterior.
3. Una revisión completa posterior restablece el modo incremental (hoy: completa forzada).
4. Cortes por turnos, tamaño, cobertura ausente o memoria del modelo inválida impiden habilitar incremental.
5. Metadata histórica o inválida lleva a revisión completa.
6. Avisos repetidos de infraestructura conservan el estado anterior sin duplicarse.
7. Excluir todo por filtros deliberados no crea una revisión parcial artificial.
8. Parcial que cubre A y B y deja C sin revisar: el siguiente prepare (push que solo cambia D) tiene alcance {C, D}, no el PR entero.
9. La continuación termina complete y el comentario sale complete aunque la pasada no haya visto A ni B.
10. Si en la continuación A cambió (blob distinto), A vuelve al alcance.
11. Parcial sin bloque `unreviewed` o con bloque malformado: nada cubierto, la siguiente pasada mantiene el alcance.
12. El modelo declara complete pero un archivo quedó fuera por presupuesto: parcial y ese archivo no entra al libro.
13. Comentario sin libro o con libro de otro head: revisión completa.
14. Tras una completa, el siguiente push sigue `forced-full-t16` (D0 intacta).
15. Re-run en el mismo head tras una parcial con libro: continuación con el alcance pendiente.
16. Hallazgo abierto en un archivo cubierto y sin cambios sigue abierto aunque el modelo de la continuación lo marque resuelto.
17. Rutas con `-->`, unicode o salto de línea en el libro hacen roundtrip sin romper el comentario.
18. `verify_sticky.py` acepta un comentario con libro y rechaza uno con libro corrupto.

Las pruebas focalizadas corren localmente (tests/test_cobertura_acumulada.py y tests/test_verify_sticky.py). La batería completa y actionlint corren en el PR de implementación.
