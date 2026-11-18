## Reglas de trabajo

Iguales en todos los repos de David. Si otro documento de este repo dice otra cosa sobre calidad,
candados, evidencia, rondas o pruebas, manda esto.

- Todo cambio entra por PR; nadie sube directo a la rama principal.
- Pruebas nuevas solo en 4 casos: dinero (precios, pujas, presupuestos, cobros), escribir datos de
  producción, instalar o borrar en las máquinas, y una falla que ya pasó en producción (una prueba,
  no una batería). Lo demás: basta comprobar que corre.
- Las pruebas que ya existen deben pasar antes de mergear: en CI si el repo lo tiene; si no, en
  local.
- Revisa uno solo, distinto de quien escribió. Solo bloquea por seguridad, datos, el comportamiento
  pedido roto o uno de los 4 casos sin su prueba, siempre con el comando que lo reproduce. Lo demás
  va en notas.
- DeepSeek opina en el PR; el revisor decide. Un hallazgo Low nunca abre otra vuelta.
- Máximo 2 rondas de revisión por PR; una 3.ª solo por algo grave.
- Los cambios grandes van detrás de un interruptor apagado y se despliegan poco a poco.
- El tamaño de archivos y funciones es una meta, no un freno.
- Los encargos o contratos para otro agente son cortos: qué debe pasar y cómo se comprueba.
- Revisión cruzada con otro modelo solo cuando David la pide.
- Nunca subas llaves ni contraseñas; no uses `--no-verify` sobre la revisión de secretos.
- No agregues compuertas, checklists, revisores, baterías ni CI obligatorios sin que David lo pida.
