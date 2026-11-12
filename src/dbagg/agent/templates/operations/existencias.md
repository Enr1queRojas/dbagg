# Operación seleccionada: existencias
- existencias: existencias actuales de granos por producto, en su unidad y en toneladas, con
  total. producto es la palabra pedida; sin período ni customer. Precios, insumos o entradas de
  inventario no son existencias: usa otra_operacion.
Ejemplo (existencias): «¿Cuánto frijol queda?» → consultar_negocio(operation="existencias",
  producto="frijol").
