# dbagg

Generador de reportes de riesgo de crédito y piloto interno para consultar SQL Server
por WhatsApp con OpenAI.

Estado: piloto en estabilización para producción. El avance por entregas está en
[refactor-roadmap.md](docs/refactor-roadmap.md); los requisitos de despliegue están
en [production.md](docs/production.md).

## Inicio rápido

Requiere Python 3.11 o 3.12 y Microsoft ODBC Driver 18 para conectar con SQL Server.
Desde la raíz del repositorio en Windows:

```cmd
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

En una instalación nueva, copia `config/env.example` a `.env` y completa los valores
localmente. Si ya tienes `.env`, consérvalo. Arranca el servicio:

```cmd
.\.venv\Scripts\python.exe -m uvicorn whatsapp_agent:create_app --factory --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

En otra consola ejecuta `cloudflared tunnel --url http://127.0.0.1:8000` y configura
en Meta la URL del túnel terminada en `/webhook`. La guía de
[WhatsApp](docs/whatsapp-setup.md) explica las credenciales, suscripciones y diagnóstico.

## Organización

| Carpeta | Responsabilidad |
| --- | --- |
| `src/dbagg/api/`, `src/dbagg/integrations/` | Webhook HTTP y cliente de Meta |
| `src/dbagg/agent/`, `src/dbagg/services/` | Modelo, herramientas, memoria y conversación |
| `src/dbagg/database/` | Conexión, catálogo y validación de SQL de lectura |
| `src/dbagg/context/` | Reglas de negocio versionadas y carga de ajustes locales |
| `src/dbagg/reporting/` | Cálculo y plantilla del reporte HTML |
| `src/dbagg/evaluation/`, `evals/` | Valoraciones, revisión humana y casos sintéticos |
| `config/`, `docs/`, `tests/` | Ejemplos, documentación y pruebas unitarias/de integración |

Los archivos Python de la raíz mantienen los comandos existentes. Los reportes nuevos
se generan en `reportes/`. Credenciales, reportes y evaluaciones locales quedan fuera
de Git. Consulta la [arquitectura](docs/architecture.md) y la
[guía de actualización](docs/migration.md) antes de actualizar una instalación existente.

## Mejorar las respuestas del agente

Activa `FEEDBACK_ENABLED=true` en `.env` y reinicia. Después de una respuesta puedes
enviar `/buena` o `/mala motivo`. La valoración guarda localmente la pregunta, respuesta
y contexto de conversación para revisión. No implica que la respuesta sea correcta.

La [guía de evaluación](docs/evaluation.md) incluye una rúbrica, revisión contra el
sistema de referencia y exportación de ejemplos corregidos y anonimizados. Solo se
exportan casos aprobados; no se inicia entrenamiento ni se suben datos automáticamente.
Las reglas de negocio se documentan en [contexto de negocio](docs/business-context.md).

## Desarrollo y validación

```cmd
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m build
.\.venv\Scripts\python.exe scripts\smoke_wheel.py
```

En Linux usa `.venv/bin/python`. CI configura estas verificaciones con Python 3.11 y
3.12 en Linux y Windows. Node.js 24 ejecuta las reglas de presentación del reporte;
el servicio Python no lo requiere. Las pruebas simulan SQL, OpenAI y Meta; la conectividad y exactitud con datos
reales se validan por separado desde la PC autorizada. El piloto conserva memoria en
un solo proceso y requiere `--workers 1`.
