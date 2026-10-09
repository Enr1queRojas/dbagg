# Arquitectura

```text
src/dbagg/
  api/app.py                Webhook HTTP, firma y números autorizados
  integrations/meta.py       Envío a Meta y códigos de error seguros
  services/conversation.py   Conversación, comandos y registro opcional de valoraciones
  services/memory.py         Memoria por remitente, deduplicación y límite de mensajes
  agent/service.py           Ciclo acotado de herramientas de OpenAI
  agent/prompts.py           Instrucciones, contratos y versión del prompt
  context/                  Carga/mezcla y definiciones de negocio distribuidas
  database/client.py        Acceso al catálogo y consultas SQL acotadas
  database/validation.py    Validación independiente del SQL generado
  database/connection.py    Utilidades de cadenas ODBC
  reporting/               Cálculo, HTML, plantilla y CLI del reporte
  evaluation/              Valoraciones locales, revisión humana y exportación
config/                    Ejemplos de configuración sin secretos
docs/                      Instalación, negocio, evaluación y migración
evals/                     Rúbrica y casos sintéticos de referencia
tests/unit/                Configuración, SQL, memoria, agente, reportes y evaluación
tests/integration/         Webhook con servicios simulados y entradas compatibles
```

`whatsapp_agent.py`, `score_riesgo.py` y `business_context.py` son adaptadores de los
comandos anteriores. La implementación vive en el paquete `dbagg`; no hay una segunda
copia del agente. La entrada recomendada es `dbagg.api.app:create_app --factory`.

El webhook valida la firma y el remitente antes de delegar al servicio de conversación.
El agente inspecciona el catálogo y propone SQL; el validador lo restringe antes del
acceso a datos. El transporte Meta no conoce las reglas del negocio ni las credenciales
SQL. Los prompts y el contexto llevan versiones registradas con las valoraciones.

## Configuración y archivos

La raíz del checkout contiene `.env`. `DBAGG_HOME` permite seleccionar otra raíz para
una instalación empaquetada; sin ese valor, una instalación fuera del checkout usa
el directorio de trabajo. No se cambia el directorio de trabajo del proceso.

`src/dbagg/context/default.json` y la plantilla HTML se distribuyen dentro del paquete.
El contexto base se combina primero con el archivo local antiguo `business_context.json`
y después con `config/business_context.json`. Los ajustes locales no se modifican ni
se versionan. Las listas reemplazan listas; los diccionarios se combinan recursivamente.

`reportes/` contiene salidas locales. `data/feedback.sqlite3` contiene valoraciones
optativas. Ninguno se publica en Git. El contexto y los ejemplos distribuidos no deben
contener filas de clientes, contraseñas o respuestas reales copiadas.

## Límites que siguen vigentes

Un solo worker. La memoria y la deduplicación siguen siendo volátiles; el trabajo
concurrente se descarta cuando el servicio está ocupado. No se promete entrega exactamente
una vez ni funcionamiento permanente de un túnel rápido. El nuevo registro SQLite conserva
valoraciones explícitas, no una cola de mensajes ni todos los chats.

El contexto se recarga por pregunta. Su huella identifica la configuración usada, pero
no se conserva una copia del prompt completo ni de los resultados SQL en la evaluación.
Las etiquetas humanas no se aplican automáticamente como instrucciones al agente.

El reporte usa `FECHA` en cobranza, con conversión SQL estilo 103 y corte al día
siguiente exclusivo. Un saldo desconocido o historial de fechas inválidas deja el
caso sin clasificar y muestra el motivo. El cálculo puro está separado de la lectura SQL.
La fecha de créditos conserva `NOTE_DATE` hasta confirmar su mapeo. El reporte mantiene
el filtro histórico de importes positivos; esa regla y sus umbrales necesitan validación
contable. Consulta [refactor-roadmap.md](refactor-roadmap.md) y [production.md](production.md).
