# Contexto modular y modelos por tarea

Implementación experimental en `feat/modularization`, 10 de noviembre de 2026.
La configuración activa y el despliegue no se modifican. El plan completo **no cumple
su definición de terminado**: faltan acceso real a Jev, disponibilidad de nano,
calibración y evaluación final de respuestas/costo. Este cambio permite continuar
con esas entregas sin sustituir las reglas actuales de negocio.

## Estado de las entregas

| Entrega del plan | Resultado |
| --- | --- |
| Inventario y contratos | Registro neutral, módulos versionados e inventario reproducible por sección/ruta. |
| Contexto modular | Núcleo, dominio de personas, operación individual y gastos exploratorios, con mini de referencia. |
| Jev | Adaptador HTTP oficial, validación de probabilidades, timeout, observación y un respaldo. Pruebas HTTP simuladas; falta credencial real. |
| Modelos por tarea | Perfil de extracción separado y escalada acotada; nano devolvió HTTP 404 y no se recomienda activarlo. |
| Estado y SQL | Estado explícito por conversación, identidad de resultados verificados, invalidación por dominio, reinicio y aislamiento concurrente. Gastos continúa exploratorio. |
| Evaluación | Comparación real de primera herramienta/parámetros en 16 casos sintéticos de desarrollo, consumo y estimación de costo. Falta evaluación final y de respuestas completas. |
| Activación gradual | Banderas reversibles y modo de observación; desactivado por defecto. |

## Inventario y dependencias

`python scripts/context_inventory.py` reproduce `modular-context-inventory.json`.
Mide caracteres del contexto empaquetado con **todo el catálogo**, no tokens ni costo.
Los permisos y los archivos locales pueden cambiar estos tamaños.

| Bloque del flujo anterior | Caracteres |
| --- | ---: |
| Sistema | 18,624 |
| Negocio | 16,635 |
| Catálogo completo | 10,663 |
| Herramientas, bancos incluidos | 5,159 |
| Total | 51,081 |

La referencia del adjunto usaba otro catálogo/configuración y no es idéntica a esta
medición. Esta rama parte del PR #19; no se importó ni fusionó el PR #20.

- `templates/core.md`: función, seguridad, decisión por turno, comprobaciones,
  identidad, calendario y aclaraciones. Se conserva completo en todas las rutas.
- `templates/domains/customers.md`: contratos y dependencia de identidad compartida
  entre clientes y proveedores; no se elimina la búsqueda de homónimos entre catálogos.
- `templates/operations/*.md`: definición completa de la operación seleccionada.
- `templates/domains/expenses.md`: exploración y reglas de gastos, sin atribuir
  semántica verificada a las fuentes candidatas.
- Bancos, inventarios, proveedores explícitos y decisiones dudosas regresan al
  intérprete completo. No se activan módulos incompletos de esos dominios.
- `topics` conocidos se seleccionan por operación. Las reglas comunes, relaciones,
  claves locales desconocidas y temas locales nuevos se conservan. Los prompts
  personalizados completos fuerzan el flujo anterior para respetar su política.
- Los ejemplos del prompt anterior permanecen disponibles en el flujo completo;
  el modular conserva sus reglas normativas y no envía la lista completa de ejemplos.

No se recortan bloques para cumplir un presupuesto. Los módulos y el contexto
seleccionado tienen huellas reproducibles. El inventario lista dependencias y
líneas exactamente duplicadas; no atribuye equivalencias semánticas automáticamente.

Con este catálogo completo, saldo reduce 61.67% de caracteres; total de pagos,
56.04%; gastos, 66.90%. Estado de cuenta y notas pendientes conservan más dependencias
(y reducen aproximadamente 45%). No son porcentajes de ahorro facturado.

## Flujo y restricciones

El registro local solo ofrece identificadores autorizados y herramientas conocidas.
El proveedor de referencia clasifica dominio y operación en una llamada breve.
Jev puede reemplazarlo sin decidir modelos, permisos, archivos o funciones arbitrarias.
Una ruta ambigua o inconsistente usa el flujo anterior. Las respuestas breves de
selección y «sí» mantienen el intérprete completo para resolver la intención pendiente.

Las operaciones de clientes solo ofrecen `consultar_negocio` con una operación
cerrada y `pedir_aclaracion`. El servicio existente sigue resolviendo identidades,
validando contexto y ejecutando SQL parametrizado. Una entidad inventada no se
acepta. Los parámetros se validan antes de la ejecución. Se permiten como máximo
dos intentos de interpretación por operación modular, incluido el respaldo; la
extracción secundaria se invoca una sola vez. Información faltante requiere
aclaración y no una escalada para inventarla.

Las consultas claras de movimientos/gastos pueden describir su fuente mediante
código, sin llamada de interpretación. El SQL sigue siendo exploratorio, necesita
esquema previo y pasa por los validadores existentes. Las tablas quedan restringidas
al dominio y a los permisos de configuración. No se suman muestras para producir
totales. Un mes explícito sin año pide precisión salvo una continuación explícita de un rango confirmado del mismo dominio; los períodos relativos siguen el calendario del negocio.

**Pendiente en código/contrato:** convertir gastos a una operación verificada tras
confirmar estados, importes, fechas, anulaciones y reglas contables. El catálogo
actual marca sus fuentes como `candidate`; no se promovieron a `verified` por el
solo hecho de tener ejemplos SQL. También falta resolver parámetros deterministas
y períodos elípticos con el estado sin depender de interpretación, y ampliar los
módulos a los dominios posteriores del plan.

El estado volátil guarda ruta, entidad confirmada por el servicio, período y
aclaración. El historial completo existente sigue presente: todavía no se reduce.
Un cambio de dominio limpia filtros incompatibles; `/reiniciar` borra historial y
estado. Las trazas y la bandera de reintento del asistente son locales al hilo;
los turnos del mismo remitente se serializan y la memoria usa un bloqueo reentrante.
El estado con identificadores se mantiene fuera de los eventos de consumo.

## Jev: contrato comprobado y límites

Documentación consultada el 2026-11-10:

- https://docs.typesafe.ai/api/system-one.md
- https://docs.typesafe.ai/primitives/choice.md
- https://docs.typesafe.ai/models.md

Endpoint fijo `https://api.typesafe.ai/v1/systemone`, Bearer `TYPESAFE_API_KEY`,
`model=jev-1.14.0`, una pregunta `choice` con opciones del registro. Se envía la
pregunta y el último intercambio conversacional; no el prompt completo, SQL ni
resultados de herramientas. La confianza y probabilidades se conservan separadas,
sin equipararlas a confianza de otros proveedores. Distribuciones inválidas,
rutas desconocidas, errores HTTP y timeouts generan un respaldo acotado. No hay
reintentos HTTP automáticos del adaptador Jev.

La cuenta no tiene `TYPESAFE_API_KEY`. No se midieron precisión, latencia ni costo
real de Jev; tampoco están confirmados los límites organizacionales ni los términos
de tratamiento de datos aplicables a esta cuenta. No activar fuera de observación
antes de verificar acceso y calibrar un umbral con casos en español. El ejemplo
0.8 del evaluador solo sirve para experimentar, no es un umbral recomendado.

## Configuración y reversión

`config/llm.modular.example.toml` contiene una configuración completa sin secretos.
`LLM_CONFIG` puede apuntar a una copia local. No reemplazar sin revisar otros perfiles,
transcripción y prompts locales. Las configuraciones existentes siguen funcionando.

```toml
[modular]
enabled = true
domains = ["clientes", "gastos"]
router = "reference"
shadow = false
# extraction_profile = "nano"  # exige perfil y disponibilidad comprobados
```

Para observar Jev, usar `router="jev"`, `shadow=true` y configurar el secreto en el
entorno. Para activar sus decisiones, `shadow=false` requiere `min_confidence`
explícito. El modo de observación nunca estrecha herramientas ni controla ejecución,
aunque sus llamadas sí consumen API. Quitar `extraction_profile` vuelve al perfil
principal. Quitar un dominio lo devuelve al flujo anterior. `enabled=false` revierte
todo el enrutamiento modular; no requiere migración de datos.

El perfil principal es también el respaldo y explorador. Por defecto es mini, pero
se respeta la elección preexistente de otros proveedores. Nunca se cambia el modelo
global para habilitar nano. Si falta la clave del perfil de extracción, se utiliza
el principal. Una clave presente con un modelo no disponible produce un intento
fallido registrado y un respaldo, no una respuesta sin validar.

## Evaluación y costo

Reproducir (consume API, solo datos sintéticos, sin conexiones a SQL):

```sh
python -m dbagg.evaluation.modular \
  --dataset evals/cases/modular-development.json \
  --output work/modular-evaluation --repeat 1
```

El evaluador ejecuta la orquestación real y captura la primera herramienta ejecutable,
aplicando restricciones de herramientas y de esquema antes del SQL. No evalúa
respuestas financieras ni debe confundirse con una prueba completa contra SQL Server.
Los casos son de **desarrollo**, usados para corregir reglas; no son un conjunto
final independiente. Los tests de negocio usan datos sintéticos y resultados conocidos.

La última ejecución completa está en `evaluations/modular-development/final.json`;
la primera, con regresiones y sin los ajustes posteriores, en `initial.json`.
`report.html` presenta la comparación final. Se conservaron errores, intentos
fallidos y consumo desconocido. No se aplicó ni desplegó automáticamente ninguna variante.

| Variante | Decisiones correctas | Costo conocido estimado USD |
| --- | ---: | ---: |
| Flujo anterior + mini | 15/16 | 0.0224336 |
| Modular + referencia mini | 16/16 | 0.0276512 |
| Modular + referencia + nano | No evaluable: 404 del modelo; un respaldo observado | 0.0008496 y un intento de costo desconocido |
| Jev + mini / Jev + nano | No disponibles: falta credencial Jev | No medido |

**No hay ahorro total demostrado.** El costo conocido de modular con clasificador
mini subió aproximadamente 23.3% en esta muestra de primera decisión; no representa
una consulta financiera completa. Caché, orden de ejecución y muestra pequeña
limitan la comparación. No se atribuye a nano el resultado obtenido por el respaldo
mini. La primera ejecución intentó nano varias veces antes de confirmar el 404;
el evaluador ahora interrumpe esa variante tras un fallo permanente de acceso/modelo.

Los precios se versionan en `evaluation/cost.py`, consultados el 2026-11-10 en las
páginas oficiales de cada modelo. Se separa entrada normal y entrada en caché; la
caché OpenAI es parte de la entrada. Campos desconocidos no se convierten a cero.
Los eventos incluyen tarea, proveedor, modelo, duración y consumo informado; los
fallos sin uso informado conservan costo desconocido. La evaluación desactiva
reintentos de SDK para contar intentos observados; una configuración de producción
con reintentos internos no aporta desglose de esos intentos en esta primera versión.

Para aceptar la cascada faltan Jev real, nano disponible, casos finales separados,
repeticiones comparables, el benchmark real del PR #20, prueba contra SQL Server y
exactitud de respuesta/costo por consulta correcta incluyendo todos los respaldos.

## Medición integrada de conversaciones completas

La instrumentación de `feat/token-usage` está integrada, incluido el consumo real
informado por Jev y la separación por tarea. Ver `token-usage.md` para la comparación
reproducible del flujo completo con SQLite sintético, sin usar SQL de producción:

```sh
python scripts/compare_usage.py --output work/usage-compare-001
```

Carga `OPENAI_API_KEY` y `TYPESAFE_API_KEY` de `.env`. Genera `comparison.html`,
`comparison.json` y los manifiestos/JSONL de cada variante. `--mock` verifica la
instrumentación con números artificiales; no mide ahorro. Esta integración no
modifica ni reemplaza los resultados de desarrollo registrados anteriormente.
