# Actualizar desde la estructura anterior

## Antes de actualizar

Cierra Uvicorn. Conserva una copia privada fuera del repositorio de tu `.env`, ajustes
de negocio y cualquier reporte HTML que quieras guardar. Esta rama retira de Git los
HTML generados que antes estaban versionados: al actualizar, Git puede quitar esas
copias del directorio de trabajo. No se borra el historial anterior del repositorio.

Revisa `git status` y conserva tus cambios locales antes de hacer pull. No uses
`reset --hard` ni sobrescribas tu configuración con los ejemplos.

## Actualizar la instalación Windows

Desde la raíz del proyecto, estando en la rama `refactor` (todavía sin integrar a `main`):

```cmd
git pull --ff-only origin refactor
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe business_context.py
.\.venv\Scripts\python.exe -m uvicorn whatsapp_agent:create_app --factory --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

Una vez integrada la rama, actualiza desde `main`. Los comandos de Uvicorn y
`python score_riesgo.py` se conservan. La plantilla y las reglas predeterminadas ahora
se distribuyen dentro del paquete; no requieren rutas absolutas de tu PC.

## Configuración y archivos locales

- `.env` sigue en la raíz. Los nombres de las variables están en [whatsapp-setup.md](whatsapp-setup.md).
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

La reorganización de archivos no convierte los resultados anteriores en respuestas
verificadas. Las nuevas operaciones se describen abajo. La evaluación requiere contrastarlos
con el sistema de referencia, siguiendo [evaluation.md](evaluation.md).

## Rama refactor: código dentro de src/

La primera entrega mueve el paquete a `src/dbagg/`. El directorio exterior es el
repositorio; el paquete Python vive solo dentro de `src/`. Reinstala después del pull:

```cmd
git switch refactor
git pull --ff-only origin refactor
.\.venv\Scripts\python.exe -c "import shutil; shutil.rmtree('dbagg.egg-info', ignore_errors=True)"
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Si la rama aún no existe localmente, usa `git fetch origin` y
`git switch --track origin/refactor`. `.env` continúa en la raíz y los comandos de
Uvicorn y del reporte se conservan. Node.js 24 es necesario para ejecutar la prueba
de presentación JavaScript; no se necesita para ejecutar el servicio Python.

El comando de limpieza retira solo los metadatos generados por la instalación antigua
en la raíz (`dbagg.egg-info`); no toca `.env` ni datos. Dejarlos después de mover a
`src/` puede hacer que herramientas de Python informen la versión anterior. La nueva
instalación genera sus metadatos dentro de `src/` y el entorno virtual.

`core` no forma parte del repositorio. Si quedó vacía en tu PC, `rmdir core` la retira;
si contiene archivos, ese comando no los elimina. Los directorios generados o archivos
ignorados de instalaciones anteriores pueden permanecer localmente tras un pull.
Un despliegue desde el wheel evita depender de esos residuos del checkout.

Esta entrega también corrige saldos desconocidos, fechas de cobranza, límites TOP y
valoración tras errores. Lee [refactor-roadmap.md](refactor-roadmap.md) para sus límites
y validaciones pendientes contra la base real.

## Segunda entrega: consultas verificadas

La versión 0.2.2 mantiene la misma rama y comandos. Reinicia Uvicorn después de
actualizar y reinstalar. No reemplaces `.env` ni el contexto local con ejemplos.
`BUSINESS_TIMEZONE` es opcional y usa `America/Mexico_City` de forma predeterminada.

El texto libre del modelo ya no se envía como respuesta financiera: saldos y pagos
usan cinco [operaciones verificadas](business-queries.md), y otros objetos conservan
una salida exploratoria. Cambios locales en fuentes, columnas o filtros de esas
operaciones deben coincidir con el contrato del código; se informa cualquier diferencia.
Prueba homónimos, selección de cliente, seguimiento y pagos de un período, comparando
con el sistema de referencia. La cola persistente sigue pendiente.
