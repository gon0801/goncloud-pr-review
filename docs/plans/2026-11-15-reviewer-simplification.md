# Plan de implementación para Devin y revisión de Codex

Fecha: 2026-11-15. Estado: **D1–D4 y R1–R6 implementados; observaciones finales corregidas**.
Repositorio: `gon0801/goncloud-pr-review`, rama base `main`.
Base del diseño: `bad212f5056427ddbd23909e3856cf4a014b23f3`.
Contrato: [diseño de simplificación](../reviewer-simplification-design.md).

Devin ejecutó D1–D4 y R1–R6 de corrido, con commits separados. Codex revisó
el resultado y cerró las tres observaciones restantes. Las instrucciones
siguientes conservan el plan ejecutado. El estado verificable de entrega y
las pruebas están en la [evidencia final](../evidence/reviewer/simplificacion-cierre.md).
No se autoriza merge, despliegue o migración masiva.

## Arranque y forma de entrega

1. Lee `AGENTS.md`, el diseño y este plan en tu checkout. Registra
   `git rev-parse HEAD` y `git status --short`. Trabaja desde `main` actualizado
   en una rama propia, sin sobrescribir cambios ajenos. Si la base avanzó, compara
   los módulos de esta entrega con el SHA del diseño y registra diferencias.
2. Conserva la evidencia que siga siendo válida. La base inspeccionada pasó
   802 pruebas y `scripts/check.sh`; antes de entregar código ejecuta las
   comprobaciones del cambio sobre tu SHA. No hace falta repetir primero toda
   la batería de la base si no estás investigando una diferencia.

Orden: **D1 → D2 → D3 → D4**. D1 es técnicamente independiente; se implementa primero
porque cierra la exportación amplia con un diff pequeño. D3 depende de D2, y D4
depende de D3. Usa una rama `refactor/reviewer-simplificacion` y un solo PR borrador
al terminar. Haz commits separados por unidad y conserva el SHA del puente D3;
no aplastes el historial antes de la revisión. Cada unidad continúa sobre la
anterior verificada, sin esperar aprobación de Codex. No ejecutes cambios
concurrentes sobre los mismos módulos. Las secciones «Revisión Codex» son el
alcance de la revisión final, no puntos de parada.

Resuelve de forma autónoma decisiones locales, errores de implementación,
fallos de pruebas y ajustes de imports. Si una incompatibilidad exige cambiar un
contrato del diseño, registra reproducción, impacto y propuesta; detén solo esa
parte y continúa lo independiente. No solicites confirmación rutinaria ni declares
terminada una parte bloqueada. No hagas cambios fuera de alcance para sortearla.

Por entrega: resumen de comportamiento, SHA completo, diff base→head, pruebas
nuevas con su motivo, salida de comprobaciones y riesgos pendientes. Guardar la
evidencia breve en `docs/evidence/reviewer/simplificacion-D<n>.md`, sin secretos
ni logs crudos del proveedor. No marcar `done` por terminar de escribir código.

## D1 — Exportar únicamente el resultado final

**Promesa:** el artifact del worker contiene solo `result.json` cerrado y
saneado; el coordinador consume workers nuevos y antiguos con la misma validación.

Puede tocar: `.github/workflows/ai-review-worker.yml`,
`templates/ai-review-worker.yml`, `review_coordinator.py` en `cmd_close_result`,
tests de cierre/workflows/instalación y evidencia D1.
No cambia: runtime del modelo, providers, permisos, versiones fijadas del revisor,
workflow E1, nombres de artifact, autenticación o semántica de hallazgos.

Pasos:

1. Cambiar ambos `upload-artifact.path` a `${{ runner.temp }}/ai-review/result.json`;
   añadir `if-no-files-found: error`. La subida depende del cierre exitoso; no
   introducir `always()` ni exportar resultados intermedios.
2. Mantener todos los campos finales del paquete usados por el consumidor.
   Retirar el saneamiento de `repair.json`/`cierre.json` destinado solo a exportarlos,
   una vez demostrada la nueva selección de archivos. Mantener la redacción de
   la respuesta y error finales.
3. Probar un directorio temporal con logs/contexto/vueltas extra que contienen
   un secreto ficticio. El inventario seleccionado por cada workflow debe ser
   exactamente `{result.json}` y el resultado final no contener ese secreto.
   Una prueba que solo compruebe ausencia del string en YAML no es suficiente.
4. Pasar por el consumidor un artifact mínimo y otro antiguo con extras y un
   único resultado; ambos se aceptan. Dos resultados o identidad/attempt erróneos
   se rechazan como antes. Ausencia de archivo/cierre fallido no produce artifact
   exitoso y sigue la recuperación de worker sin resultado.

**Revisión Codex:** comprobar el destino real de la subida, no solo `redact()`;
confirmar el workflow instalado y la plantilla, y que E1 conserva result+manifest.
La garantía es del artifact, no una afirmación de ausencia de secretos en todos
los logs de Actions.

## D2 — Una aceptación de hallazgos para todos los formatos

**Promesa:** entradas equivalentes v1/v2/v3 producen los mismos IDs, estados,
anclas y contadores bajo la política definida en el diseño. Ningún adaptador
retiene su propio algoritmo de merge.

Puede tocar: `review.py`, `review_domain.py`, adaptación de `_estado_actual` en
`review_coordinator.py`, tests de memoria/resolución/cobertura/capacidad,
`.github/workflows/e1-measure.yml` **solo para migrar su caller de
`merge_findings`**, scripts con callers afectados, README y evidencia D2.
No cambia: gramática/modo de comandos, presupuesto/digest de política, providers,
datos históricos de evaluación, formato exportado por E1 ni consumidor Glee.

Pasos:

1. Añadir matriz de transición de la tabla del diseño. Cubrir reapertura sin
   delta, reapertura pertinente, descarte persistente, nuevo claim resuelto,
   IDs duplicados, ID sobre otro archivo, línea legada desplazada y reversión
   que no debe resolver un hallazgo recién creado. Mostrar los fallos relevantes
   antes de corregirlos; no convertir indiscriminadamente tests viejos a verde.
2. Implementar `memoria_operativa` reutilizando `_migrate_legacy` a través del
   lector y `snapshot_a_v3`, también para Legacy. Comparar contra el JSON original
   antes del saneamiento: pérdidas de títulos/rutas/relacionadas/entradas o
   colisiones de IDs conservan el original, sin renumeración silenciosa.
3. Hacer que preparación, publicador directo y coordinador usen el estado común;
   aceptar mediante `accept_report`, descartar mediante `aplicar_descartes`.
   Conservar política de comandos de cada modo. Escribir v3 solo tras validación
   integral de capacidad. Legado inválido/futuro/sin espacio conserva el cuerpo.
4. Migrar todos los callers, incluido el seed incremental de E1. Eliminar
   `merge_findings`, `apply_dismissals` y el branch legado de `build_findings`.
   Renombrar `_match_como_merge_findings` por su política de títulos. Actualizar
   imports de los tests tocados; no dejar wrappers que reintroduzcan el API viejo.
5. Documentar los cambios deliberados respecto a v1 y comprobar que el
   `prev.json` de E1 sigue siendo consumible. No reescribir los corpus históricos.

**Revisión Codex:** seguir un caso desde el codec hasta el comentario en cada
modo; comparar estados, no solo tipos; revisar descartes, anclas relacionadas,
límites y cursor. Ejecutar el parser Glee congelado contra el cuerpo producido.

## D3 — Codec de documento y versión puente

**Promesa:** existe un lector/escritor puro de documento probado, y una versión
operativa a la cual volver sin que un escritor antiguo destruya una vista nueva.
Esta entrega todavía publica el formato histórico sin view.

Puede tocar: nuevo `review_document.py`, helpers de render presupuestado,
guardas de entrada en `cmd_publish`/`cmd_reconcile`, tests nuevos
`test_review_document.py`, tests de compatibilidad/capacidad y evidencia D3.
No cambia: formato emitido por producción, bloque v3, permisos o consumidor Glee.

Pasos:

1. Implementar los tipos y el protocolo view/slots del diseño. Las regiones
   opacas no se interpretan por encabezados Markdown. Importar comentario viejo
   como histórico, con memoria/cabecera por sus lectores existentes.
2. Implementar preparación completa y presupuesto de estado/cuerpo, con hashes
   después de saneamiento y recortes. Probar ida/vuelta UTF-8, cambios de títulos,
   delimitadores hostiles, slots duplicados/ausentes, versión futura, hash erróneo,
   memoria corrupta, cabecera adulterada y tres ciclos sin doble escapado.
   No agregar dependencias externas para serializar.
3. Agregar guardas en ambos escritores históricos: cualquier marcador activo
   view/slot/end-slot, también desplazado, incompleto o huérfano, impide escribir
   y despachar. No confundir una cita neutralizada en prosa con ese marcador.
4. Probar el estado viejo del PR20, 60 hallazgos abiertos, libro grande y tres
   explicaciones multibyte. Reportar bytes/caracteres reales, prosa recortada y
   resultado (publicable/Keep). Un comentario corriente representativo no debe
   quedar inutilizable por los nuevos metadatos; si ocurre, ajustar el diseño
   antes de D4, sin subir límites ni eliminar memoria.
5. Entregar SHA de D3 como **versión puente de retorno**. Demostrar con un
   documento del nuevo codec que ninguno de sus dos escritores lo modifica.

**Revisión Codex:** codec y presupuesto completos; preservar contratos del
consumidor externo; ensayo de retorno en procesos separados o checkouts aislados.
No basta afirmar que v3 es compatible: el lector viejo pierde campos desconocidos.

## D4 — Activar datos de presentación y eliminar el parser de Markdown

**Promesa:** revisión, descarte, explicación y fallo convergen en un documento
estructurado que se renderiza y publica una sola vez. El aspecto del Markdown
no decide qué datos se conservan.

Puede tocar: `review_document.py`, `review_render.py`, `review_sticky.py`,
`review_coordinator.py`, `review.py`, `scripts/verify_sticky.py`,
`scripts/armar_sticky_pr20.py`, tests relacionados, README y evidencia D4.
No cambia: codec externo v3, permisos, identidad de artifact, runtime/proveedores,
fixtures congelados de consumidores ni configuración de repos consumidores.

Pasos:

1. Construir cambios visibles tipados durante la convergencia. Una explicación
   nueva o aviso puede cambiar el documento sin cambiar los hallazgos; no fingir
   un avance de generación/coverage para forzar su publicación.
2. Migrar todos los callers de `revision_visible`, `estado_visible`, `encadenar`
   y `visible=` al único preparador. Eliminar esas implementaciones y los
   reconocedores de títulos/avisos del cuerpo viejo. La migración conserva la
   prosa antigua opaca; no mantiene otro renderer ni un parser legado de títulos.
3. Sustituir las guardas de escritor puente por el lector completo. Conservar
   bloqueo de documentos inválidos/futuros. `publish_checkpoint` compara la
   publicación completa con la lectura fresca, protege base obsoleta y devuelve
   confirmación solo del documento íntegro. Migrar también la publicación directa.
4. Probar revisión → descarte → varias explicaciones → aviso → fallo → reinicio,
   conservando memoria/cobertura y las tres explicaciones/avisos pertinentes.
   Cambiar los encabezados visibles y comprobar que los datos sobreviven.
   Probar mismo dominio con distinta vista, PATCH aplicado con respuesta perdida,
   POST incierto, lectura fresca divergente, dos stickies y desborde sin despacho.
   Verificar que la solicitud exacta de `work_after_commit` llega al recibo solo
   tras confirmar; no se despacha por coincidencia exclusiva del estado.
5. Actualizar verificador y script de PR20 para el documento completo; comprobar
   su salida con el parser Glee original. Todo cuerpo emitido debe poder leerse
   y ser consistente tras las reducciones de capacidad.
6. Ensayar vuelta al SHA de D3: se detienen escrituras sin perder estado o vista.
   Volver a D4 permite leer ese mismo cuerpo. No ejecutar ese ensayo contra PRs
   reales ni instalar la action en consumidores como parte de esta entrega.

**Revisión Codex:** recorrer los cuatro flujos, recuperación/idempotencia y texto
hostil; confirmar que desaparecieron las decisiones basadas en encabezados y la
igualdad exclusiva del bloque de estado. Revisar diferencias visibles deliberadas
de la primera importación y verificar que quedan rotuladas como históricas.

## Comprobaciones y protocolo de revisión

Python requerido: 3.10 o superior; en los comandos siguientes `python3` debe
resolver a esa versión. En el entorno del diseño se usó Python 3.14.7.
Se reutilizan fixtures y pruebas existentes; añadir pruebas por el fallo que
detectan, no por alcanzar un conteo.

```bash
python3 --version
python3 -m unittest discover -s tests -p 'test_review_domain.py'
python3 -m unittest discover -s tests -p 'test_review_coordinator.py'
python3 -m unittest discover -s tests -p 'test_review_commands.py'
python3 -m unittest discover -s tests -p 'test_capacidad_pr20.py'
python3 -m unittest discover -s tests -p 'test_e1a_workflow.py'
# Desde D3, después de crear el archivo:
python3 -m unittest discover -s tests -p 'test_review_document.py'
# Piso final integrado y antes de cualquier push, sin --no-precommit:
bash scripts/check.sh
git diff --check
```

Las suites dirigidas se eligen por la entrega y se ejecutan durante el desarrollo;
no es obligatorio correrlas otra vez después de una batería completa verde del
mismo SHA. Comprueba cada unidad con las pruebas afectadas; corre la batería
completa al final y antes de cualquier push que la exija. No publiques cada commit
intermedio solo para avanzar: puedes completar D1–D4 localmente y hacer un único
push final. Si falta una herramienta de `check.sh`, instalarla en el entorno de
trabajo según sus instrucciones; no quitar el gate. Registrar actionlint en CI
si el script local informa que no está disponible.

Devin entrega el conjunto implementado y su evidencia; Codex responde **aceptado**, **requiere cambios** o
**bloqueado**, con impacto y reproducción de cada hallazgo. Ante correcciones,
volver a revisar las superficies afectadas y reutilizar lo que sigue válido.
El diff nuevo requiere su propio `check.sh` antes de la aceptación final.
La aprobación de D4 no equivale a desplegar: la instalación, cambios de pins,
merges y publicaciones en consumidores se coordinan después por separado.

| Situación | Acción concreta |
|---|---|
| Otro cambio ya resolvió una tarea | Demostrar contrato con código/prueba y entregar el delta restante; no rehacerla. |
| Diferencia de semántica no cubierta en la tabla | Registrar reproducción y propuesta para Codex; detener solo lo dependiente y continuar el resto. No añadir otro motor ni ocultarla cambiando tests. |
| Capacidad insuficiente | Conservar comentario y trabajo sin confirmar; presentar bytes y fixture. No aumentar límites. |
| Esquema/documento desconocido o corrupto | No escribir ni despachar; emitir diagnóstico sin secretos. |
| Consumidor requiere otra forma | Mantener fixture original y documentar incompatibilidad para Codex; continuar tareas independientes. No editarlo para pasar. |
| Se necesitan credenciales/servicio real para una prueba | Usar adaptador o fixture local; declarar no verificado el recorrido real. No hacer llamadas pagadas. |
| Dos soluciones producen la misma excepción estructural | Revisar contrato antes de añadir flags; registrar el cambio de diseño con su evidencia. |

## Entrega final y encargo copiable

La entrega final es un único PR borrador con los SHAs/diffs de D1–D4 y sus evidencias, tabla de diferencias
del comportamiento v1, inventario de símbolos eliminados y medición de capacidad.
Comparar líneas de producción, tests y documentación por separado, contando
módulos nuevos; no reportar solo el descenso de `review.py`.
No se fija presupuesto de llamadas al modelo: las pruebas son deterministas y
no necesitan corridas pagadas. Los documentos actuales no constituyen una prueba
de implementación ni un despliegue.

> Devin: implementa `docs/plans/2026-11-15-reviewer-simplification.md` siguiendo
> `docs/reviewer-simplification-design.md`. Ejecuta D1 → D2 → D3 → D4 de corrido,
> en una rama y con commits separados, sin esperar aprobación entre unidades.
> Codex revisará el conjunto al final. Verifica cada unidad con pruebas dirigidas
> y ejecuta `bash scripts/check.sh` al final y antes del push. Conserva el SHA del
> puente D3. Resuelve los problemas locales autónomamente; ante un bloqueo de
> contrato, registra reproducción/propuesta y continúa lo independiente. Entrega
> un solo PR borrador con evidencia y pendientes explícitos. No hagas merge,
> despliegues, llamadas pagadas ni migraciones de comentarios reales.

Este texto es para entregar cuando se decida iniciar la implementación; no se ha
enviado a Devin durante la preparación del plan.

Verificación documental (2026-11-15): dos lectores de diseño revisaron el contrato;
sus bloqueadores se corrigieron y comprobaron en una segunda lectura limitada.
Enlaces locales, bloques de código y `git diff --check` correctos; gitleaks sobre
el contenido de los tres documentos no encontró secretos. No se repitió la batería
de runtime porque esta entrega solo modifica documentación.

Ajuste de ejecución solicitado por David: D1–D4 se realizan de corrido y Codex
revisa al final. Esta instrucción sustituye la revisión intermedia por entrega;
se conservan las dependencias, pruebas y contratos del diseño.
