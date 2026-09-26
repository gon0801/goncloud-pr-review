# goncloud-pr-review

Revisión automática de pull requests para los repos de gon0801. Reemplaza a CodeRabbit sin tope de revisiones por hora. Corre Claude Code en modo solo-lectura dentro de GitHub Actions con un solo modelo permitido, **DeepSeek V4.1 Flash**. Hoy lo toma de la suscripción OpenCode Go y está listo para pasar a la API de DeepSeek. El revisor ve el repo completo en el commit del PR, no solo el diff, así que puede ir a buscar callers, tests y esquemas antes de opinar.

## Cómo funciona

Cada repo lleva un workflow chico (`.github/workflows/ai-review.yml`, copia de `templates/ai-review.yml`) que llama a la action de este repo (`action.yml`). La action hace cinco pasos.

1. **Gate.** Busca el comentario fijo del bot en el PR. Si ya revisó exactamente ese commit, termina sin gastar tokens.
2. **Prepare.** Calcula el diff contra el merge-base y saca lockfiles, binarios, `dist/`, `build/` y `vendor/` de la raíz, `node_modules/` a cualquier profundidad, y lo que el repo agregue en `exclude`. Ordena lo restante con el código primero, luego config y al final docs. Si el diff pasa de `max_diff_bytes`, lo que no cabe queda listado como no revisado. También precalcula sin gastar turnos quién usa los símbolos cambiados, qué pruebas los mencionan y las convenciones del repo.
3. **Install.** Instala Claude Code y LiteLLM con la versión fijada. La caché solo se comparte dentro del mismo PR (así la acota GitHub): la primera revisión de cada PR instala en frío (~45 s) y las siguientes la reusan. El entorno de LiteLLM en caché se prueba antes de usarlo y se reconstruye si no arranca con el Python de la máquina. Si la instalación falla, no tumba el check: sigue el mismo camino suave que cualquier otra falla de infraestructura.
4. **Review.** Corre `claude -p` con `--restricted --safe-mode --strict-mcp-config --tools Read,Grep,Glob` y un tope de 60 turnos (80 si el diff pasa de 20 archivos o 300 KB; 30 en un push incremental chico). No puede ejecutar código, escribir archivos, salir a la red, ni leer fuera del checkout y del directorio de trabajo. También ignora los settings, hooks, MCP y CLAUDE.md que traiga el PR. Con OpenCode Go, Claude Code habla con un proxy LiteLLM local (versión fijada) que traduce a `/chat/completions`, porque Go solo sirve DeepSeek en formato OpenAI. Solo el proxy tiene la llave; Claude Code recibe un token desechable por corrida. Si falla por algo transitorio, reintenta una vez; si es 400/401/403/404, no reintenta. Ninguna de estas fallas tumba el check en rojo: si el equipo del repo no puede arreglarlas (falta la llave, el proxy no arrancó, el proveedor está caído), el job termina en verde con una anotación amarilla y Publish agrega el aviso "No se pudo revisar..." sin marcar el commit como revisado.
5. **Publish.** Edita el comentario fijo del PR, o lo crea si no existe. El comentario lleva un marcador oculto con el SHA revisado. Antes de publicar se borra del texto cualquier aparición de la API key o del token. Si el paso anterior falló por algo de infraestructura, este commit no se marca como revisado: si ya había un comentario de una revisión anterior, se le agrega arriba un aviso "No se pudo revisar el commit..." sin tocar la revisión anterior; si no había comentario, se crea uno solo con el aviso. Así la próxima vez (otro push o un "Re-run jobs") se vuelve a intentar sobre este mismo commit.

El prompt de review vive en `prompt.md`. Cada repo puede agregar reglas propias en `.github/ai-review.md`, y esas reglas se leen de la rama base, no del PR. Así un PR no puede cambiar las reglas con las que se le revisa.

## Cuándo se dispara

Se dispara con `pull_request` en `opened`, `synchronize`, `reopened` y `ready_for_review`. Los drafts no se revisan hasta que se marcan listos. Tampoco se revisan PRs de forks, porque GitHub no les da secrets. Los cambios de labels, títulos o comentarios no disparan nada. Los PRs abiertos por bots o agentes sí se revisan, con una excepción de GitHub: un PR creado o actualizado con el `GITHUB_TOKEN` de otro workflow no dispara workflows. Los agentes tienen que usar un PAT o un token de GitHub App para que su PR se revise.

**Concurrencia.** El grupo es `ai-review-<número de PR>` con `cancel-in-progress`. Los PRs distintos corren en paralelo, sin tope propio más allá de los jobs simultáneos de tu plan de Actions. Si llegan varios pushes al mismo PR, se cancela la corrida vieja y se revisa el último commit.

**Duplicados.** Hay un solo comentario por PR y se edita en su lugar. Si GitHub reenvía el mismo evento, el gate ve que ese SHA ya está revisado y no hace nada.

## Instalación en un repo

```bash
scripts/set-secret.sh gon0801/mi-repo          # pide la key una vez, sin eco
scripts/install.sh gon0801/mi-repo             # abre el PR con el workflow
EXCLUDE=$'data/**\nout/**' scripts/install.sh gon0801/mi-repo   # con exclusiones extra
```

El único secret es `AI_REVIEW_API_KEY`: la llave de OpenCode Go (opencode.ai/auth) o la de DeepSeek (platform.deepseek.com), según el proveedor. En una cuenta personal los secrets van repo por repo.

## Operación

- **Modelo.** Solo DeepSeek V4.1 Flash. No hay input para cambiarlo; los proveedores permitidos viven en `PROVIDERS` de `review.py` y cualquier otro valor de `provider` se rechaza.
- **Apagarlo en un repo sin tocar código.** `gh variable set AI_REVIEW_DISABLED --body true -R gon0801/mi-repo`. Para prenderlo, `gh variable delete AI_REVIEW_DISABLED -R ...`. El job sigue apareciendo y queda en verde de inmediato (no se omite): el primer paso del workflow ve la variable, sin importar mayúsculas, y se salta el checkout y la action. Eso es de la plantilla nueva; en los repos que ya tienen el workflow viejo, la action igual termina en verde sin revisar, pero antes hace el checkout.
- **Token propio.** Si pasas `github_token` de una GitHub App, pasa también `bot_login` con el login de esa App (por ejemplo `mi-app[bot]`). Solo los comentarios de ese login cuentan como la revisión fija.
- **Pedir otra revisión del mismo commit.** En la pestaña Checks del PR, "Re-run jobs" sobre `AI review`. Un re-run se salta el gate a propósito.
- **Revisión nueva.** Se hace sola con cada push. Del segundo push en adelante es incremental (si el comentario anterior es de antes de esta versión y no trae la memoria de hallazgos, esa primera vez se revisa todo el PR): solo revisa lo que cambió desde la última revisión (tope de 30 turnos si el push es chico; si no, el tope normal), verifica los hallazgos abiertos y conserva sus ids (F1, F2, ...). El veredicto cuenta solo los abiertos; los resueltos y descartados van plegados. Si el push es un rebase (el commit anterior ya no es ancestro), se revisa todo de nuevo pero se conservan los descartes.
- **Cuándo un hallazgo pasa a resuelto.** Solo si en ese push cambió alguno de sus archivos: donde está el problema o donde va el arreglo (por ejemplo, las pruebas de una función renombrada). El modelo puede nombrar el archivo del arreglo, así que un "resuelto" equivocado es posible cuando cambia un archivo que el modelo relaciona sin razón; es el costo de no dejar abiertos para siempre los arreglos hechos en otro archivo. Si ves uno equivocado, menciónalo en el PR; el revisor solo lo reabre si en otra revisión ve que el problema sigue ahí.
- **Descartar un hallazgo.** Comenta en el PR `ai-review: descartar F3` (o `ai-review: descartar todo`) y se aplica en el siguiente push: ese hallazgo no vuelve a salir, ni en la lista ni en el detalle. Cada comentario se aplica una sola vez: editar uno viejo no cuenta, escribe uno nuevo. Solo cuenta si lo escribe alguien con permiso de escritura en el repo (el revisor lo verifica con el endpoint de colaboradores usando el mismo token del comentario); los comentarios de terceros se ignoran.

## Proveedor y costo

**Ahora: `provider: opencode-go`.** Usa la suscripción OpenCode Go ($10 al mes) con el modelo `deepseek-v4.1-flash`. Go cuenta peticiones en ventanas de 5 horas, semana y mes. DeepSeek V4.1 Flash da unas 6,500 peticiones cada 5 horas y 32,500 al mes con la promoción 4× que termina el 27 de septiembre de 2026; sin ella, una cuarta parte. Una revisión gasta unas 20 a 80 peticiones. Cuando la cuota se agota, el check queda en verde con un aviso amarillo hasta que la ventana se renueva, y el merge no se bloquea.

**Destino: `provider: deepseek`, la API de DeepSeek, sin cuotas y pagando por uso.** Para cambiar:

1. Carga saldo en platform.deepseek.com y crea una llave.
2. Corre `scripts/set-secret.sh` con la llave nueva en los mismos repos.
3. Cambia el default de `provider` en `action.yml` a `deepseek`. Ese proveedor habla formato Anthropic directo, sin proxy.

Con la API de DeepSeek ($0.30/M de entrada, $0.006/M en caché, $1.20/M de salida en hora pico, la mitad fuera de pico), una revisión típica cuesta unos $0.05, y el mes queda en $50 a $150 para ~2,000 revisiones. Cada comentario trae en "Alcance de la revisión" los turnos (usados/tope) y los tokens de esa revisión, y con la API de DeepSeek también el costo aproximado. Los repos públicos no gastan minutos de Actions; en los privados, cada revisión toma unos 4 a 9 minutos del plan según el tamaño del diff.

## Problemas comunes

Las fallas que el repo no puede arreglar (falta la llave, el proxy no arrancó, el binario de Claude Code no se instaló, el proveedor está caído, un error permanente del API) nunca tumban el check en rojo ni lo dejan "omitido": el job termina en verde con una anotación amarilla (`::warning::ai-review: ...` en el log del paso "Review"), y si ya había una revisión previa de este PR, el comentario fijo le agrega arriba un aviso "No se pudo revisar el commit..." sin borrar la revisión anterior. El commit no queda marcado como revisado, así que el siguiente push (o un "Re-run jobs") lo vuelve a intentar.

| Síntoma | Causa probable |
|---|---|
| El job termina verde pero con una anotación amarilla en "Review" | Falta el secret `AI_REVIEW_API_KEY`, el proxy o Claude Code no arrancaron, o el proveedor devolvió un error; el detalle está en la anotación y, si aplica, en el aviso del comentario fijo |
| El job falla en rojo de verdad | Un evento que no es `pull_request`, un `provider` o `max_turns` inválido, una falla en Gate o Prepare (git o la API de GitHub), o "Publish" no pudo escribir el comentario (ver la fila de abajo) |
| No aparece el job | El PR es draft o viene de un fork |
| La anotación dice "excedió el tiempo límite" | El intento pasó de su tiempo máximo (15 s por turno permitido, mínimo 5 min). No se reintenta, porque otro intento tardaría lo mismo; el siguiente push o "Re-run jobs" lo vuelve a intentar |
| El comentario dice "Revisión incompleta" | El diff pasó el presupuesto, se acabaron los turnos, o el revisor declaró cobertura parcial; el detalle viene en el mismo comentario |
| Falla "Publish" con 403 | El workflow del repo no tiene `pull-requests: write` |

Los logs de cada paso llevan el prefijo `ai-review:`.

## Diferencias con CodeRabbit

- No hay comentarios inline por línea. Todo va en un solo comentario con referencias `archivo:línea`. Es a propósito, porque así el comentario se puede editar en su lugar sin duplicados.
- No hay chat con el bot (`@coderabbitai ...`), ni resumen del PR, ni walkthrough, ni diagramas.
- No corre linters. Asume que el CI del repo ya los corre.
- Nunca marca "Request changes" ni bloquea el merge. Es un check informativo.
- `path_instructions` de CodeRabbit se reemplaza por `.github/ai-review.md`, un solo archivo de reglas en prosa.

## Desarrollo

```bash
python3 -m unittest discover -s tests -v
```

CI corre las pruebas y `actionlint`. Este repo se revisa a sí mismo con `.github/workflows/ai-review.yml`, que usa la action local (`uses: ./`).
