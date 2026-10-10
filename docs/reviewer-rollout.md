# Puesta y vuelta del revisor coordinado (T11)

Procedimiento de corte y retorno para el escritor coordinado (publicador más
worker) definido en `docs/reviewer-corrections-architecture.md`, sección
"Instalación, activación y retorno". El instalador produce el conjunto
atómico; este documento fija el orden operativo. Instalar en repositorios
reales pertenece a T16; aquí se ensaya primero con revisiones fijas en el
repo central.

> **Estado del modo coordinado.** Activo solo en el repo central (#90). La
> ronda 3 del piloto (`docs/evidence/reviewer/T16-piloto-2.md`) pasó los casos
> 1 a 5 después de que la ronda 2 encontrara pérdida de hallazgos, pero ninguno
> ejerció una resolución: el coordinador no verificaba los «resolved» del modelo
> (#94 lo arregla, con el central activo). Los consumidores no se despliegan sin
> decisión del operador, después de #92 y #94. Hasta que se quite esa barrera,
> `install.sh --coordinado` se niega sin `AI_REVIEW_PILOTO=1`.

## Qué instala el modo coordinado

```bash
AI_REVIEW_PILOTO=1 ACTION_SHA=<sha candidato> scripts/install.sh --coordinado gon0801/mi-repo
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
   ni runs del publicador pendientes de reenvío. Comprueba los dos con
   `gh run list --workflow ai-review-worker.yml --status in_progress` (y
   `queued`) justo antes de mergear el retorno: en #86 un worker de otro PR
   (#87) seguía en vuelo, su aviso dio 422 porque el publicador ya no
   existía y la solicitud quedó `pending` huérfana en esa memoria.
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

---

# Piloto de T16: orden, avance y medición

Estado: la medición terminó (63 corridas, $110.6439) y la decisión de D0 se
ejecutó en el PR #65. Ronda 1 del piloto (`docs/evidence/reviewer/T16-piloto.md`):
la cadena funciona tras seis arreglos pero sin capa visible; volvió a current
(#77). Ronda 2 (`docs/evidence/reviewer/T16-piloto-2.md`): con capa visible
(#79, #83) el publicador perdía 5 de 6 hallazgos por revisión; el central
volvió al revisor directo (#86). Ronda 3 (mismo documento): con #87, #88 y #89
el central reactivó el coordinado (#90) y pasaron los casos 1 a 5, sin ejercer
una resolución (el coordinador no las verificaba; #94); sigue activo
en el central. Los consumidores (summonaikit-claude, goncloud-openclaw,
goncloud-Orbit) quedan a decisión del operador después de #92. El corte y
el retorno mecánicos son los que describe el procedimiento de T11 de arriba.

## Qué hay congelado hoy

- `evaluation/reviewer/v2/pairing.json`: 33 pares con repeticiones 1 a 3 y orden
  alternado control-variante, congelado en `882d006` antes de medir. 15 pares de
  producto en el SHA exacto (5 ya observados en E1, 10 pendientes) y 18 pares de
  pushes consecutivos (12 pendientes de T16 y 6 de la repeticion 1 sin capturar).
- Ref de la medición: tag `t16-candidato-e0898ff` =
  `e0898ffbb305547bc8175ec64ca1573b7e3c337b`. Es anterior al forzado de la
  revisión completa del segundo push (PR #65), así que NO es el candidato del
  piloto: el piloto correría el incremental que la decisión retiró.
  (`882d006` queda como el commit que congeló el pairing.)
- SHA candidato del piloto: un tag `t16-piloto-<sha8>` sobre `main` posterior
  al forzado (#65) y al arreglo de los secrets del worker; el SHA exacto que
  resuelva se registra en la evidencia del piloto. El piloto queda clavado a
  ese `ACTION_SHA`; un merge de `main` del repo central no cambia lo que
  ejecutan los consumidores instalados.
- D0 y C0 desactivadas por defecto y sin caller que las active:
  `ReviewPolicy.strict_budget=False` en los dos únicos callers de producción
  (`review.py` prepare y execute-request) y `selectivo` sin pasar en ninguno.

## Readiness del repo central (verificada sin instalar nada)

- `action.yml` intacta: último cambio en T11 (`a3b4891`), sin diff pendiente.
- Instalador de T11 presente: `scripts/install.sh`.
- Preparación de D0: `review_context.py` con `prepare_review`, `cmd_prepare`
  como adaptador con el manifiesto históricamente idéntico.
- C0: `contexto_selectivo` sólo corre con `selectivo=True`; hoy ningún caller lo
  pasa.
- `e1-measure.yml` corregido en el candidato: el paso "Revisor fijo fuera del
  árbol medido" copia `review.py review_domain.py review_context.py
  prompt.md` (antes sólo copiaba `review.py prompt.md` y el job rompía en
  prepare desde que existe `review_domain.py`; pineado por
  `tests/test_e1a_workflow.py`).

## Orden del piloto

1. **Repo central** (`goncloud-pr-review`): primero revisiones fijas en el repo
   central, en un PR del propio repo, con el revisor fijado al SHA candidato.
2. **summonaikit-claude**: primer consumidor, mismo `ACTION_SHA`.
3. **goncloud-openclaw**: segundo consumidor.
4. **goncloud-Orbit**: tercer consumidor.

En cada consumidor la instalación es el commit/PR atómico del procedimiento de
arriba, que retira los escritores antiguos antes de activar el coordinador.

## Criterios de avance por repo

Un repo avanza al siguiente sólo si completó, sobre su propio PR:

1. Los casos representativos de T05 publicados y releídos: un solo comentario,
   límites y roundtrip verificados (bloques que cierran, UTF-8, `-->`), y
   solicitudes simultáneas sin duplicar IDs.
2. Un descarte concurrente, una explicación, otra revisión del mismo SHA y la
   recuperación manual de una solicitud pendiente (condiciones de T08 y T09).
3. Un único comentario y un único escritor por PR, con resultados identificados
   por SHA.
4. Roundtrip y capacidad dentro del perfil vigente de 8000 bytes con reserva
   medida: el estado representativo de M1 mide 5897 bytes con diez hallazgos;
   casos que no quepan no avanzan el piloto (el perfil ampliado de 40000 exige
   su propia prueba y no es parte de este piloto).
5. Ensayo de retorno completado (procedimiento de arriba): checkpoint válido,
   escritor compatible publicando, descartes y pendientes conservados.

Con la muestra incompleta del baseline (20 de 30 PRs, 6 de 10 pares de pushes),
D0 y C0 siguen sin autorización de activación: el piloto corre con revisión
completa salvo que un encargo posterior autorice explícitamente medir D0/C0.

## El piloto nunca se convierte en activación general

Un merge de este plan, del rollout o del piloto no activa nada. La activación
general exige, por opción y por escrito: activar, mantener en piloto o volver a
`current`, con el SHA del código, la configuración, la muestra y los criterios
satisfechos identificados; cada opción conserva el esquema y el presupuesto
necesarios para desactivarla. La medición viva y la activación requieren encargo
posterior y revisión cruzada previa.

## Diseño original de la corrida de costo unitario

El candidato de medición es el commit que ya incluye el arreglo del `cp` (con
su prueba actualizada), publicado como tag `t16-candidato-<sha8>` en el remoto
al empujar la rama: `--ref` de workflow_dispatch acepta rama o tag, no un SHA,
y el SHA del candidato sólo existe en el remoto después de ese push. El rollout
registra el SHA exacto que resuelva el tag.

Un par, brazo control, proveedor directo (deepseek), sin publicar nada:

```
gh workflow run e1-measure.yml --ref t16-candidato-<sha8> \
  -f caso=pr2 -f pr=2 \
  -f head=3166ad5a7f6ca4a95b00432154fc19cfb689e3c5 \
  -f base=5844ff63ab7bcd10644824f70ccfb7dd7791c0ba \
  -f proveedor=deepseek
```

- Llaves: `DEEPSEEK_API_KEY` para la ruta deepseek (o `AI_REVIEW_API_KEY` para
  opencode-go), configuradas por el operador con `gh secret set` en el repo y
  leídas sólo como env del paso de la ruta elegida. Nunca se escriben en
  archivos del repo ni en discos locales.
- Qué registra: la corrida deja `result.json` con tokens (usage), `duration_ms`
  y `total_cost_usd` por intento, con `ATTEMPTS=2` y presupuesto de turnos
  histórico. El costo del par es el del intento exitoso más los fallidos.

### Brazos del par de push: control y delta-d0

Los pares de push miden dos brazos del mismo push. El control es la invocación
de arriba (revisión completa del par `head`/`base`). El brazo delta-d0 añade
dos entradas opcionales: `prev_sha` (SHA del push anterior) y `prev_findings`
(bloque crudo de hallazgos `<!-- ai-review:findings=... -->` que el modelo dejó
en el `result.json` del control del push anterior). Con esas entradas el
workflow construye `prev.json` antes de `prepare` como lo dejaría el estado
publicado que lee `cmd_gate`: numera los hallazgos con `merge_findings` sobre
`parse_model_findings` (los `F-new` del modelo pasan a F1..Fn, nunca ids
vacíos) y fija `completion` en complete. Sobre el tag de la medición
(`t16-candidato-e0898ff`) la corrida sale incremental (manifest
`mode=incremental`, `prev_sha` del push anterior); desde el forzado del PR #65,
con el código de `main` la misma entrada sale `full` con
`reason=forced-full-t16` y conserva el delta real. Sin las entradas, la corrida
es control (`mode=full`). Declaración N2 del veredicto f5: `completion` queda
fija en complete y no se aplican descartes, razonable en E1 porque nada se
publica y no existen descartes que aplicar.

```
gh workflow run e1-measure.yml --ref t16-candidato-<sha8> \
  -f caso=pr2-push-2-delta -f pr=2 \
  -f head=<push2-head> -f base=<merge-base> -f proveedor=deepseek \
  -f prev_sha=<push1-head> -f prev_findings="$(cat bloque-push1.md)"
```

Límite en cadena, declarado: el brazo delta-d0 de un push depende del estado
de hallazgos de la revisión del push i-1. Para los deltas de push-2 en
adelante, el control del push i-1 corre primero, su bloque se extrae del
`result.json` y se pasa como `prev_findings` al delta del push i. Para los
deltas de push-1 (cuyo `sha_anterior` es `pushes[0]`, sin control congelado en
el pairing original) existe la dependencia congelada del experimento
«dependencia-control-push-0»: una corrida de control por PR sobre `pushes[0]`,
compartida por las repeticiones, cuyo único rol es proveer ese bloque inicial.
Sin ese eslabón la corrida delta no puede ejecutarse con fidelidad al protocolo
D0 (pasar `base=sha_anterior` no lo sustituye: sería una revisión completa del
delta sin memoria). El artefacto del brazo delta lleva sufijo `-delta` en el
nombre para no colisionar con el de su control.

## Estimación de costo de la medición completa

Base real: las 20 corridas de E1 con costo conocido (DeepSeek V4.1 Flash).
Ruta deepseek directo (la del piloto): mediana **$1.4484** por corrida, media
$1.7365, rango $0.7051-$3.5961 (n=15). Ruta opencode-go: mediana $0.5135 (n=5).

Corridas pagadas según el pairing congelado (sólo lo congelado): 10 de
producto pendientes × 1 corrida pagada cada uno (el lado CodeRabbit son
comentarios existentes, sin gasto) + 18 de push pendientes × 2 corridas
(control completo y variante delta-d0, las dos de proveedor) = **46 corridas**.

Dependencias congeladas en f6 (decisión B2 del veredicto f5): 4 corridas más,
el control compartido sobre `pushes[0]` de pr2, pr4, pr9 y pr13 que provee el
bloque de memoria de los deltas de push-1. Total: **50 corridas pagadas**.

Estimación con la ruta directa: 46 × $1.4484 ≈ **$67** (las 46 corridas del
pairing); sumando las 4 dependencias, mediana 50 × $1.4484 ≈ **$72**; media
50 × $1.7365 ≈ $87; rango $35-$180 con los unitarios extremos
($0.7051-$3.5961). Con el unitario medido en f3 ($1.6149), 50 × $1.6149 ≈ $81.

## Resultado real de la medición (fases 9 y 10, 2026-10-09)

Ejecutada sobre el tag `t16-candidato-e0898ff` con proveedor deepseek, 3
corridas concurrentes como máximo y cadena de memoria por PR. Costo de la
medición (f9 + f10): **$109.029** — $89.3827 en las 50 corridas de la
medición completa (f9) y $19.6463 en las 12 de la repetición de los pares de
rep 2 (f10). Total T16 incluyendo la corrida unitaria de f3 ($1.6149):
**$110.6439**. Dentro del rango declarado.

**Propuesta histórica al operador (docs/evidence/reviewer/D0-C0.md)**. En el
momento de la medición el brazo delta medido ERA el modo incremental vigente en producción en cada
segundo push (`action.yml` corre gate+prepare; `cmd_gate` escribe prev.json y
`cmd_prepare` entra incremental). Opciones reales: (1) mantener el
incremental actual, que la medición muestra 11-25% más caro y con pérdida de
bloques (6 de 18 corridas incrementales con el comentario sin cierre; 0 de 32
completas; defecto de producción registrado en Plans.md), o (2) volver a
revisión completa en el segundo push, un cambio en `cmd_prepare` con su
encargo, pruebas y revisión. Calidad medida: 0 High/Critical perdidos y 0
falsos resueltos en ambos brazos; precisión sin jueces. El operador eligió la
opción (2), ejecutada en el PR #65.

Desvío registrado y reemplazado (B1 de VEREDICTO-T16-f9): en f9 los 6 pares de
la repetición 2 corrieron control antes que delta, al revés del orden congelado
(`variante-control`). Esas 12 corridas quedan como desvío sin borrar
(evidencia en `evaluation/reviewer/v2/medicion-t16/`) y la f10 repitió esos 6
pares en el orden congelado con los mismos bloques fuente. Para el análisis de
D0, la repetición 2 válida de cada par de push es la de f10.

Observación para el informe: en mediana no pareada, el brazo delta-d0 de f9
salió por encima del control ($1.6322 contra $1.3516); la lectura de costo de
D0 corresponde al análisis por par con la repetición 2 de f10.

Límite operativo del workflow (N3 de VEREDICTO-T16-f9): dos despachos con el
mismo `caso` comparten grupo de concurrencia y GitHub cancela el pendiente más
viejo. La medición usó etiquetas únicas por corrida (`-rN`, `-r2b`); cualquier
re-dispacho debe hacer lo mismo.

C0 NO está en esta estimación y no se mide sin pares propios congelados: el
pairing no declara ningún brazo C0, y el plan exige congelar en pairing.json
los pares y repeticiones antes de medir. Medir C0 requiere, en un encargo
aparte con revisión, congelar pares control-completo contra C0 (mismo target,
modelo, proveedor y reglas) y recién entonces estimar su costo aparte.

Límites de la estimación, declarados: (1) no incluye corridas de calibración
ni repeticiones por fallos del proveedor (E1 necesitó retanda por cuota); (2)
los costos unitarios vienen de los diffs de E1, cuyo tamaño no se midió
(el diff de pr2 mide 54 321 bytes; el rango 0.7-3.6 es de costo en dólares),
y casos más grandes cuestan más por la relación turnos-diff; (3) el lado CodeRabbit de las
reps 2-3 de producto son los mismos comentarios existentes en cada repetición:
su dispersión es cero por construcción y el informe de T16 debe declararlo
para no leerla como estabilidad; (4) el gasto de proveedor de la variante
delta-d0 debería ser menor al control — ese ahorro es lo que se mide, así que
la cifra por brazo es techo.
