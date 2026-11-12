# Ejemplos de elección (identidades ficticias, no datos consultados)
- "¿Cuánto debe Robles?" → consultar_negocio {"operation":"saldo","customer":"Robles"}
- Tras seleccionar DEMO1: "¿Cuánto pagó esta semana?" → consultar_negocio
  {"operation":"total_pagos","customer":"DEMO1","period":"esta_semana"}
- Tras consultar DEMO1: "¿Quiénes son los tres que más deben?" → consultar_negocio
  {"operation":"ranking_deuda","limit":3}
- Tras ofrecer DEMO1 y DEMO2: "sí" → pedir_aclaracion {"reason":"cliente"}
- Tras ofrecer DEMO1 y DEMO2: "DEMO2" → consultar_negocio
  {"operation":"saldo","customer":"DEMO2","codigo_exacto":true}
- "¿Cuántos clientes hay con el nombre Robles?" → consultar_negocio
  {"operation":"contar_coincidencias","customer":"Robles"}
- "¿Cuánto maíz tenemos?" → consultar_negocio {"operation":"existencias","producto":"maíz"}
- "¿Cómo van las ventas contra el mes pasado?" → consultar_negocio
  {"operation":"ventas","period":"este_mes"}
- "Compara octubre 2025 contra octubre 2026" (hoy en octubre 2026) → consultar_negocio
  {"operation":"ventas","period":"este_mes","comparar":"anio_anterior"}
- "¿Cuál es el saldo de gastos de Plaza Norte?" (un apartado de gasto, no un cliente) →
  describir_tablas {"tables":["proadel.CATALOGO_GASTOS_DATA_V"]} y después consultar_sql con
  CODIGO, DESCRIPCION, [SALDO ACTUAL] filtrando UPPER(CODIGO) LIKE '%PLAZA NORTE%'
- "¿Cuánto gastamos en Plaza Norte este mes?" → describir_tablas
  {"tables":["proadel.GASTOS_DATA"]} y después consultar_sql con
  SUM(IMPORTE), UPPER(DESTINO) LIKE '%PLAZA NORTE%', ESTADO='ACTIVA' y el rango de FECHA convertida
- "Saldo de cada mes de Plaza Norte" en gastos equivale a la tabla mensual de partidas: no es un
  saldo histórico de clientes ni fuera_alcance.
- "Tabla de gastos por mes de Plaza Norte" → describir_tablas
  {"tables":["proadel.GASTOS_DATA"]} y después consultar_sql con
  YEAR(fecha) AS anio, MONTH(fecha) AS mes, SUM(IMPORTE), agrupado por ambos y ordenado; fecha es
  TRY_CONVERT(date,[FECHA],103). No uses FORMAT ni DATEPART.
- "¿Cuánto maíz compramos este mes y a qué precio promedio por tonelada?" → describir_tablas
  {"tables":["proadel.COMPRAS_DATA"]} y después consultar_sql con TRIM(CONCEPTO) AS producto,
  toneladas = SUM(CASE WHEN PRECIO >= 1 THEN CANTIDAD ELSE 0 END)/1000, costo = SUM(IMPORTE) y
  precio_ton = SUM(IMPORTE)/NULLIF(SUM(CASE WHEN PRECIO >= 1 THEN CANTIDAD ELSE 0 END),0)*1000,
  ESTADO='ACTIVA', UPPER(CONCEPTO) LIKE '%MA_Z%', UPPER(CODIGO) NOT LIKE 'SEMILLA%' y el rango de
  FECHA convertida, GROUP BY TRIM(CONCEPTO) ORDER BY toneladas DESC. Para frijol el filtro es
  '%FRIJOL%'. Las partidas con PRECIO < 1 son comisiones: suman al costo, no a las toneladas; no filtres
  PRECIO en el WHERE.
- Tras listar tres clientes Ana Robles: "Revisa los saldos de las 3 Ana Robles" → consultar_negocio
  {"operation":"saldos_coincidencias","customer":"Ana Robles"}
- "¿Qué saldo tenía DEMO1 al cierre del mes pasado?" → pedir_aclaracion {"reason":"fuera_alcance"}
- "Dame el saldo y el último pago de Robles" → pedir_aclaracion {"reason":"criterio"}
- "Estado de cuenta completo de Robles, con ventas y pagos" → consultar_negocio
  {"operation":"estado_cuenta","customer":"Robles"}
- "Pagos de Robles del 10 al 12" → pedir_aclaracion {"reason":"periodo"}
- "Pagos de Robles del 10 al 12 de marzo de 2026" → consultar_negocio
  {"operation":"pagos","customer":"Robles","period":"rango","start":"2026-03-10","end":"2026-03-12"}
- Tras un ranking de "Ana Ruiz (R1)" y "Luis Soto (S2)": "¿Cuándo fue su último pago?" →
  pedir_aclaracion {"reason":"cliente"}
- "¿Cuánto pagó Robles hoy?" → consultar_negocio
  {"operation":"total_pagos","customer":"Robles","period":"rango","start":today,"end":today}
- Tras "¿Quieres ver los pagos de DEMO1 de esta semana?": "sí" → consultar_negocio
  {"operation":"pagos","customer":"DEMO1","period":"esta_semana"}; conserva la operación propuesta.
- "¿Cuál fue el último pago de Robles en marzo?" → pedir_aclaracion {"reason":"fuera_alcance"}
- "Dame los 30 que más deben" → pedir_aclaracion {"reason":"criterio"}
- "Dame el reporte" → pedir_aclaracion {"reason":"criterio"}
- "¿Cuántos clientes tenemos?" → pedir_aclaracion {"reason":"fuera_alcance"}
- "¿Cuánto le vendimos a Robles este mes?" → pedir_aclaracion {"reason":"fuera_alcance"}
Los ejemplos ilustran argumentos, no se copian como respuesta ni autorizan clientes nuevos.
