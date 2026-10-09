# Cambios

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

Quedan pendientes las entregas de evidencia, cola persistente y operación descritas
en `docs/refactor-roadmap.md`. La publicación del código no habilita producción.
