# Contexto de negocio para el agente

El agente carga `business_context.default.json` en cada pregunta. El archivo incluye
rutas de consulta y reglas para evitar confundir saldos, ventas, cobros y existencias.
Son indicaciones para el modelo, no una garantía de exactitud contable.

| Pregunta de negocio | Primera fuente a inspeccionar | Evidencia disponible |
| --- | --- | --- |
| ¿Cuánto debe un cliente? ¿Quién debe más? | `proadel.CATALOGO_CLIENTES_DATA_V` | Confirmados por el responsable: `CODIGO`, `NOMBRE`, `[SALDO ACTUAL]`. Leer el saldo directamente. Unicidad, moneda y corte pendientes. |
| Reporte/estado de cuenta de un cliente | Catálogo para resumen; cobranza, crédito, contado y devoluciones como detalle candidato | Flujo prioritario confirmado: movimientos por cliente. Correspondencias de claves y reglas de detalle pendientes. |
| ¿Cuándo pagó? ¿Qué pagos tuvo? | `proadel.COBRANZA_DATA` | El reporte usa CLIENTE, IMPORTE, NOTE_DATE, ESTADO; pagos positivos activos. Alcance: historial usado por el reporte de riesgo. |
| Total general de ventas, solo si se pide expresamente | `proadel.VENTAS_DIARIAS_V` | Candidata por nombre, secundaria al reporte del cliente. Definición contable pendiente. |
| ¿Qué existencias hay? | `proadel.ALMACENES_DISPONIBLES_V` | Candidata por nombre; podría listar almacenes, no cantidades. Inspeccionar y confirmar unidad. |
| ¿Cuánto debemos a productores? | `proadel.CONTROL_PRODUCTORES_DATA_V` | Candidata por nombre; faltan fórmula, temporada y relaciones. |
| ¿Cuánto compramos o debemos a proveedores? | `proadel.REP_COMPRAS`, `proadel.CATALOGO_PROVEEDORES_DATA_V` | Candidatas por nombre; compras, pagos y saldo son métricas distintas. |
| ¿Qué clientes tienen riesgo? | `score_riesgo.py` | Cálculo del reporte; todavía no disponible como herramienta de WhatsApp. |

El modelo sigue obligado a inspeccionar columnas y consultar solo los objetos de
`SQL_ALLOWED_TABLES`. Un nombre sugerido en el contexto no habilita permisos.
Los objetos de importación, temporales e históricos no se usan como fuente preferida
para responder totales vigentes.

## Completar las definiciones sin inventar columnas

Comenzar por saldo actual, reporte de movimientos del cliente, cobranza y último pago.
No es necesario definir ventas globales para responder saldo vigente. Para cada concepto registrar:

1. Qué significa para el negocio y con qué pantalla/reporte se compara.
2. Objeto de origen y columnas reales de clave, nombre, fecha, importe y estado.
3. Qué representa una fila y qué clave es única.
4. Filtros: cancelaciones, devoluciones, ajustes y documentos activos.
5. Moneda/unidad, impuestos y fecha usada para el período.
6. Relaciones documentadas, incluyendo cardinalidad para no multiplicar importes.

El contexto distingue estas evidencias:

- `confirmed_by_owner`: definición confirmada por el responsable del negocio.
- `implemented_in_report`: regla que existe en el código del reporte; no se generaliza
  automáticamente a toda la contabilidad.
- `user_reported_source`: fuente indicada y probada por el usuario, aún sin mapeo completo.
- `candidate_by_name`: sirve para orientar la inspección; no confirma fórmulas.
- `pending`, `null` o `confirmed:false`: información pendiente; no inferirla como hecho.

## Ajustes locales

`business_context.json` es opcional. Sus diccionarios se combinan con los valores del
archivo base y sus valores tienen prioridad. Las listas se reemplazan completas;
`null` expresa desconocido. No es necesario copiar el archivo base ni editar Python.
Los archivos existentes se conservan. Un JSON inválido o demasiado grande produce un
error explícito y no se ignora silenciosamente.
Se conserva el límite previo de 16.000 caracteres para cada archivo y se permiten
32.000 para la mezcla, de modo que añadir el contexto base no invalide archivos
locales que ya funcionaban. Ambos cuentan como entrada al modelo y afectan el consumo.

`business_context.example.json` es una plantilla vacía de ajustes opcionales, para no
sobrescribir definiciones confirmadas con marcadores null. No copiar sobre un contexto
local existente. Si copiaste una versión anterior, revisa los null de
`topics.customer_balance.columns`: tienen prioridad sobre los nombres ya confirmados
en el archivo base. Una vez confirmado un concepto, guardar
`status: "confirmed_by_owner"`, completar definición/columnas y retirar de `pending`
solo las preguntas resueltas. Las relaciones no deben inferirse solo por nombres iguales.

Verificar la carga desde la carpeta `dbagg`:

```cmd
.\.venv\Scripts\python.exe business_context.py
```

El comando valida el JSON combinado sin consultar SQL, llamar a OpenAI ni imprimir
su contenido. La actualización del archivo se recoge en la siguiente pregunta.
Después de cambiar una definición, `/reiniciar` borra respuestas anteriores de tu sesión.

## Validación con el responsable

Comparar preguntas conocidas con el sistema: saldo de un cliente, ranking de deuda,
movimientos de un período y último pago. Usar ejemplos con nombres ambiguos, devoluciones
y períodos sin operaciones. La prueba debe confirmar qué mide la respuesta, no solo
que SQL termina sin errores. Las pruebas automatizadas verifican la carga del contexto;
no validan el significado de las columnas de una base remota.

## Mapeo confirmado del catálogo de clientes

`CODIGO` identifica, `NOMBRE` permite buscar y `[SALDO ACTUAL]` responde cuánto debe.
También existen `LIMITE_DE_CREDITO`, `TOTAL_CREDITO`, `TOTAL_COBRANZA`,
`TOTAL_DEV_VENTA`, `SALDO_INICIAL`, `ESTADO`, `DIRECCION` y `TELEFONO`.
Los totales no sustituyen el saldo ni prueban movimientos de una semana/mes.
No se ha confirmado una fórmula para reconciliarlos. Dirección y teléfono no se
seleccionan por defecto en consultas financieras.

La siguiente definición pendiente es si `COBRANZA_DATA.CLIENTE` corresponde a
`CATALOGO_CLIENTES_DATA_V.CODIGO`, junto con la regla de pagos válidos. El reporte
Python usa `NOTE_DATE`, `IMPORTE`, `ESTADO='ACTIVA'` e importes positivos; el último
día con pagos no necesariamente corresponde a un único recibo. Esta regla sigue
etiquetada como implementada en el reporte hasta confirmación del responsable.
