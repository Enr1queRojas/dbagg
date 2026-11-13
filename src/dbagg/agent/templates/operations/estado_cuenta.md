# Operación seleccionada: estado_cuenta
Estado de cuenta completo de un cliente o proveedor con ventas, pagos y devoluciones.
No lo sustituyas por una lista de pagos. Si falta el período, omite period y el
servicio pide fechas. La identidad y el período deben resolverse antes del PDF.
Ejemplo (estado_cuenta): «Estado de cuenta de DEMO7 de este mes» →
  consultar_negocio(operation="estado_cuenta", customer="DEMO7", period="este_mes").
