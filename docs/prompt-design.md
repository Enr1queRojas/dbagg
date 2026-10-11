# Diseño y evaluación del prompt v5

El objetivo es elegir correctamente operación, cliente y período sin ampliar las capacidades
actuales. Por ejemplo, después de consultar un cliente, un ranking debe ser global; un saldo
histórico debe declarar la limitación, no mostrar el saldo actual. La versión v5 es una
propuesta que requiere evaluación con el modelo real antes de sustituir el prompt probado.

## Responsabilidades

| Lugar | Responsabilidad |
| --- | --- |
| `agent/templates/system.md` | Interpretar intención, referencias y límites de alcance. |
| `agent/templates/tools.json` | Explicar cuándo usar cada herramienta. |
| `agent/prompts.py` | Construir mensajes y esquemas de argumentos. |
| `agent/service.py` | Coordinar llamadas, ejecutar herramientas y conservar evidencia. |
| `business/customers.py` | Reglas contables, consultas parametrizadas y respuesta verificada. |
| `context/default.json` | Significado y estado de verificación de fuentes de negocio. |

`Assistant.answer` mantiene el estado de una pregunta: presupuesto, objetos descritos y último
resultado. Delega trabajo concreto a métodos de la misma clase. No hay registro dinámico de
plugins, herencia de herramientas ni otro framework. Las reglas SQL y financieras permanecen
fuera del prompt. Las fuentes candidatas del contexto no pueden eludir contratos verificados.

## Decisiones del diseño

- Prioridades explícitas: instrucciones del servicio, contratos de herramientas y datos de la
  conversación separados por función. Una fila de SQL nunca es una nueva instrucción.
- Flujo breve: interpretar, elegir operación, aclarar si falta información y explorar solo
  cuando corresponde. No se solicita cadena de pensamiento ni exposición de razonamiento.
- Referencias delimitadas: cliente confirmado frente a lista de candidatos, continuidad de
  período frente a cambio de tema. El historial orienta; las cifras se consultan de nuevo.
- Abstención concreta: no sustituir reportes históricos, estados de cuenta completos o
  comparación de clientes por una operación parecida. Las aclaraciones son mensajes del código.
- Ejemplos contrastivos con identidades ficticias. No incluyen saldos reales ni respuestas que
  puedan copiarse como evidencia. El formato usa Markdown y JSON normal, sin funciones de un
  proveedor particular.

Una instrucción no garantiza cumplimiento. El control de acceso, SQL y evidencia sigue en código.
El prompt tampoco resuelve colas persistentes, entregas fallidas, reportes aún no implementados
ni pérdida de contexto que ya expiró. Consulta los pendientes en `refactor-roadmap.md`.

## Aplicar sin perder tu configuración

Actualizar Git no cambia `config/prompts/agent.md` ni `tools.json` existentes: son archivos locales.
Para probar v5 y conservar una reversión sencilla, desde la raíz del checkout actualizado:

1. Copia `src/dbagg/agent/templates/system.md` a `config/prompts/agent-v5.md` y
   `src/dbagg/agent/templates/tools.json` a `config/prompts/tools-v5.json` (archivos nuevos).
2. Conserva el perfil OpenAI y su modelo. En la sección `[prompts]` de `config/llm.toml` cambia:

   ```toml
   [prompts]
   system_file = "prompts/agent-v5.md"
   tools_file = "prompts/tools-v5.json"
   ```

3. Ejecuta `.\.venv\Scripts\python.exe -m dbagg.llm check`. Verifica perfil, modelo y huella;
   este comando no consulta APIs ni SQL. Reinicia Uvicorn porque cambiaste las rutas.
4. Envía `/reiniciar` antes de cada escenario independiente. Cloudflare puede seguir abierto.

Para volver, restaura las dos rutas originales y reinicia Uvicorn. El contenido de los archivos
se recarga por pregunta; cambiar las rutas requiere reiniciar. La huella refleja el contenido
real: el prefijo v5 por sí solo no demuestra que reemplazaste un prompt local antiguo.
Las variantes locales `config/prompts/` se excluyen de Git y de la distribución del paquete.

## Comparación antes de aprobar

Usa `evals/cases/customer-reports.json` para evaluar identidad, fuente, fechas y cálculos, y
`evals/cases/intent-routing.json` para selección de herramientas y referencias. Son conjuntos
sintéticos; los casos de intención no consultan producción automáticamente. Las llamadas
esperadas son criterios para observar al modelo, no respuestas simuladas que prueben comprensión.

Mantén fijo el modelo y ejecuta cada escenario al menos tres veces con el prompt anterior y el
nuevo. Conserva el historial indicado en cada caso y reinicia entre casos. Registra localmente:
modelo, huella del prompt, caso, operación, cliente, período, resultado, duración y costo cuando
el proveedor los informe. Compara en la misma base de pruebas o verifica contra una referencia
actual, porque los datos reales pueden cambiar entre ejecuciones.

Aprueba solo si todos los casos críticos de aislamiento, fechas, identidad y evidencia cumplen
la rúbrica y no aparecen regresiones en el flujo ya probado. Aumentar el tamaño del prompt no
prueba una mejora; mide errores y costo. Los tests con proveedores simulados verifican controles
del programa, no la interpretación de OpenAI. Las claves y el modelo real siguen siendo requisitos
para esa evaluación. `/buena` y `/mala motivo` registran valoración si FEEDBACK_ENABLED está activo;
la valoración se revisa antes de aprobar un ejemplo. No hay entrenamiento automático.
