# Preparación para producción

El código se distribuye como paquete Python, con implementación en `src/dbagg/`.
La rama `refactor` es de estabilización: tener una estructura limpia y un wheel válido
no confirma todavía entrega confiable ni exactitud financiera en producción.

## Construir un artefacto verificable

Desde la raíz, con `.venv` activado y Node.js 24 para las pruebas de presentación:

```sh
python -m pip install -r requirements.txt -e '.[dev]'
python -m ruff check src tests scripts whatsapp_agent.py score_riesgo.py business_context.py
python -m ruff format --check src tests scripts whatsapp_agent.py score_riesgo.py business_context.py
python -m unittest discover -s tests -v
python -m build
python scripts/smoke_wheel.py
```

El smoke instala el wheel en un directorio temporal fuera del checkout y comprueba
importación, contexto, plantilla HTML/JS y webhook firmado con envío simulado. No
llama APIs externas ni usa credenciales de producción. Conserva el wheel aprobado y
las versiones de dependencias; prueba cada publicación sobre una instalación limpia.

## Ejecutar desde una instalación del wheel

Instala el wheel en un entorno virtual del equipo que tenga acceso aprobado a SQL
Server. Microsoft ODBC Driver 18 debe estar instalado. Crea un directorio de datos
privado fuera del código y define `DBAGG_HOME` hacia ese directorio; allí viven `.env`,
`config/business_context.json`, `data/` y `reportes/`.

El entrypoint del paquete instalado es:

```sh
python -m uvicorn dbagg.api.app:create_app --factory --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

Los comandos `dbagg-report`, `dbagg-context` y `dbagg-eval` también se instalan con el
wheel. `whatsapp_agent.py` es el adaptador del checkout; no es necesario distribuirlo
junto al paquete instalado. `/health` comprueba el proceso, no servicios externos.

## Requisitos antes de habilitar uso habitual

- Contrastar saldos/pagos con consultas de referencia aprobadas. Confirmar el mapeo
  pendiente de créditos y las reglas del reporte antes de usar su score en decisiones.
- Completar evidencia de respuestas y entrega persistente de mensajes (H03/H04/H06/H11).
  Hoy el proceso todavía puede descartar trabajo simultáneo y perder envíos fallidos.
- Unificar TLS del reporte/agente y validar permisos SQL de lectura. La excepción de
  certificado de la demo no es la configuración predeterminada de producción.
- Configurar arranque supervisado, reinicio y túnel HTTPS con dirección estable. La
  PC actual puede seguir siendo el host si es la que tiene acceso a SQL Server.
- Usar credenciales de Meta adecuadas para operación continua, verificar suscripciones
  y probar `/diagnostico` y consultas de referencia de extremo a extremo.
- Acordar zona horaria, ubicación privada, retención y respaldo; ensayar recuperación
  y reversión al artefacto anterior sin perder datos locales.

La matriz CI agrega Windows/Linux; el resultado de cada ejecución debe revisarse antes
de publicar. Una ejecución local Linux no valida Windows ni el servidor SQL remoto.
