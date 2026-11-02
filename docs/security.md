# Seguridad

Defensas del piloto, de afuera hacia adentro, y lo que queda a cargo de quien lo opera.

## Webhook de WhatsApp

- Firma `X-Hub-Signature-256` (HMAC con `META_APP_SECRET`) en cada POST; sin firma válida,
  403 y el cuerpo no se interpreta. Cuerpo limitado a 256 KB mientras se lee.
- Solo se atienden `META_PHONE_NUMBER_ID` y los números de `WHATSAPP_ALLOWED_NUMBERS`;
  el resto se ignora sin respuesta.
- Un mensaje se procesa una vez (deduplicación por ID durante 24 h) y cada número puede
  preguntar cada 10 s.
- Reenvío: Meta firma el cuerpo pero no la hora de entrega, y la deduplicación vive en
  memoria. Un mensaje con `timestamp` de más de 15 minutos, o inválido, no se responde
  (`stage=ignored_stale`). Si el túnel estuvo caído más tiempo, esos mensajes se pierden
  y hay que volver a preguntar.
- El log de acceso de uvicorn imprime la URL completa; `hub.verify_token` se reemplaza por
  `<redactado>` antes de escribirse.
- La descarga de notas de voz lleva el token de Meta, así que solo se hace a hosts HTTPS
  de Meta (`fbsbx.com`, `facebook.com`, `fbcdn.net`, `whatsapp.net`).

## Base de datos

- El login SQL debe ser de solo lectura: en la primera conexión el servicio rechaza
  funcionar si es `sysadmin`, `db_owner`, `db_datawriter` o `db_ddladmin`.
- Mínimo privilegio: [`scripts/sql/dbagg_readonly.sql`](../scripts/sql/dbagg_readonly.sql)
  crea `dbagg_lectura` con `SELECT` solo sobre las fuentes del catálogo, sin
  `db_datareader` y con `EXECUTE` denegado. Lo ejecuta un administrador; después se
  cambian `DB_USER`/`DB_PASSWORD` en `.env`. Un test falla si el catálogo cambia y el
  script no.
- `consultar_sql` (exploratorio) pasa por `validate_sql`: una sola sentencia `SELECT`,
  solo objetos y columnas del catálogo, funciones en lista blanca, sin variables
  (`@x`, `@@VERSION`), sin CTE, hints, `OPENROWSET`/`OPENQUERY` ni otras bases, `JOIN`
  solo declarados y con `ON a.col = b.col` (sin productos cruzados), máximo tres
  referencias a objetos y `TOP 50`. Un alias no habilita una columna fuera del catálogo.
  El tiempo de consulta está limitado a 15 s.

## Datos guardados

- No se guardan números de teléfono, audio ni resultados SQL. La conversación vive en
  memoria 30 minutos.
- `data/gaps.sqlite3` (`GAP_LOG_ENABLED`) y `data/feedback.sqlite3` (`FEEDBACK_ENABLED`)
  guardan el texto de preguntas y, en valoraciones, la respuesta: pueden contener nombres
  y saldos. Están fuera de Git.
- Retención: al arrancar se aplica `DATA_RETENTION_DAYS` (90 por defecto, 1–3650). Lo más
  antiguo se borra; un grupo descartado conserva solo su texto normalizado para que no
  vuelva a revisión, y una valoración aprobada conserva solo su caso anonimizado. A mano:
  `python -m dbagg.evaluation purge --days 30`.

## Secretos y dependencias

- `.env` y `config/llm.toml` están en `.gitignore`; `Settings` no imprime secretos en
  `repr` y los logs registran solo etapas, tipos de error y códigos numéricos.
- Auditoría de dependencias: `pip install -e .[dev]` y `python -m pip_audit`. `starlette`
  se fija explícitamente porque FastAPI acepta versiones vulnerables.

## Pendiente fuera del código

- TLS con verificación de identidad del servidor SQL (`DB_ALLOW_UNVERIFIED_TLS`).
- Crear `dbagg_lectura` con el script y retirar el login actual, que tiene
  `db_datareader` sobre las 161 tablas y vistas, y permiso `EXECUTE`.
