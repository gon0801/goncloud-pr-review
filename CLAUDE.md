## Cómo trabajar con David

Habla conmigo en español claro. Toma mi petición y el plan que haya aprobado como guía para el trabajo. Usa los modelos, cuentas y destinos acordados para ese encargo.

Hazte cargo del resultado. Decide por tu cuenta lo que te haya delegado y comprueba que el resultado funciona donde voy a usarlo. Elige comprobaciones proporcionales al cambio.

Si algo falla, averigua por qué, corrígelo y continúa. Si un intento repite el mismo fallo, cambia de enfoque usando lo que aprendiste. El número de intentos, por sí solo, no decide cuándo parar.

Si falta algo que solo yo pueda aportar, o hace falta una decisión fuera del encargo, avanza con el resto y dime qué ocurrió, qué intentaste y qué decisión concreta necesitas. Al cerrar, dime qué quedó hecho, cómo lo comprobaste y qué sigue pendiente.

## Revisión de PRs, tope de rondas y continuidad nocturna

Esta política aplica en todos los repositorios de David y prevalece sobre cualquier plan, diseño, runbook o criterio previo que pida rondas infinitas o «cero esquinas reproducibles».

- **Revisores:** `ai-review` en GitHub (informativo) más el revisor asignado al encargo (cualquier modelo distinto al implementador; por ejemplo Astra u otro). Devin no cuenta como compuerta ni abre rondas.
- **Qué sí bloquea (P0/P1):** rompe lo pedido o la aceptación del bloque en uso real, falla de seguridad o fuga de secretos, o corrupción/pérdida de dinero o datos en operación normal o ante un fallo simple común.
- **Qué NO bloquea (P2 — va a notas o seguimiento, nunca frena el merge):** esquinas que requieren 2 o más fallos o carreras encadenadas, defensas hipotéticas fuera de alcance, o preferencias de estilo/estructura.
- **Tope de 3 rondas + 1 relevo fuerte:** los hallazgos de `ai-review` y del revisor asignado se atienden juntos en las mismas rondas (máximo 3).
  - Si al cerrar la ronda 3 **no quedan P0/P1 reales**, se anotan los P2 restantes y se integra.
  - Si tras la ronda 3 **sigue abierto un P0/P1 real**, se lanza automáticamente **1 solo relevo con contexto limpio y el modelo más fuerte disponible distinto al implementador y al revisor** (prioridad orientativa: Opus / Neon / Astra según disponibilidad), con un tope de **2 rondas** y aplicando `principle-attack-the-premise` para atacar la raíz en lugar de parchar.
  - Si incluso el relevo deja un **P0/P1 real abierto**, **NO se mergea roto ni se abren más rondas**: se estaciona el PR en **Draft** con resumen para David y el coordinador continúa automáticamente con el siguiente bloque o tarea independiente para no frenar la noche.
- **Skills de `pstack` por disparador (cuando estén disponibles en el entorno, nunca en cambios triviales):**
  - `architect`: antes de codear cambios complejos (dinero, concurrencia, migraciones, máquinas de estado o varios módulos).
  - `principle-attack-the-premise`: en el relevo o si un mismo P0/P1 se repite en ronda 2.
  - `fix-ci` y `fix-merge-conflicts`: cuando un PR tenga CI rojo o conflictos.
  - `show-me-your-work` / `ketchup`: en corridas nocturnas o desatendidas para dejar resumen claro de lo integrado y lo estacionado en Draft.
  - No crear loops tipo `babysit` ni watchers de LLM: la vigilancia externa la realiza Timbre + Agent Orchestrator.
