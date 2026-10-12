# Actualizar una instalación existente

## Código y dependencias

Detén Uvicorn. Revisa `git status` y conserva cambios locales, `.env`, contexto de negocio,
prompts, valoraciones y reportes. No uses `reset --hard` ni sustituyas tus ajustes por ejemplos.

Para una versión ya integrada, actualiza desde `main`:

```cmd
git switch main
git pull --ff-only origin main
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe business_context.py
.\.venv\Scripts\python.exe -m dbagg.llm check
```

Si estás probando un cambio pendiente, usa la rama indicada en ese PR. Las ramas históricas
`refactor` y `feature/llm-config-providers` ya se integraron; no es necesario recrearlas.
Los comandos `whatsapp_agent:create_app` y `python score_riesgo.py` siguen siendo compatibles.
Node.js 24 se usa en las pruebas de presentación; no es necesario para ejecutar el backend.

## Revisar residuos locales después de un merge

Un merge retira archivos versionados, pero puede dejar carpetas que contienen cachés ignoradas.
La implementación vigente vive en `src/dbagg/`; un `dbagg/` antiguo en la raíz no significa que
existan dos versiones activas. Comprueba la importación:

```cmd
.\.venv\Scripts\python.exe -c "import dbagg; print(dbagg.__file__)"
.\.venv\Scripts\python.exe scripts\clean_generated.py
```

La primera ruta debe apuntar al paquete instalado; en el checkout editable será `src/dbagg/`.
La segunda orden solo muestra candidatos. Para retirar los residuos comprobados:

```cmd
.\.venv\Scripts\python.exe scripts\clean_generated.py --apply
```

El script revisa cachés en la raíz, `src/`, `tests/` y `scripts/`. Retira el `dbagg/` antiguo
solo si contiene exclusivamente bytecode dentro de `__pycache__` o carpetas vacías, y `core/`
solo si está vacío. Conserva candidatos con archivos versionados, enlaces/junctions o contenido
no reconocido. Si Git no está disponible, no elimina nada. No examina `.venv`, `config`, `data`
ni reportes. Los cachés se regeneran al ejecutar Python; su reaparición es normal.

`build/`, `dist/`, los metadatos `*.egg-info` y reportes anteriores no se borran automáticamente:
pueden contener artefactos de una publicación o estar en uso por una instalación editable.
Un archivo ignorado no es necesariamente basura. Nunca uses `git clean -fdx` para este trabajo:
también puede borrar claves, datos, entorno virtual y configuración local.

Para auditar futuros archivos: comprueba si Git los versiona, busca referencias con `rg`, revisa
entrypoints en `pyproject.toml`, recursos del paquete y pruebas antes de retirarlos. No basta
que un archivo no tenga imports: plantillas, CLI y fixtures se cargan por otros mecanismos.

## Configuración que se conserva

- `.env` sigue en la raíz. Variables en [whatsapp-setup.md](whatsapp-setup.md).
- `config/business_context.json` tiene prioridad sobre el archivo antiguo de la raíz.
- `config/llm.toml` y `config/prompts/` son locales; un pull no actualiza su contenido.
  Para cambiar a v5 conservando una reversión, sigue [prompt-design.md](prompt-design.md).
- `data/feedback.sqlite3` contiene las valoraciones privadas; `reportes/` contiene HTML generado.
- `DBAGG_HOME` selecciona otra raíz de datos/configuración para instalaciones fuera del checkout.

Los scripts de la raíz son adaptadores pequeños, probados y todavía utilizados por los comandos
anteriores. `src/dbagg/agent/templates/` contiene las plantillas distribuidas; `config/prompts/`
contiene ajustes locales: tienen propósitos distintos. Los casos de `evals/` también se conservan.

## Cambios de versiones anteriores

Desde 0.2, el paquete vive en `src/dbagg/` y los reportes nuevos se generan en `reportes/`.
Conserva una copia privada de los HTML antiguos que necesites: actualizar desde una versión
que los tenía en Git puede retirarlos del directorio de trabajo. Las cinco operaciones de
[negocio](business-queries.md) usan contratos verificados; no cambian por editar un prompt.

Desde 0.3, `python -m dbagg.llm init` crea perfiles/prompts solo si no existen. Sin TOML local
se mantiene OpenAI con `OPENAI_API_KEY` y `OPENAI_MODEL`. Reinicia para cambiar modelo, claves
o rutas; las ediciones de texto del prompt se recargan por pregunta. Las valoraciones reciben
columnas adicionales sin borrar registros anteriores. Consulta [llm-configuration.md](llm-configuration.md).

## Volver a arrancar

```cmd
.\.venv\Scripts\python.exe -m uvicorn whatsapp_agent:create_app --factory --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

Si Cloudflare siguió abierto, conserva la URL; si lo reiniciaste y cambió, actualiza `/webhook`
en Meta. Prueba `/diagnostico`, una consulta conocida y su seguimiento. Contrasta los importes
con el sistema de referencia; las pruebas locales no validan servicios externos.
