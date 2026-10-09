# Refactor por entregas

Base auditada: `4a55ebf`. Rama de trabajo: `refactor`. El responsable eligió comenzar
por exactitud y regresiones; la siguiente entrega se revisa por separado.

| Hallazgo | Estado tras segunda entrega | Pendiente |
| --- | --- | --- |
| H01: saldo desconocido verde | Corregido en cálculo y HTML; nulos/no numéricos/infinitos quedan sin datos con motivo | Contrastar casos reales |
| H02: fecha incorrecta | Cobranza usa FECHA, conversión 103, corte superior y detección de fechas inválidas | Validación SQL real; confirmar fecha de créditos, reglas de reversos y calibrar score |
| H03: pérdida de mensajes | Pendiente | Cola persistente, recuperación y orden por conversación |
| H04: cifras sin evidencia | Texto financiero libre descartado; respuesta de negocio desde filas y estado explícito | Evaluar elección de intención/cliente con modelo real; otras fuentes son exploratorias |
| H05: límites SQL alterados | TOP pequeño se conserva; TOP grande se acota; paginación no soportada se rechaza | No habilita paginación todavía |
| H06: reglas solo en prompt | Cinco operaciones parametrizadas para clientes y pagos; reglas fuera del modelo | Validar tipos/fechas en SQL Server; extender contratos a otros reportes |
| H07: voto sobre otra respuesta | Un error entregado invalida la referencia anterior | Valoración por ID de mensaje y clasificación de fallos de disponibilidad |
| H08: evaluación/trazas | Pendiente | Runner de referencia, causas, tokens, tiempos y versiones |
| H09: pruebas de límites de datos | Fixtures del cálculo, presentación JS, límites y feedback; CI Windows/Linux y smoke del wheel | Integración con SQL Server y pruebas de recuperación/concurrencia |
| H10: costo/duración | Opciones de ejecución como MAXDOP se rechazan | Deadline global, caché de metadatos y presupuesto |
| H11: entrega Meta | Pendiente | Bandeja de salida, ID y estados, reintentos acotados |
| H12: configuración/datos | Pendiente | Política ODBC común, minimización y retención |

## Cambios de estructura

`src/dbagg/` contiene el paquete instalable. `config/`, `docs/`, `evals/`, `scripts/`
y `tests/` contienen recursos de desarrollo. La raíz mantiene tres adaptadores pequeños
para los comandos anteriores. No se versionan carpetas vacías, credenciales, bases
locales o reportes generados.

## Evidencia de la primera entrega

Las regresiones usan datos sintéticos. Comprueban fórmula con saldo conocido, saldo
desconocido/negativo/cero, claves duplicadas o vacías, falta de fecha, fechas inválidas,
dataset vacío, presentación del saldo desconocido, conservación de TOP y voto después
de un error. La extracción SQL se inspecciona con dobles de prueba; esto no acredita
compatibilidad del servidor ni exactitud contable sobre datos de producción.

El reporte conserva el filtro `IMPORTE>0` como parte del método histórico del score.
No se extiende esa regla al agente de cobranza, cuya definición confirmada solo exige
`ESTADO='ACTIVA'`. El saldo sigue siendo actual; `hoy` limita pagos y cargos, pero no
permite usar el resultado como backtest histórico. La fecha de créditos no fue confirmada.

## Siguientes entregas

1. Entrega confiable: H03/H11; debe completarse antes de ampliar usuarios simultáneos.
2. Evaluación y rendimiento: H08/H09/H10.
3. Operación: H12, respaldo, arranque supervisado y validación del despliegue.

## Evidencia de la segunda entrega

Contratos y alcance en [business-queries.md](business-queries.md). Las regresiones
sintéticas cubren homónimos, códigos duplicados, saldo desconocido, cancelaciones,
importes negativos, fechas ausentes, empates del último pago, límites de listados,
períodos y respuestas sin evidencia. Un webhook firmado recorre agente y consultas
con dependencias externas simuladas, incluida la pregunta de seguimiento.

El calendario del agente usa una zona IANA explícita, predeterminada a
`America/Mexico_City`. Las pruebas locales no validan SQL Server remoto ni la
interpretación de un modelo real. Las consultas exploratorias no se presentan como
definiciones de negocio verificadas; la selección de la operación sigue necesitando
evaluación. No se considera concluida la preparación para producción.
