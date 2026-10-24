## Pruebas y revisiones
- Pruebas nuevas SOLO en 4 casos: dinero (pujas, precios, presupuestos, cobros), escribir datos de produccion
  (base del servidor, Amazon, Mercado Libre, Shopify, Odoo), instalar/borrar/deshacer en las maquinas, y una
  falla que ya paso en produccion (una prueba, no una bateria). Lo demas: basta comprobar que corre. Nunca
  pruebas de texto, de las pruebas ni mutantes. Un revisor no pide pruebas fuera de eso.
- Un revisor solo bloquea por seguridad, datos, el comportamiento pedido roto o un caso de los 4 sin su
  prueba, siempre con el comando que lo reproduce. Lo demas va en notas y se corrige en la misma ronda.
- Nunca subir contrasenas ni llaves a un repo.
