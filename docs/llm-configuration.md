# Modelos y prompts configurables

La versión 0.3 separa la interpretación del modelo (`src/dbagg/llm/`) de las operaciones
verificadas y validación SQL. Se puede cambiar proveedor, modelo y prompts sin editar
la lógica de negocio. No se amplían permisos SQL ni se ejecuta texto libre como SQL.

## Preparar los archivos locales

Después de instalar las dependencias, desde la raíz del proyecto:

```cmd
.\.venv\Scripts\python.exe -m dbagg.llm init
```

Crea los siguientes archivos, sin sobrescribir archivos existentes:

```text
config/
  llm.toml              Perfiles, modelo activo, endpoint y parámetros
  prompts/
    agent.md            Instrucciones de interpretación del agente
    tools.json          Descripciones de las cuatro herramientas autorizadas
```

Los archivos generados están ignorados por Git para conservar experimentos y ajustes
locales al actualizar. Las versiones base públicas se distribuyen dentro del paquete:
`src/dbagg/llm/default.toml` y `src/dbagg/agent/templates/`. No se copian API keys.
`init` conserva el modelo indicado previamente en `OPENAI_MODEL` para el perfil OpenAI.
Si ya existen los archivos, edítalos directamente; repetir `init` no los reinicia.

## Cambiar proveedor o modelo

En `config/llm.toml`, cambia la selección:

```toml
version = 1
active_profile = "glm"
```

Edita el `model` del perfil elegido con el **identificador que admite tu cuenta**.
Los identificadores incluidos son ejemplos, no una comprobación de disponibilidad,
precio o compatibilidad de cualquier modelo. El modelo elegido debe soportar llamadas
a funciones/herramientas. Puedes duplicar un perfil con otro nombre para comparar
modelos del mismo proveedor.

| Perfil incluido | Adaptador | Variable de API key | Endpoint predeterminado |
| --- | --- | --- | --- |
| `openai` | OpenAI Chat Completions | `OPENAI_API_KEY` | `https://api.openai.com/v1` |
| `glm` | API compatible con OpenAI de Z.ai | `ZAI_API_KEY` | `https://api.z.ai/api/paas/v4` |
| `gemini` | Compatibilidad OpenAI de Gemini | `GEMINI_API_KEY` | `https://generativelanguage.googleapis.com/v1beta/openai` |
| `claude` | Anthropic Messages nativo | `ANTHROPIC_API_KEY` | `https://api.anthropic.com/v1` |

Guarda el valor de la clave correspondiente en tu `.env`, no en TOML ni en prompts.
Solo se exige la clave del perfil activo: Claude, Gemini o GLM no requieren una clave
OpenAI adicional. `api_key_env` permite cambiar el nombre de la variable.
El endpoint GLM incluido es el de la API general; otros productos o regiones pueden
requerir otro endpoint y credenciales según su proveedor.

Para inspeccionar la configuración sin consultar SQL ni consumir llamadas LLM:

```cmd
.\.venv\Scripts\python.exe -m dbagg.llm check
.\.venv\Scripts\python.exe -m dbagg.llm check --profile claude
```

Muestra perfil, proveedor, modelo, nombre/presencia de la variable de clave y huella del
prompt. Nunca imprime la clave. `--profile` solo inspecciona ese perfil; no cambia el
perfil activo. Un resultado `api_key_present=false` significa que falta configurarla;
`check` no demuestra acceso al proveedor ni disponibilidad del modelo.

**Reinicia Uvicorn al cambiar el perfil, modelo, claves, parámetros o rutas de prompts.**
Puedes conservar Cloudflare abierto. Usa `/reiniciar` al comenzar una comparación de
modelos para que ambos partan del mismo historial.

## Precedencia y parámetros

1. `LLM_CONFIG`, si existe, selecciona otro TOML. Rutas relativas parten de `DBAGG_HOME`
   o de la raíz del proyecto. Una ruta explícita inexistente es un error.
2. Sin esa variable se usa `config/llm.toml`, si existe.
3. Sin archivo local se usan perfiles distribuidos; OpenAI sigue siendo predeterminado
   y `OPENAI_MODEL` mantiene compatibilidad con la instalación anterior.
4. `LLM_PROFILE` tiene prioridad sobre `active_profile`. Cuando existe TOML local,
   `OPENAI_MODEL` deja de seleccionar el modelo: manda `model` del perfil.

Cada perfil requiere `provider`, `model`, `api_key_env` y `base_url`. El proveedor
`openai_compatible` permite otro servicio que implemente Chat Completions y tool calling.
El endpoint exige HTTPS y no admite credenciales incrustadas ni parámetros de URL.

Parámetros opcionales:

- `max_output_tokens`: de 128 a 16384, predeterminado 1200. Incluye el presupuesto que
  el proveedor pueda dedicar al razonamiento; una respuesta cortada no ejecuta herramientas.
- `temperature`: opcional. Omitir si el modelo no admite este parámetro. El perfil
  OpenAI incluido conserva 0.0; los demás no lo envían por defecto.
- `token_parameter`: `max_tokens` o `max_completion_tokens`, según el contrato del
  modelo compatible. Anthropic usa siempre `max_tokens`.
- `timeout_seconds`: de 1 a 120 por solicitud; `max_retries`: de 0 a 2. No constituyen
  un plazo global para toda la conversación. El agente conserva sus límites de herramientas.
- `[profiles.NOMBRE.extra_body]`: opciones `thinking`, `reasoning_effort`, `google`,
  `output_config` o `verbosity`, solo cuando el endpoint/modelo las admita. No permite
  reemplazar herramientas, mensajes, identidad del modelo ni opciones de transporte.

El perfil GLM incluido desactiva thinking para una primera prueba acotada. Para habilitarlo,
revisa la documentación del modelo y ajusta presupuesto y parámetros. La capa compatible
conserva `reasoning_content` y firmas de Gemini; el adaptador Claude devuelve sus bloques
de razonamiento firmados sin modificarlos entre llamadas a herramientas.
No se intenta otro proveedor ni se elimina un parámetro silenciosamente si la API falla.
Una respuesta inválida o cortada, o un error de autenticación, se reporta como fallo.

## Editar prompts

`agent.md` es texto UTF-8, no una plantilla ejecutable: no hay Python ni sustitución
arbitraria de variables. El programa añade objetos autorizados, contexto de negocio,
calendario e historial por separado. Puedes ajustar redacción, prioridades y ejemplos
sintéticos. No copies datos privados de clientes ni claves.

`tools.json` contiene exactamente cuatro nombres y sus descripciones. Los nombres,
esquemas de argumentos, lista de tablas, SQL permitido, límites y reglas de saldos/pagos
siguen definidos en código. Editar una descripción no crea nuevas herramientas.
Las reglas de contexto de negocio mantienen su archivo independiente.

El contenido de los prompts se vuelve a leer **en cada pregunta**, sin reiniciar.
Cambiar sus rutas en TOML sí requiere reiniciar. Un archivo inválido, vacío, faltante o
excesivo produce un error; no se sustituye silenciosamente por instrucciones antiguas.
Puedes usar otras rutas mediante `[prompts] system_file` y `tools_file`, relativas al
TOML o absolutas. Sin esas opciones se usan los recursos distribuidos del paquete.

Cada respuesta conserva `catalog-domains-v6:<huella>` calculada con el texto y las
descripciones realmente cargadas. Las valoraciones `/buena` y `/mala` guardan también
el modelo, proveedor y perfil seleccionados. La base SQLite existente recibe las dos
columnas nuevas sin borrar registros, revisiones ni ejemplos aprobados; las entradas
anteriores mantienen esos campos vacíos cuando se desconoce el proveedor.

## Transcripción de notas de voz

La sección opcional `[transcription]` configura las notas de voz de WhatsApp. Es
independiente del perfil activo: siempre usa una API `/audio/transcriptions` compatible
con OpenAI. Si falta la sección se aplican estos valores:

```toml
[transcription]
enabled = true
model = "gpt-4o-mini-transcribe"   # también whisper-1 o gpt-4o-transcribe
api_key_env = "OPENAI_API_KEY"
base_url = "https://api.openai.com/v1"
language = "es"
max_seconds = 60                   # 5 a 300
timeout_seconds = 30
hint = ""                          # hasta 500 caracteres; solo en el llm.toml local
```

`hint` orienta la ortografía de nombres frecuentes (apellidos, apartados de gasto,
productos) que el modelo de voz confunde. Escríbelo solo en `config/llm.toml`, que no se
versiona, para no publicar nombres reales.

Si la variable de `api_key_env` no existe, el servicio arranca con una advertencia y
responde a las notas de voz que escriban su pregunta; los mensajes de texto no cambian.
`enabled = false` las desactiva explícitamente. Confirma que el modelo esté disponible
en tu cuenta antes de cambiarlo.

## Prueba de un perfil

1. Ejecuta `check`; confirma perfil/modelo, `api_key_present=true` y huella esperada.
2. Reinicia Uvicorn y prueba `/diagnostico` (solo verifica la conexión de WhatsApp).
3. Envía `/reiniciar`, una consulta de saldo, una de último pago y un ranking.
4. Compara con tu sistema y registra `/buena` o `/mala motivo` si habilitaste valoraciones.
5. Cambia de perfil y repite las mismas preguntas desde una conversación reiniciada.

Las pruebas automatizadas usan HTTP y datos sintéticos: comprueban formatos,
continuación de herramientas, rechazo de respuestas truncadas, aislamiento de credenciales,
reglas SQL y migración de valoraciones. No prueban las cuentas ni los modelos remotos.
El benchmark automático, costos/tokens y la cola persistente siguen siendo entregas pendientes.

Referencias de los protocolos: [OpenAI](https://platform.openai.com/docs/api-reference/chat),
[Gemini](https://ai.google.dev/gemini-api/docs/openai),
[Z.ai](https://docs.z.ai/guides/overview/quick-start) y
[Anthropic](https://platform.claude.com/docs/en/api/messages).

Para probar el prompt v5, conservar tus ajustes y comparar respuestas, consulta
[diseño y evaluación del prompt](prompt-design.md).
