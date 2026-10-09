# Puesta y vuelta del revisor coordinado (T11)

Procedimiento de corte y retorno para el escritor coordinado (publicador más
worker) definido en `docs/reviewer-corrections-architecture.md`, sección
"Instalación, activación y retorno". El instalador produce el conjunto
atómico; este documento fija el orden operativo. Instalar en repositorios
reales pertenece a T16; aquí se ensaya primero con revisiones fijas en el
repo central.

## Qué instala el modo coordinado

```bash
ACTION_SHA=<sha candidato> scripts/install.sh --coordinado gon0801/mi-repo
```

- Un único commit en `chore/ai-review` que agrega
  `.github/workflows/ai-review-publish.yml` y
  `.github/workflows/ai-review-worker.yml`, retira el escritor anterior
  `.github/workflows/ai-review.yml` y conserva cualquier otro archivo.
- Ambos workflows fijan el CLI confiable a `gon0801/goncloud-pr-review`
  en `ACTION_SHA` (checkout con `repository` y `ref` explícitos). El piloto
  queda clavado a ese SHA candidato: un merge de `main` del repo central no
  cambia lo que ejecutan los consumidores instalados.
- Si la rama cambió durante la instalación, el instalador reintenta sobre la
  punta nueva sin forzar; si no logra en tres intentos, falla con error.
- El conjunto lee los mismos secrets que el escritor que el retorno repone:
  `AI_REVIEW_API_KEY` (opencode-go, principal) y `DEEPSEEK_API_KEY`
  (respaldo). No hace falta crear secrets nuevos para instalarlo.

El modo actual (`scripts/install.sh gon0801/mi-repo`) no cambia: instala
`ai-review.yml` y, si se pasa `ACTION_SHA`, fija `uses:` a ese SHA en vez de
`@main`. Consumidores sin configuración nueva se comportan igual que antes.

## Corte: de la revisión actual al coordinador

1. **Detener la admisión.** Congela merges del PR del conjunto y evita
   eventos nuevos de revisión en el consumidor durante el corte (por ejemplo,
   pausando los pushes planeados). La bandera de desactivación por variable
   de las plantillas nuevas queda pendiente (fila de T11); hoy la admisión se
   detiene por congelación del evento.
2. **Drenar ejecuciones antiguas.** Espera a que no queden runs del escritor
   anterior (`gh run list --workflow ai-review.yml`) ni runs del worker
   despachados sin artifact. El coordinador no activa hasta que la cola esté
   vacía.
3. **Comprobar el checkpoint.** El comentario fijo del PR debe ser el único
   comentario con el marcador y su bloque debe leerse como memoria válida
   (schema 3, generación actual, descartes y solicitudes pendientes
   presentes). `review.py reconcile` sobre un evento sin admisión es el
   verificador: publica sin trabajo nuevo o falla alto.
4. **Activar el coordinador.** Fusiona el PR del conjunto. El piloto corre
   sobre el SHA candidato: prueba primero revisiones fijas en el repo central
   antes de fusionar en un consumidor.

## Retorno: del coordinador al escritor compatible

El rollback operativo **no** es volver al binario `179e6a3`: ese escritor
congela v2 y no actualiza estados ampliados. El retorno habilita el escritor
compatible probado (modo actual, `ai-review.yml` con `ACTION_SHA` fijado).

1. **Detener y drenar al coordinador.** Congela merges que toquen los
   workflows coordinados y espera a que no queden runs del worker en vuelo
   ni runs del publicador pendientes de reenvío.
2. **Desactivar `anchors` y modos experimentales.** Ninguna plantilla
   instalada activa `FINDING_IDENTITY=anchors` ni D0; verifícalo en el
   entorno del consumidor antes de cambiar de escritor.
3. **Instalar el retorno.** `ACTION_SHA=<sha compatible probado>
   scripts/install.sh gon0801/mi-repo` genera el commit que repone
   `ai-review.yml` y retira el conjunto coordinado, con la misma atomicidad.
4. **Conservar schema y hallazgos.** El escritor de retorno lee y actualiza
   el estado con estrategia `current`: conserva el schema 3, los hallazgos y
   avanza la generación. La prueba
   `CompatibleRollback.test_updates_expanded_schema3_in_current_mode` fija
   esta garantía sobre una memoria con descarte, solicitud pendiente y
   generación en curso.
5. **Verificar la memoria.** Tras la primera revisión del escritor de
   retorno, el comentario fijo conserva los descartes confirmados y las
   solicitudes pendientes (sobreviven a la desactivación por diseño).

Antes de cambiar de escritor otra vez se repite la exclusión por consumidor
del paso 1 del corte. Una segunda vuelta al coordinador repite el corte
completo desde el paso 1.
