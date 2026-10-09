# Actualizar desde la estructura anterior

## Antes de actualizar

Cierra Uvicorn. Conserva una copia privada fuera del repositorio de tu `.env`, ajustes
de negocio y cualquier reporte HTML que quieras guardar. Esta rama retira de Git los
HTML generados que antes estaban versionados: al actualizar, Git puede quitar esas
copias del directorio de trabajo. No se borra el historial anterior del repositorio.

Revisa `git status` y conserva tus cambios locales antes de hacer pull. No uses
`reset --hard` ni sobrescribas tu configuración con los ejemplos.

## Actualizar la instalación Windows

Desde la raíz del proyecto, estando en la rama `whatsapp-openai`:

```cmd
git pull --ff-only origin whatsapp-openai
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe business_context.py
.\.venv\Scripts\python.exe -m uvicorn whatsapp_agent:create_app --factory --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

Una vez integrada la rama, actualiza desde `main`. Los comandos de Uvicorn y
`python score_riesgo.py` se conservan. La plantilla y las reglas predeterminadas ahora
se distribuyen dentro del paquete; no requieren rutas absolutas de tu PC.

## Configuración y archivos locales

- `.env` sigue en la raíz. La plantilla pública ahora es `config/env.example`.
- `business_context.json` de la raíz sigue funcionando. Los ajustes nuevos pueden ir
  en `config/business_context.json`; este último tiene prioridad si existen ambos.
- El reporte HTML predeterminado se genera en `reportes/score_riesgo.html`.
- Las valoraciones están desactivadas por defecto. Agrega `FEEDBACK_ENABLED=true`
  para habilitarlas. La base local se crea en `data/feedback.sqlite3` al usarla.
- `DBAGG_HOME` permite elegir otro directorio para `.env`, ajustes y datos locales.
  Si ejecutas desde el checkout no necesitas configurarlo.

Si mantuviste Cloudflare abierto, conserva su URL actual. Si lo reiniciaste, actualiza
la URL `/webhook` en Meta. Prueba `/diagnostico`, una consulta conocida y después
`/buena` o `/mala motivo` si activaste las valoraciones.

Esta reorganización conserva las reglas contables existentes; no convierte los
resultados anteriores en respuestas verificadas. La evaluación requiere contrastarlos
con el sistema de referencia, siguiendo [evaluation.md](evaluation.md).
