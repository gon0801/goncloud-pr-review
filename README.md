# goncloud-pr-review

Revisión automática de pull requests para los repos de gon0801. Reemplaza a CodeRabbit sin tope de revisiones por hora. Corre Claude Code en modo solo-lectura dentro de GitHub Actions y usa DeepSeek como modelo. El revisor ve el repo completo en el commit del PR, no solo el diff, así que puede ir a buscar callers, tests y esquemas antes de opinar.

## Cómo funciona

Cada repo lleva un workflow chico (`.github/workflows/ai-review.yml`, copia de `templates/ai-review.yml`) que llama a la action de este repo (`action.yml`). La action hace cinco pasos.

1. **Gate.** Busca el comentario fijo del bot en el PR. Si ya revisó exactamente ese commit, termina sin gastar tokens.
2. **Prepare.** Calcula el diff contra el merge-base y saca lockfiles, binarios, `dist/`, `build/` y `vendor/` de la raíz, `node_modules/` a cualquier profundidad, y lo que el repo agregue en `exclude`. Ordena lo restante con el código primero, luego config y al final docs. Si el diff pasa de `max_diff_bytes`, lo que no cabe queda listado como no revisado.
3. **Install.** Instala Claude Code con la versión fijada.
4. **Review.** Corre `claude -p` apuntado a `https://api.deepseek.com/anthropic` con `--restricted --safe-mode --strict-mcp-config --tools Read,Grep,Glob`. No puede ejecutar código, escribir archivos, salir a la red, ni leer fuera del checkout y del directorio de trabajo. También ignora los settings, hooks, MCP y CLAUDE.md que traiga el PR. Si falla, reintenta una vez.
5. **Publish.** Edita el comentario fijo del PR, o lo crea si no existe. El comentario lleva un marcador oculto con el SHA revisado. Antes de publicar se borra del texto cualquier aparición de la API key o del token.

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

El único secret es `DEEPSEEK_API_KEY` (se saca en platform.deepseek.com). En una cuenta personal los secrets van repo por repo.

## Operación

- **Cambiar de modelo.** Pasa `model:` en el `with:` del workflow del repo, por ejemplo `deepseek-v4-pro[1m]`. Para cambiarlo en todos los repos, cambia el default en `action.yml`. El sufijo `[1m]` le dice a Claude Code que la ventana de contexto es de 1M.
- **Apagarlo en un repo sin tocar código.** `gh variable set AI_REVIEW_DISABLED --body true -R gon0801/mi-repo`. Para prenderlo, `gh variable delete AI_REVIEW_DISABLED -R ...`.
- **Pedir otra revisión del mismo commit.** En la pestaña Checks del PR, "Re-run jobs" sobre `AI review`. Un re-run se salta el gate a propósito.
- **Revisión nueva.** Se hace sola con cada push.

## Modelo y costo

El default es `deepseek-flash`, DeepSeek-V4.1-Flash con 1M de contexto. En hora pico cuesta $0.30/M de entrada, $0.006/M de entrada en caché y $1.20/M de salida, y fuera de pico la mitad. Es el mejor precio/rendimiento de DeepSeek hoy. `deepseek-v4-pro` cuesta unas 4.4× más.

Cada comentario trae en "Alcance de la revisión" los turnos, los tokens y el costo aproximado de esa revisión, y lo mismo queda en el Summary del job. Una revisión típica debería salir en centavos. Para ~1,500 revisiones al mes (500 PRs con ~3 pushes cada uno), calcula decenas de dólares, no cientos. Los repos públicos no gastan minutos de Actions; en los privados, cada revisión toma unos 3 a 6 minutos del plan.

## Problemas comunes

| Síntoma | Causa probable |
|---|---|
| El job falla en "Check inputs" | Falta el secret `DEEPSEEK_API_KEY` en ese repo |
| El job falla en "Review" con 401/402 | Key inválida o sin saldo en DeepSeek |
| El job falla en "Review" con modelo desconocido | DeepSeek renombró sus modelos; actualiza el default `model` en `action.yml` |
| No aparece el job | El PR es draft, viene de un fork, o `AI_REVIEW_DISABLED` está en `true` |
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
