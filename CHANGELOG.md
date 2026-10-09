# Cambios

## 0.2.2 — Segunda entrega de refactor

- Cinco operaciones verificadas para saldo, ranking y pagos por cliente; SQL parametrizado,
  comprobación de esquema, homónimos y códigos duplicados.
- Respuestas financieras desde datos SQL; se descarta prosa libre del modelo. Otras
  consultas conservan una salida explícitamente exploratoria, sin validar su semántica contable.
- Cobranza aplica `ACTIVA`, `FECHA`, períodos explícitos y empates del último pago;
  informa datos incompletos y límites sin sumar muestras ni inventar ceros.
- Calendario del negocio con `BUSINESS_TIMEZONE`, predeterminado `America/Mexico_City`.
- Prompt `customer-reports-v2`, regresiones sintéticas de negocio y seguimiento por webhook.
  La selección de intención por el modelo y SQL Server remoto requieren validación real.

## 0.2.1 — Primera entrega de refactor

- Paquete Python movido a `src/dbagg/`; comandos anteriores y `.env` de la raíz compatibles.
- Saldo desconocido/inválido y falta de fechas ya no producen riesgo verde; el HTML
  presenta el motivo y mantiene visibles los saldos desconocidos.
- Cobranza usa `FECHA` y conversión 103, limita el historial al corte y detecta fechas
  inválidas. El saldo es vigente; no se reconstruyen saldos históricos.
- TOP pequeños se conservan, grandes se acotan. OFFSET, PERCENT, WITH TIES y opciones
  de ejecución no soportadas se rechazan explícitamente.
- Un error entregado invalida la valoración de la respuesta anterior.
- Nuevas regresiones de cálculo, HTML/JavaScript y feedback; CI Windows/Linux y prueba
  de instalación del wheel fuera del checkout.

Quedan pendientes las entregas de cola persistente, evaluación y operación descritas
en `docs/refactor-roadmap.md`. La publicación del código no habilita producción.
