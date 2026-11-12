# Operación seleccionada: ranking_deuda
- ranking_deuda: ranking global de deuda actual, solo si piden quiénes deben más, los mayores o un
  top. Nunca lo uses para saldos de clientes nombrados. Omite customer, aunque hubiera un cliente en el
  historial. limit respeta la cantidad pedida entre 1 y 10; si no se indica, usa 5. No es riesgo,
  morosidad ni antigüedad de cartera. No recortes un pedido mayor de 10 sin aclaración de criterio.
Ejemplo (ranking_deuda): «Los cuatro que más deben» →
  consultar_negocio(operation="ranking_deuda", limit=4).
