# Probar Jev con WhatsApp en local

El piloto usa la misma cascada del benchmark: Jev selecciona rutas de clientes/pagos
y gastos; `gpt-4.1-mini` interpreta parámetros, explora SQL o sirve de respaldo.
Bancos, inventario y demás casos siguen el flujo completo existente. No se activa nano.
El umbral 0.8 es el usado en la prueba local, no una calibración de producción.

## 1. Actualizar y comprobar

Desde la raíz del checkout en PowerShell:

```powershell
git pull --ff-only origin feat/modularization
.\.venv\Scripts\python.exe scripts/run_local_jev.py --check
```

Conserva tu `.env` con `OPENAI_API_KEY`, `TYPESAFE_API_KEY`, las variables `META_*`,
`WHATSAPP_ALLOWED_NUMBERS`, `SQL_ALLOWED_TABLES` y `DB_CONNECTION_STRING` de tu
instalación actual. Las claves permanecen locales. El número que usará WhatsApp
debe estar en la lista autorizada. Se mantienen las validaciones de SQL de solo
lectura, catálogo, columnas y TLS existentes.

`--check` valida configuración, presencia de las claves y rutas habilitadas; no
inicia el servidor ni realiza llamadas a Jev, OpenAI, Meta o SQL Server. Debe mostrar:

- `router: "jev"`, `shadow: false`, `jev_api_key_present: true`.
- `interpretation_and_fallback_model: "gpt-4.1-mini"`.
- `custom_prompts_loaded: false` y una lista no vacía de `available_routes`.
- La ruta de `usage_path` y `external_services_checked: false`.

El arranque selecciona `config/llm.jev-local.toml` **solo para ese proceso**. No
sobrescribe `config/llm.toml`, `.env` ni los prompts locales. En este piloto usa los
módulos empaquetados de la rama: un `[prompts]` personalizado en el archivo habitual
haría que el agente volviera al flujo anterior. La configuración de voz del piloto
usa `gpt-4o-mini-transcribe`, español, máximo 60 segundos; las reglas locales de
negocio, permisos, números y conexión SQL siguen cargándose normalmente.

Si faltan dependencias después de una actualización, ejecuta:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 2. Arrancar el bot

Cierra el Uvicorn anterior que use el puerto 8000 y ejecuta:

```powershell
.\.venv\Scripts\python.exe scripts/run_local_jev.py
```

Este comando arranca el webhook existente en `127.0.0.1:8000`, con un solo proceso,
la verificación de firma, la lista autorizada y el registro de consumo activos.
La base y las respuestas de WhatsApp ahora son las de tu entorno real, a diferencia
del benchmark sintético. Los mensajes de prueba consumen API.

Para usar otro puerto: `--port 8100`; el túnel debe apuntar al mismo puerto.
Puedes comprobar que el servicio está escuchando desde otra consola:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

`/health` comprueba solo el webhook, no la conexión a servicios externos.

## 3. Conectar WhatsApp

Si tu túnel existente sigue apuntando a `http://127.0.0.1:8000`, consérvalo.
Si necesitas un túnel temporal nuevo, en otra terminal:

```powershell
cloudflared tunnel --url http://127.0.0.1:8000
```

Si cambió la URL, actualiza el callback de Meta a la nueva URL terminada en `/webhook`.
El script existente actualiza la suscripción de la app y la configuración del número;
se ejecuta con el bot y el túnel en marcha:

```powershell
.\.venv\Scripts\python.exe scripts/update_webhook_url.py https://TU_HOST.trycloudflare.com
```

Si usas tu túnel/dominio fijo habitual, mantiene esa URL y su ruta hacia el bot local;
no es necesario crear un túnel temporal ni cambiar el callback si ya corresponde.
No se inicia ni modifica ningún túnel automáticamente desde el nuevo lanzador.

## 4. Probar desde el número autorizado

Envía primero `/diagnostico` para confirmar que WhatsApp llega a este proceso, y luego
`/reiniciar` para empezar sin referencias anteriores. Prueba consultas reales del ERP:

1. «¿Cuál es el saldo de [cliente real]?»
2. «¿Y cuánto ha pagado en total?»
3. «Ahora quiero el total de gastos registrados».
4. «¿Cuánto gastamos este mes?»

Comprueba los resultados contra el ERP. El cambio a gastos no debe conservar el
cliente anterior; los resultados de gastos siguen etiquetados como exploratorios.
Un saludo solo se resuelve localmente; «Hola, ¿cuánto debe [cliente]?» sigue al enrutador.
Espera la respuesta anterior y deja al menos 10 segundos entre mensajes: se conserva
el límite de admisión del piloto. Las notas de voz usan la misma ruta tras transcribir.

La consola muestra `stage=provider_usage provider=jev task=routing` y las llamadas
OpenAI con tokens informados. `stage=routing_selected` muestra la ruta, confianza y
si es observación. Si aparecen llamadas adicionales de clasificación/interpretación,
se cuentan también: Jev puede usar el respaldo.

## 5. Revisar el consumo y detener

Por defecto el registro se añade a `work/whatsapp-jev/usage.jsonl`, ignorado por Git.
Si definiste `DBAGG_USAGE_PATH`, se respeta esa ruta; el arranque imprime cuál usa.
Los registros contienen tokens, tareas, duraciones e identificadores aleatorios de
consulta; no preguntas, respuestas, SQL, números de teléfono ni claves. Un valor
`null` significa consumo desconocido. La caché OpenAI forma parte de la entrada.

Para seguir el archivo por defecto después del primer mensaje:

```powershell
Get-Content .\work\whatsapp-jev\usage.jsonl -Tail 20 -Wait
```

La misma consulta comparte `request_id` entre Jev, mini, etapas y transcripción.
El archivo acumula sesiones; no mezcles sus totales con una ejecución aislada del
benchmark. Las etapas tienen tiempos inclusivos: no sumes una conversación con las
llamadas que contiene. Los logs no sustituyen una validación de exactitud financiera.

`Ctrl+C` detiene este proceso. El comando habitual de Uvicorn vuelve a usar tu
configuración habitual, porque el lanzador no modificó los archivos ni las variables
de la terminal de PowerShell. No se realizó despliegue en servidor.
