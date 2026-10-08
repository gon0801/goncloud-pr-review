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
- El conjunto lee los secrets `API_KEY` (proveedor principal) y
  `FALLBACK_API_KEY` (respaldo) del consumidor; el escritor que el retorno
  repone lee `AI_REVIEW_API_KEY` y `DEEPSEEK_API_KEY`.

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

---

# Piloto de T16: orden, avance y medición (fase 1, sin gasto)

Estado: fase 1 de T16 — documentos y preparación. Cero gasto, cero activación,
nada instalado. La medición viva y cualquier activación requieren encargo
posterior con revisión cruzada previa. El corte y el retorno mecánicos son los
que describe el procedimiento de T11 de arriba.

## Qué hay congelado hoy

- `evaluation/reviewer/v2/pairing.json`: 33 pares con repeticiones 1 a 3 y orden
  alternado control-variante, congelado en `882d006` antes de medir. 15 pares de
  producto en el SHA exacto (5 ya observados en E1, 10 pendientes) y 18 pares de
  pushes consecutivos (12 pendientes de T16 y 6 de la repeticion 1 sin capturar).
- SHA candidato del código: `882d00610fe8546b8ccc2404a9f5639c19632bbb` (punta de
  `rc/t16-piloto`). El piloto queda clavado a ese `ACTION_SHA`; un merge de
  `main` del repo central no cambia lo que ejecutan los consumidores instalados.
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
- `e1-measure.yml` presente pero con un defecto conocido que hay que corregir
  antes de la primera corrida real: el paso "Revisor fijo fuera del árbol
  medido" copia sólo `review.py prompt.md` y faltan `review_domain.py` y
  `review_context.py` (el workflow ya estaba roto desde que existe
  `review_domain.py`; está pineado por `tests/test_e1a_workflow.py`).

## Orden del piloto

1. **Repo central** (`goncloud-pr-review`): primero revisiones fijas en el repo
   central, en un PR del propio repo, con el revisor fijado al SHA candidato.
2. **summonaikit-claude**: primer consumidor, mismo `ACTION_SHA`.
3. **openclaw**: segundo consumidor.
4. **Orbit**: tercer consumidor.

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

## Diseño de la corrida de costo unitario (no ejecutada)

Un par, brazo control, proveedor directo (deepseek), sin publicar nada:

```
gh workflow run e1-measure.yml --ref 882d00610fe8546b8ccc2404a9f5639c19632bbb \
  -f caso=pr2 -f pr=2 \
  -f head=3166ad5a7f6ca4a95b00432154fc19cfb689e3c5 \
  -f base=5844ff63ab7bcd10644824f70ccfb7dd7791c0ba \
  -f proveedor=deepseek
```

- Prerrequisito: corregir el `cp` de `e1-measure.yml` (faltan
  `review_domain.py` y `review_context.py`; hoy el job rompe en prepare).
- Llaves: `DEEPSEEK_API_KEY` para la ruta deepseek (o `AI_REVIEW_API_KEY` para
  opencode-go), configuradas por el operador con `gh secret set` en el repo y
  leídas sólo como env del paso de la ruta elegida. Nunca se escriben en
  archivos del repo ni en discos locales.
- Qué registra: la corrida deja `result.json` con tokens (usage), `duration_ms`
  y `total_cost_usd` por intento, con `ATTEMPTS=2` y presupuesto de turnos
  histórico. El costo del par es el del intento exitoso más los fallidos.

## Estimación de costo de la medición completa

Base real: las 20 corridas de E1 con costo conocido (DeepSeek V4.1 Flash).
Ruta deepseek directo (la del piloto): mediana **$1.4484** por corrida, media
$1.7365, rango $0.7051-$3.5961 (n=15). Ruta opencode-go: mediana $0.5135 (n=5).

Pares pendientes de observación según el pairing congelado: 10 de producto
(reps 2-3) y 18 de push (12 de T16 más 6 de la repeticion 1 sin capturar).

Brazos según el plan: en pares de producto, control y C0 (2 corridas por par);
en pares de push, control, D0 y C0 (3 corridas por par; C0 medida como variante
separada frente al mismo control). El orden está alternado y congelado.

| Grupo | Pares pendientes | Brazos | Corridas |
|---|---:|---:|---:|
| Producto (control + C0) | 10 | 2 | 20 |
| Push (control + D0 + C0) | 18 | 3 | 54 |
| **Total** | 28 | | **74** |

Estimación con la mediana de la ruta directa: 74 × $1.4484 ≈ **$107**. Con la
media: ≈ $128. Rango por corrida extrema: $52-$266.

Límites de la estimación, declarados: (1) asume costo de las corridas D0/C0
igual al control, cuando el delta y el contexto selectivo deberían costar menos
— ese ahorro es justo lo que T16 mide, así que la cifra es techo, no
predicción; (2) no incluye corridas de calibración ni repeticiones por fallos
del proveedor (E1 necesitó retanda por cuota); (3) los costos unitarios vienen
de diffs de E1 (0.7-3.6 KB revisados), y casos más grandes cuestan más por la
relación turnos-diff; (4) CodeRabbit y cualquier revisión de terceros no
generan gasto de proveedor en este diseño.
