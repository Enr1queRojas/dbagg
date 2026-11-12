# Operación seleccionada: ultima_nota
- ultima_nota: última nota de una persona en todo el historial, con folio, fecha, partidas e
  importe. De un cliente es su última nota de crédito (su última compra a crédito); de un proveedor,
  su última nota de compra. Úsala para "última nota", "última compra", "última compra a crédito" o
  "nota de compra" de una persona. Una nota no es un pago: nunca respondas con ultimo_pago. Solo
  todo; con un período ("última nota de septiembre") usa fuera_alcance, sin enviar period ni rango.
Ejemplo (ultima_nota): «¿Qué fue lo último que nos compró Ana Demo?» →
  consultar_negocio(operation="ultima_nota", customer="Ana Demo").
