## Reglas de trabajo

Iguales en todos los repos de David. Si otro documento de este repo dice otra cosa sobre calidad,
candados, evidencia, rondas o pruebas, manda esto, salvo un plan que David aprobó para ese trabajo:
ahí manda el plan. Si una regla no se puede cumplir, el trabajo sigue y se anota por qué en el PR.
Solo se detiene ese paso, hasta resolverlo, por secretos, por seguridad o por algo que el plan no
autoriza (borrar datos, gastar, escribir en producción, hablar con terceros en nombre de David),
que espera su aprobación. Lo demás no espera a nadie.

Sin excepción:

- Todo cambio entra por PR; nadie sube directo a la rama principal.
- Las llaves y contraseñas quedan fuera del repo, y la revisión de secretos corre siempre (sin
  `--no-verify`). Si marca algo que no es una llave, se agrega a `.gitleaksignore` con su razón y
  se sigue.

Con criterio:

- Pruebas obligatorias en 4 casos: dinero (precios, pujas, presupuestos, cobros), escribir datos de
  producción, instalar o borrar en las máquinas, y una falla que ya pasó en producción (una prueba,
  no una batería). Fuera de esos casos, una prueba solo si es la forma más barata de comprobar que
  funciona. Por defecto no se hacen baterías, pruebas de texto ni mutantes; sí cuando son la forma
  más barata de demostrar un riesgo de dinero o de datos (por ejemplo, el texto exacto con el que
  alguien decide). Fuera de eso, el revisor no las pide.
- Las pruebas que ya existen deben pasar antes de mergear. Si una ya falla igual en la rama
  principal, no frena: se anota en el PR.
- El revisor normal es un modelo distinto del que escribió; si no está disponible, el siguiente que
  sí lo esté. La revisión no espera a una persona. Solo bloquea por seguridad, datos, el
  comportamiento pedido roto o uno de los 4 casos sin su prueba, con el comando que lo reproduce o,
  si no se puede reproducir, la línea exacta y el riesgo. Lo demás va en notas.
- Sin tope de rondas mientras cada una traiga avance. Si queda un bloqueo concreto, el implementador
  lo corrige y el revisor comprueba la corrección; cuando solo quedan notas, se integra el PR. Si el
  mismo bloqueo vuelve 3 rondas seguidas sin avance, lo revisa otro modelo; si ese tampoco lo
  resuelve, se escala a David con el bloqueo y su evidencia.
- Todo cambio que toca producción dice en el PR cómo regresarlo.
- Compuertas (tamaño, lint, tipos, checklists, baterías, CI obligatorio) y revisores extra además
  del normal se agregan solo si David lo pide.
