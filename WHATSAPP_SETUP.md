# Piloto interno de WhatsApp con OpenAI y SQL Server

Este servicio se ejecuta en la PC Windows que ya tiene acceso a SQL Server.
Solo responde a números autorizados. Recibe texto, consulta el catálogo de los
objetos permitidos y ofrece a OpenAI herramientas para inspeccionar columnas,
resolver clientes por nombre, ejecutar SELECT validados y responder con los resultados.
El modelo predeterminado es `gpt-4.1-mini`, configurable
en `OPENAI_MODEL`; confirma su disponibilidad y tarifa en tu cuenta antes de usarlo.
No confundir una suscripción de ChatGPT con acceso y facturación de la API.

## 1. Instalar en la PC

Obtén estos archivos del repositorio en tu PC. Desde la carpeta `dbagg`, con Python
3.12 y Microsoft ODBC Driver 18 instalados:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-whatsapp.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Las dependencias del generador de reportes (numpy/pandas) deben seguir instaladas
en el entorno que preparaste anteriormente. El manifiesto de WhatsApp es adicional.

## 2. Configurar SQL y OpenAI

Conserva tu `.env` existente; agrega los nombres de `.env.whatsapp.example` sin
sobrescribir las credenciales. No compartas el archivo, no lo subas a Git y no
sincronices las credenciales con otras personas mediante OneDrive.

- `OPENAI_API_KEY`: clave de un proyecto de API de OpenAI con facturación activa.
  Configura alertas de presupuesto y revisa el consumo: una alerta no constituye
  necesariamente un límite estricto. https://platform.openai.com/api-keys
- `OPENAI_MODEL`: modelo disponible en esa cuenta, inicialmente `gpt-4.1-mini`.
- `DB_CONNECTION_STRING`: cadena ODBC completa con `Encrypt=yes` y
  `TrustServerCertificate=no`. El servicio no usa el fallback del generador de
  reportes que desactiva la verificación de certificado. Si falla TLS, corrige
  el nombre/cadena de confianza del certificado con el proveedor.
- `SQL_ALLOWED_TABLES`: lista explícita `schema.objeto` separada por comas.
  Prefiere vistas dedicadas que solo expongan columnas aptas para WhatsApp.
  No añadas tablas de contraseñas, tarjetas, datos personales innecesarios o
  información que no deba salir de la organización.
- Usa una cuenta SQL con SELECT únicamente sobre esas vistas. `readonly=True`
  y el validador no sustituyen permisos de base de datos. Solicita a tu administrador
  una cuenta dedicada; no uses una cuenta administradora o con permisos de escritura.

El esquema, la pregunta y los resultados limitados se envían a OpenAI. Las respuestas
se envían a Meta/WhatsApp. Autoriza esos datos para ese uso antes de conectar vistas
reales. No se guardan conversaciones, SQL ni resultados en logs de la aplicación.

## 3. Preparar el número de prueba de Meta

### Excepción temporal para la demo local

Si el proveedor aún no ha corregido su certificado y el responsable acepta el riesgo,
se puede usar `Encrypt=yes;TrustServerCertificate=yes` en `DB_CONNECTION_STRING`
y agregar `DB_ALLOW_UNVERIFIED_TLS=true` al `.env`. Reinicia el servicio. La conexión
sigue cifrada, pero no verifica la identidad del servidor, lo que permite ataques de
suplantación. La consola muestra una advertencia. No desactives `Encrypt`.
Esta excepción es solo para la demo local: cuando el proveedor corrija el certificado,
restaura `TrustServerCertificate=no` y `DB_ALLOW_UNVERIFIED_TLS=false`, y reinicia.
Conserva los permisos SQL de solo lectura y la lista de números/objetos autorizados.

En https://developers.facebook.com/ crea/configura una aplicación compatible con
WhatsApp y sigue su asistente de inicio para obtener el número de prueba:

1. Agrega y verifica los números destinatarios de tu equipo en el panel de pruebas.
2. Copia el token de acceso en `META_ACCESS_TOKEN`. El token temporal sirve para
   el piloto y caduca; para uso permanente configura un token con los permisos
   apropiados y un número de producción siguiendo la documentación de Meta.
3. Copia el identificador del número en `META_PHONE_NUMBER_ID` (no el número visible
   ni el ID de la cuenta empresarial).
4. Copia el secreto de la aplicación en `META_APP_SECRET`. Se usa para verificar
   las firmas de cada evento, no es el token de acceso.
5. Elige un token aleatorio largo para `META_VERIFY_TOKEN`. Se usará también en
   el formulario de verificación del webhook de Meta.
6. Configura `META_GRAPH_VERSION` con una versión Graph vigente admitida por tu
   aplicación (formato `vNN.0`); consulta el panel/documentación de Meta.
7. En `WHATSAPP_ALLOWED_NUMBERS`, escribe los identificadores internacionales
   de los remitentes separados por comas, solo dígitos. Deben coincidir exactamente
   con el campo `from` que Meta entrega, que puede diferir del formato mostrado en
   tu agenda. No abras el acceso con comodines para resolver una discrepancia.

## 4. Arrancar y publicar el webhook HTTPS

```powershell
.\.venv\Scripts\python.exe -m uvicorn whatsapp_agent:create_app --factory --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

`http://127.0.0.1:8000/health` solo comprueba que el servidor local responde; no
certifica acceso a Meta, OpenAI ni SQL Server.

Meta necesita un endpoint HTTPS público. Para el piloto usa un túnel administrado
como Cloudflare Tunnel o ngrok hacia `http://127.0.0.1:8000`, instalado desde su
fuente oficial y con HTTPS válido. No abras el puerto de SQL Server ni un puerto
del router de tu casa para exponer esta aplicación. Mantén la firma HMAC habilitada.

Por ejemplo, si elegiste e instalaste `cloudflared`:

```powershell
cloudflared tunnel --url http://127.0.0.1:8000
```

Los túneles rápidos pueden cambiar de URL al reiniciar y no son para producción.
Con la URL HTTPS asignada, registra en Meta:

- Callback: `https://TU_HOST_DEL_TUNEL/webhook`.
- Verify token: el mismo valor de `META_VERIFY_TOKEN`.
- Suscripción al campo `messages` de WhatsApp y a la cuenta empresarial pertinente.

Conserva abierta la consola del servicio y la del túnel. La PC debe estar encendida
y conectada. No introduzcas claves en comandos públicos, URLs o capturas.

## 5. Validar extremo a extremo

Primero envía `/diagnostico` desde el número autorizado: debe responder
`dbagg: este mensaje llegó al agente local. Diagnóstico del webhook correcto.`
Esta comprobación no usa OpenAI ni consulta SQL. Si llega otra respuesta o no llega
ninguna, revisa las suscripciones, el número de destino y los filtros del webhook.
La consola muestra etapas `dbagg stage=...` sin teléfonos, preguntas, credenciales,
consultas ni resultados. `clarification_no_query` indica que el modelo pidió aclaración
sin ejecutar SQL; `query_ok` indica que SQL se ejecutó, no que la respuesta sea correcta.

Desde un número autorizado y agregado a los destinatarios de prueba de Meta,
envía una pregunta sobre una vista aprobada, por ejemplo:
“¿Cuál es el saldo del cliente CLAVE_REAL según la vista de alertas?”.
Compara la respuesta con una consulta de referencia en SQL Server. Verifica también
que un número no autorizado no reciba respuestas ni provoque llamadas al modelo.
Usa datos de prueba autorizados y una pregunta precisa al principio.

El cálculo de riesgo de `score_riesgo.py` no se ofrece como una herramienta en este
piloto: requiere ejecutarse por separado o exponerse mediante una integración futura.
El agente no debe inventar ese score cuando no esté en las vistas.

## Límites del piloto

### Preguntas naturales y contexto de negocio

El usuario no necesita nombrar tablas ni claves técnicas: puede preguntar “¿Cuánto
debe Esparza?” o “¿Quiénes son los cinco clientes que más deben?”. El agente inspecciona
hasta cinco objetos por llamada, usa descripciones MS_Description si están disponibles,
y puede buscar candidatos antes de consultar el detalle. Los nombres de tablas no
garantizan el significado de un saldo, relación o estado. Si hay ambigüedad real debe
preguntar por el cliente/período o criterio de negocio, sin pedir SQL.

Para mejorar la semántica, copia `business_context.example.json` a `business_context.json`
y agrega definiciones verificadas, columna de identificación/nombre/saldo de cada vista,
relaciones con cardinalidad y reglas de estados/fechas. Este archivo es opcional; nunca
incluyas claves ni filas de clientes. El agente lo carga al consultar. No conserva memoria
entre mensajes: si pregunta por una coincidencia, incluye el nombre completo o clave en
tu respuesta. La exactitud con tu esquema real debe validarse comparando consultas conocidas.

`LOGIN_ACCESS_DATA` y `__EFMigrationsHistory` se excluyen del catálogo aunque se añadan
a SQL_ALLOWED_TABLES. Restringe la lista a objetos de negocio aprobados; los objetos
temporales y de importación no deberían mezclarse con reportes vigentes.

- Una pregunta de hasta 1.000 caracteres; consultas TOP 50, timeout SQL 15 segundos,
  timeout de conexión 10 segundos y resultados acotados. Los valores largos se recortan.
- Hasta nueve llamadas al modelo, cuatro descripciones de catálogo y cuatro SELECT por
  pregunta. Este flujo puede costar más que el piloto anterior de dos llamadas; revisa
  consumo y latencia. Los mensajes no autorizados
  no consultan SQL ni OpenAI.
- Subconjunto conservador de SELECT; rechaza escrituras, múltiples sentencias, destinos
  remotos, funciones no aprobadas, hints y CTE. No soporta todo el lenguaje SQL.
- Máximo una consulta activa y una pregunta por remitente cada 10 segundos. Los mensajes
  durante sobrecarga se descartan. Las respuestas o envíos fallidos no se reintentan.
- Deduplicación en memoria por 24 horas; se pierde al reiniciar. Un único worker obligatorio.
  Para producción hacen falta cola durable, deduplicación persistente, controles de costos
  y observabilidad sin datos sensibles. No ofrece una garantía de entrega exactamente una vez.
- Solo responde a mensajes entrantes de texto. No inicia campañas ni mensajes fuera de la
  conversación; respeta la ventana y políticas vigentes de Meta.

Las pruebas automatizadas simulan OpenAI, Meta y SQL. Una ejecución local de esas pruebas
no demuestra conectividad real ni la exactitud de las respuestas generadas por el modelo.
