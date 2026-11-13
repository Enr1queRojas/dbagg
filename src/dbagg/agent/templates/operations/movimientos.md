# Operación seleccionada: movimientos
- movimientos: compras por nota y pagos activos de un proveedor, del más reciente al más antiguo.
  Admite períodos; sin período muestra los más recientes de todo el historial. Solo existe para
  proveedores; no la uses para clientes ni para gastos.
Ejemplo (movimientos): «¿Qué le compramos y pagamos a Proveedor Demo este mes?» →
  consultar_negocio(operation="movimientos", customer="Proveedor Demo", period="este_mes").
