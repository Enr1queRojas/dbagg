# Consultas de negocio verificadas

La segunda entrega de `refactor` incorpora contratos de consulta en
`src/dbagg/business/`. El modelo interpreta la intención y propone parámetros; el
código comprueba permisos, columnas, identidad y resultados, y redacta importes y
fechas directamente desde las filas. El texto libre del modelo nunca se entrega como
respuesta financiera. Versión del prompt: `customer-reports-v2`.

## Operaciones disponibles

| Pregunta | Operación | Fuente y regla |
| --- | --- | --- |
| ¿Cuánto debe Ana? | `saldo` | `CATALOGO_CLIENTES_DATA_V.[SALDO ACTUAL]`, sin recalcular acumulados |
| ¿Quiénes son los cinco que más deben? | `ranking_deuda` | Saldos positivos, descendentes; empates por código; cantidad de 1 a 10 |
| ¿Qué pagos hizo Ana esta semana? | `pagos` | Movimientos de `COBRANZA_DATA`, con límite explícito de 1 a 10 |
| ¿Cuándo pagó Ana por última vez? | `ultimo_pago` | Mayor `FECHA` válida de todo el historial; muestra empates |
| ¿Cuánto pagó Ana este mes? | `total_pagos` | `SUM(IMPORTE)` en SQL sobre el período; no suma una muestra |

Los objetos tienen esquema `proadel` y deben estar autorizados en `SQL_ALLOWED_TABLES`.
Las columnas se comprueban contra metadatos SQL antes de consultar. Los importes deben
ser de tipo numérico; un cambio a texto requiere revisar el contrato, no inferir conversiones.
No se añaden permisos ni se modifica información de negocio.

La búsqueda intenta código o nombre exactos y luego coincidencia parcial literal;
`%`, `_` y `[` del usuario no funcionan como comodines. Los valores van como parámetros
ODBC. Ante varios clientes se presentan nombres y códigos y se pide elegir, sin sumar
sus saldos. También se comprueba que el código no esté duplicado. El cliente propuesto
por el modelo debe aparecer en la pregunta o historial; la respuesta identifica nombre
más código para que se pueda comprobar la selección.

## Cobranza y períodos

- `CLIENTE` corresponde a `CODIGO`; solo `ESTADO='ACTIVA'`. No se hereda el filtro de
  importes positivos del reporte de riesgo. Se conservan importes negativos.
- La fecha es `FECHA`. Para tipos nativos se conserva fecha/hora; para texto
  `dd/MM/yyyy` se usa `TRY_CONVERT(datetime2, NULLIF(LTRIM(RTRIM(FECHA)), ''), 103)`.
  `NOTE_DATE` e `INSERTION_DATE` no sustituyen la fecha del pago.
- Último pago no tiene ventana de 365 días. Los movimientos con igual fecha/hora se
  muestran como empatados; ordenar por ID solo estabiliza la presentación.
- Fechas inválidas impiden confirmar el último pago. Los listados/totales por período
  advierten que hay movimientos que no pueden ubicarse y marcan el resultado parcial.
  Si falta un importe, no se confirma el total. Ausencia de filas no significa saldo cero.
- Se consulta una fila adicional para saber si el listado quedó limitado. También se
  limita la longitud del mensaje por filas completas y se informa si quedaron pendientes.
- `esta_semana` significa lunes hasta hoy; `este_mes` inicia el día primero. Semana/mes
  pasados son períodos completos. `rango` recibe inicio y fin inclusivos. SQL filtra
  `>= inicio` y `< día posterior al fin`, para conservar todo el último día.
- `BUSINESS_TIMEZONE=America/Mexico_City` es el valor predeterminado. Se puede cambiar
  a otra zona IANA. No se usa implícitamente la zona del servidor de alojamiento.
  Se asume que las fechas SQL representan el calendario del negocio; confirma ese criterio.

Los importes se presentan con dos decimales, sin atribuir moneda. La moneda, el corte
formal del saldo y el significado de reversos siguen pendientes de confirmación.
No hay cálculo de saldo histórico, conciliación, estado de cuenta completo ni score de
riesgo dentro de estas cinco operaciones. Un total global de cobranza necesita otro contrato.

## Exploración y evidencia

`consultar_sql` conserva la validación de lectura y la lista de permisos para otros
objetos, después de inspeccionar sus columnas. No puede consultar directamente las dos
fuentes de estos contratos. Su respuesta muestra datos con la etiqueta **exploratoria;
la definición de negocio requiere revisión**. No se considera una respuesta contable
verificada: el modelo todavía puede elegir una expresión o relación incorrecta.
No se utiliza su redacción para resumir o cambiar cifras. Un intento SQL fallido invalida
el resultado previo, evitando presentarlo como evidencia de la consulta que falló.

Las respuestas de negocio tienen estado (`answered`, `partial`, `empty`,
`needs_clarification`, `unavailable`), operación, fuentes del contrato e identificador
interno de evidencia en `Assistant.last_trace`. El identificador corresponde a esa
respuesta, no a un snapshot persistente ni a un registro auditable de todas las consultas.
La huella de contexto y versión del prompt siguen asociándose a las valoraciones;
la persistencia de trazas completas pertenece a H08. No se registran filas ni SQL crudo
como logs operativos.

Las fuentes/columnas/filtros estructurados del contexto local deben coincidir con el
contrato implementado; una diferencia se rechaza. Editar reglas en texto libre sigue
orientando al modelo, pero no reprograma las consultas verificadas. Para cambiar una
regla ejecutable se requiere modificar el contrato y sus regresiones.

## Validación antes de aprobar la entrega

Las pruebas usan exclusivamente datos sintéticos. Cubren interpretación simulada,
resolución, valores exactos, filtros, períodos, límites y seguimiento por webhook firmado.
SQLite ejecuta consultas traducidas para verificar selección y agregación; dobles de
ODBC comprueban parámetros y recortes. Esto **no acredita** compatibilidad del SQL Server
remoto, su collation, conversión de texto ni interpretación del modelo real.

Desde la PC autorizada, después de actualizar `refactor` y reiniciar el servicio:

1. `/reiniciar` y `/diagnostico`.
2. Consultar un cliente conocido y comparar código y saldo con el sistema de referencia.
3. Probar un apellido ambiguo, seleccionar el código y preguntar «¿y su último pago?».
4. Consultar pagos y total de esta semana; comparar fechas e inclusión/exclusión de estados.
5. Revisar un caso sin movimientos y otro con saldo desconocido o fechas faltantes, si existen.
6. Valorar con `/buena` o `/mala motivo` si el registro local está habilitado; revisar y
   anonimizar antes de exportar. No subir filas o capturas privadas al repositorio.

La elección de operación/cliente por el modelo aún requiere esta evaluación; un contrato
correcto no prueba que cualquier pregunta se interprete bien. Las lecturas SQL actuales
usan conexiones independientes y no garantizan un snapshot transaccional conjunto si
los datos cambian durante la consulta. La cola persistente, entrega Meta y recuperación
siguen pendientes en la próxima entrega H03/H11.
