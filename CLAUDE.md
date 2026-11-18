## Reglas de trabajo

Iguales en todos los repos de David. Si otro documento de este repo dice otra cosa sobre calidad,
candados, evidencia, rondas o pruebas, manda esto. Ninguna regla debe dejar un trabajo parado: si
una no se puede cumplir, se sigue y se anota por qué.

- Todo cambio entra por PR; nadie sube directo a la rama principal.
- Pruebas nuevas solo en 4 casos: dinero (precios, pujas, presupuestos, cobros), escribir datos de
  producción, instalar o borrar en las máquinas, y una falla que ya pasó en producción (una prueba,
  no una batería). Lo demás: basta comprobar que corre.
- Las pruebas que ya existen deben pasar antes de mergear. Si una ya falla igual en la rama
  principal, no frena: se anota en el PR.
- Revisa un modelo distinto del que escribió; si no está disponible, el siguiente que sí lo esté.
  Nunca se espera a una persona. Solo bloquea por seguridad, datos, el comportamiento pedido roto o
  uno de los 4 casos sin su prueba, siempre con el comando que lo reproduce. Lo demás va en notas.
- DeepSeek opina en el PR; el revisor decide. Un hallazgo Low nunca abre otra vuelta.
- Máximo 2 rondas con el mismo agente. Si lo que queda no es grave, se mergea y lo pendiente va en
  notas. Si queda algo grave, la 3.ª ronda la hace otro modelo más fuerte; solo se detiene si ese
  tampoco puede, y dice por qué.
- Un cambio grande que altera algo que ya corre en producción va detrás de un interruptor apagado
  y se enciende después.
- Los encargos o contratos para otro agente son cortos: qué debe pasar y cómo se comprueba.
- Nunca subas llaves ni contraseñas; no uses `--no-verify` sobre la revisión de secretos. Si marca
  algo que no es una llave, se agrega a `.gitleaksignore` con su razón y se sigue.
- No agregues compuertas (tamaño, lint, tipos, checklists, revisores extra, baterías, CI
  obligatorio) ni revisiones cruzadas sin que David lo pida.
