# Continuidad tras una revisión incompleta

## Problema

Una revisión parcial publica el SHA del intento. El siguiente push usa ese SHA para seleccionar únicamente archivos nuevos o modificados. Un archivo que quedó pendiente puede desaparecer del alcance si no cambió en ese push.

El arreglo conserva el comentario fijo, los IDs de hallazgos, los descartes y la deduplicación de eventos. No cambia el modelo ni el presupuesto.

## Comportamiento esperado

| Revisión anterior | Siguiente push |
|---|---|
| Completa, con memoria válida | Revisión incremental |
| Parcial | Revisión completa, conservando hallazgos y descartes |
| Sin cobertura verificable en la metadata | Revisión completa para establecer el estado |
| Falla de infraestructura | Se conserva la revisión anterior y su cobertura |

Ejemplo: el bot no termina de revisar `app.py`. El siguiente push solo cambia `other.py`. La siguiente revisión debe incluir ambos archivos. Cuando esa revisión termina completa, el próximo push puede volver al modo incremental.

Reenviar el mismo evento sigue sin repetir el intento. Un re-run manual solicita revisión completa, como antes.

## Forma

El publicador guarda un marcador de cobertura asociado al SHA del intento. Su valor es `complete` o `partial`. La ausencia de un marcador válido significa cobertura desconocida.

`compose` clasifica la cobertura con las mismas condiciones que generan el aviso de revisión incompleta. Ambas formas de comentario publican el marcador antes del texto recortable.

`gate` lee esa metadata y la entrega en `prev.json` junto con el SHA y los hallazgos. `prepare` solo permite revisión incremental cuando la cobertura anterior es completa y la memoria es válida. Las comprobaciones existentes de rebase y re-run siguen vigentes.

La cobertura y los hallazgos son independientes. Una revisión parcial puede encontrar problemas útiles; esos hallazgos se conservan. Una falla del proveedor no cambia el estado de la revisión anterior.

## Decisión de diseño

Se compararon dos diseños con una evaluación independiente:

- Estado completo o parcial del último intento. Añade una condición de elegibilidad para revisión incremental.
- SHA del último intento y SHA de la última revisión completa. Permite recuperar desde un punto anterior, pero exige distinguir el rango de archivos por revisar del rango usado para resolver hallazgos.

Se eligió el primero por su menor superficie de cambio. Del segundo se conserva la separación explícita entre cobertura y estado de hallazgos. No se introduce una cola por archivo.

El principio Model the Domain separa cobertura, intento y hallazgos. Laziness Protocol limita el arreglo al estado necesario para decidir el alcance.

## Límites aceptados

Una revisión completa después de un corte repite parte del trabajo. Si el PR sigue superando el límite de tamaño, puede volver a quedar incompleto. El arreglo no programa reintentos automáticos ni garantiza terminar un PR que excede el presupuesto.

Los comentarios anteriores a esta metadata requieren una revisión completa en el siguiente push. La ausencia de una advertencia visible no demuestra cobertura completa.

El estado completo refleja el protocolo de cobertura del revisor. No demuestra que se hayan detectado todos los errores.

## Pruebas de aceptación

1. Publicar una revisión parcial y preparar el siguiente push incluye el archivo que quedó pendiente aunque no cambie.
2. Conservar los IDs y descartes durante esa recuperación.
3. Una revisión completa posterior restablece el modo incremental.
4. Cortes por turnos, tamaño, cobertura ausente o memoria del modelo inválida impiden habilitar incremental.
5. Metadata histórica o inválida lleva a revisión completa.
6. Avisos repetidos de infraestructura conservan el estado anterior sin duplicarse.
7. Excluir todo por filtros deliberados no crea una revisión parcial artificial.

Las pruebas focalizadas corren localmente. La batería completa y actionlint corren en el PR de implementación.
