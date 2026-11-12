# Operación seleccionada: total_pagos
- total_pagos: total calculado por la herramienta sobre todos los pagos del período, no la suma
  de una muestra ni la reconstrucción del saldo actual. "Cuánto pagó" siempre es total_pagos, no
  pagos ni ultimo_pago.
Ejemplo (total_pagos): «¿Cuánto pagó Ana Demo el mes pasado?» →
  consultar_negocio(operation="total_pagos", customer="Ana Demo", period="mes_pasado").
