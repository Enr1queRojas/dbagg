# Estado de cuenta en PDF por WhatsApp

## Para qué

El equipo consulta estados de cuenta en el ERP. Este reporte permite pedirlos desde WhatsApp y recibir
un PDF del mismo contenido, tanto de clientes como de proveedores.

El PDF se queda con quien lo pidió: es un documento interno, no se envía a terceros.

## Flujo

1. Alguien pide el estado de cuenta o reporte de un cliente o proveedor.
2. El bot aclara quién es, con la desambiguación que ya existe.
3. Si la petición no trae fechas, el bot las pregunta y no genera nada todavía.
4. Con persona y fechas, el bot genera el PDF, lo envía y manda un resumen en otro mensaje.

```text
Usuario: Estado de cuenta de Cuervo
Bot:     Encontré varios proveedores: ... ¿Cuál quieres consultar?
Usuario: JOHAN WIEBE
Bot:     Proveedor JOHAN WIEBE (código CUERVO)
         ¿De qué fechas quieres el estado de cuenta?
Usuario: del 30 de agosto a hoy
Bot:     [PDF]
Bot:     Proveedor JOHAN WIEBE (código CUERVO)
         Estado de cuenta del 30/08/2026 al 04/11/2026.
         Saldo anterior: ... Saldo al 04/11/2026: ... Movimientos: N.
```

## Decisiones

**Del responsable**

- Clientes: solo lo que afecta el saldo, es decir, créditos, cobranza y devoluciones. Sin ventas de contado.
- Proveedores: compras y pagos.
- Primero se aclara la persona, después se preguntan las fechas.
- Entrega en PDF, a quien lo pidió.
- Se desarrolla en la rama `feature/providers`, junto con las consultas de proveedores.

**Propuestas en este diseño, pendientes de aprobación**

- Tamaño carta horizontal, con las columnas del ERP. Encabezado y títulos de columna en cada página.
- Se genera en memoria con ReportLab y nunca se escribe en disco.
- Una petición con fechas ya indicadas no vuelve a preguntarlas.
- Límite de 2,000 filas. Si se supera, no se genera un PDF parcial y se piden fechas más cortas.
- Si el PDF no se puede enviar, el bot lo dice y pide reintentar.

## Evidencia

Comprobado en SQL Server el 2026-11-04, solo con lecturas:

- A.ESPARZA3, del 30/08/2026 al 04/11/2026, reproduce el ERP al centavo: saldo anterior 49,485.52,
  crédito 58,415.40, cobranza 48,050.32 y saldo final 59,850.60.
- Clientes: `SALDO_INICIAL` + créditos `TOTAL` − cobranza `IMPORTE` − devoluciones `IMPORTE` =
  `[SALDO ACTUAL]` en 423 de 423, solo con `ESTADO = 'ACTIVA'`.
- Proveedores: `SALDO_INICIAL` + compras `IMPORTE` − pagos `IMPORTE` = `[SALDO ACTUAL]` en 228 de 228.
- `FECHA` es texto dd/MM/yyyy en estas tablas. No hay fechas inválidas en créditos, compras ni pagos activos.
- No se encontró una tabla de devoluciones de compra.

Meta permite PDF de hasta 100 MB. Se sube a `/{phone_id}/media` y se envía con un mensaje `document`
que lleva el ID y `filename`. La documentación consultada indica que el documento no admite pie de
texto, por lo que el resumen va aparte. Aún no se ha hecho un envío real.

## Cálculo

- **Saldo anterior** = `SALDO_INICIAL` + cargos activos antes de la fecha inicial − abonos activos antes
  de esa fecha.
- **Saldo acumulado** de cada fila = saldo anterior + cargos − abonos hasta esa fila.
- **Saldo al final del período** = saldo acumulado de la última fila, o el saldo anterior si no hay filas.
- **Cuadre** = `SALDO_INICIAL` + todos los cargos activos − todos los abonos activos, sin importar el
  período, comparado con `[SALDO ACTUAL]`, con tolerancia de 0.01.

Si el período termina antes de hoy, el saldo final no es el saldo actual. El encabezado muestra ambos:
saldo al final del período y saldo actual.

Si no cuadra, se entrega el PDF con un aviso visible en el encabezado y se repite en el resumen. Las
filas sin fecha válida no se pueden ubicar: se cuentan y se avisa de la misma forma. Nunca se ocultan.

Orden de las filas: fecha, luego cargos antes que abonos, luego folio e ID. El ERP puede ordenar de otra
forma los movimientos de una misma fecha; el saldo final no cambia.

## Columnas

| Columna del PDF | Crédito o compra | Cobranza o pago | Devolución (solo clientes) |
|---|---|---|---|
| Tipo | `CRÉDITO` o `COMPRA` | `COBRANZA` o `PAGO` | `DEVOLUCIÓN` |
| Fecha | `FECHA` | `FECHA` | `FECHA` |
| Folio | `FOLIO` | `FOLIO` | `FOLIO` |
| Cantidad | `CANTIDAD` | 0 | `CANTIDAD` |
| Código | `CODIGO` | `BANCO` | `CODIGO` |
| Concepto | `CONCEPTO` | `ABONO` | `CONCEPTO` |
| Precio | `PRECIO` | 0.00 | `PRECIO` |
| Total | Clientes `TOTAL`; proveedores `IMPORTE` | 0.00 | 0.00 |
| Abono | 0.00 | `IMPORTE` | 0.00 |
| Devolución | 0.00 | 0.00 | `IMPORTE` |
| Saldo | Acumulado | Acumulado | Acumulado |

El ERP llama «IMPORTE» a la columna de precio. En el PDF se llama «Precio», porque ese es su contenido.
En proveedores se omite la columna Devolución.

No sumar `TOTAL` en proveedores: varía dentro de 30 folios.

## Piezas

### 1. Consulta del estado de cuenta (`src/dbagg/business/statements.py`, nuevo)

Devuelve datos, sin formato: identidad, período, saldo anterior, filas, saldo final, saldo actual y
avisos. Consulta `SALDO_INICIAL` y `[SALDO ACTUAL]` del catálogo y los movimientos activos, con
parámetros, como las clases existentes. Rechaza fechas invertidas o inválidas.

### 2. Generador de PDF (`src/dbagg/reporting/statement_pdf.py`, nuevo)

Recibe esos datos y devuelve `bytes`. Los importes salen de los datos, nunca del modelo. Nombre del
archivo: `estado-cuenta-{código}-{inicio}-{fin}.pdf`, con el código reducido a caracteres seguros. Los
metadatos del PDF no incluyen datos de la persona. Debe mostrar nombres con acentos y `Ñ`.

ReportLab se fija en `requirements.txt` y `pyproject.toml`.

### 3. Envío por Meta (`src/dbagg/integrations/meta.py`)

`send_document(sender, content, filename)`: sube el archivo y envía el mensaje `document`, con los
mismos reintentos que `send` para 429, 5xx y timeouts. Registra solo etapa y códigos de error.

### 4. Conversación

- Operación nueva `estado_cuenta` en `consultar_negocio`, para clientes y proveedores. Pasa por la misma
  decisión de entidad.
- Sin fechas: tras confirmar a la persona, responde con su identidad y pregunta las fechas. Esa línea
  conserva el contexto en el historial, igual que las demás respuestas.
- Con fechas: `BusinessAnswer` lleva también el documento, y `services/conversation.py` lo envía antes
  del resumen.
- El outbox y el historial guardan solo el resumen. El PDF nunca se guarda en ellos.
- Si el envío falla, el resumen se reemplaza por un aviso de que no se entregó.

### 5. Prompts

Actualizar `system.md`, `tools.json` y las copias de `config/prompts`. `agent-v7.md` está cerca del límite
de 24,000 caracteres, así que el texto debe ser breve. Comprobar con `Settings.from_env()` que el bot arranca.

## Fuera de alcance

- Reportes programados o enviados a terceros.
- Logotipo y formato para clientes.
- Excel u otros formatos.
- Ventas de contado.
- Un reporte que combine cliente y proveedor.

## Lo que sería un fracaso aunque funcione

- Un PDF con cifras de otra persona.
- Un PDF cuyo saldo no cuadre sin que lo diga.
- Decir que se entregó un PDF que no llegó.
- Que un PDF o sus datos queden en disco, logs o el outbox.
- Generar un reporte sin confirmar persona y fechas.

## Pruebas

- **Unitarias:** saldo anterior, acumulado, cuadre, período sin filas, período antiguo, filas sin fecha,
  límite de filas, entidad correcta, y que el PDF sea legible y contenga las cifras esperadas.
- **Meta simulado:** carga, envío, reintentos, fallo sin falso «entregado» y secretos fuera de los logs.
- **Conversación:** las rutas del flujo, incluida la respuesta de fechas tras aclarar a la persona.
- **SQL Server real:** comparar con el ERP A.ESPARZA3 y un proveedor con compras y pagos.
- **WhatsApp real, con la app de dev:** recibir y abrir el PDF de un cliente y de un proveedor.

## Migración

- Reconstruir la imagen con ReportLab.
- Publicar los prompts actualizados en el paquete.
- Probar el envío de documentos con el número real después de desplegar.

## Decisiones libres para quien lo implemente

Estilo visual y anchos de columna, frases del bot que este documento no fija, y la división interna de
los módulos.
