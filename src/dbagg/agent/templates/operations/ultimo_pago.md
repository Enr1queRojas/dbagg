# Operación seleccionada: ultimo_pago
- ultimo_pago: pagos de la última fecha válida en todo el historial. No impongas un año o período
  arbitrario. Puede devolver varios pagos del mismo día; no elijas un recibo por ID o folio. Con un
  período ("último pago en septiembre") no aplica: usa fuera_alcance, sin enviar period ni rango.
Ejemplo (ultimo_pago): «¿Cuándo pagó por última vez Ana Demo?» →
  consultar_negocio(operation="ultimo_pago", customer="Ana Demo").
