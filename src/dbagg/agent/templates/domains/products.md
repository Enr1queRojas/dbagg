# Productos
- existencias: existencias actuales de granos por producto, en su unidad y en toneladas, con
  total. producto es la palabra pedida (maíz, frijol); sin periodo ni customer. No hay bodegas.
- ventas: ventas globales (crédito + contado − devoluciones) en pesos y toneladas, comparadas
  con el periodo anterior. period por defecto este_mes; producto opcional; sin customer.
  Contra las mismas fechas del año anterior usa comparar=anio_anterior con el periodo más
  reciente (mes en curso = este_mes; otro mes = rango del día 1 al último). Las ventas a un cliente
  concreto no tienen operación: usa fuera_alcance, no criterio ni customer.
