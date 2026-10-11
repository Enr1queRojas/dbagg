# Evaluación y mejora del agente

Una respuesta útil no necesariamente es correcta. Separamos tres pasos:

1. **Valoración del usuario:** marca buena/mala, sin convertirla en verdad verificada.
2. **Revisión del responsable:** compara con SQL/reporte oficial, revisa reglas y corrige.
3. **Ejemplo aprobado:** reemplaza datos reales por sintéticos, verifica esa versión y
   aprueba su exportación. El entrenamiento o fine-tuning sería un trabajo posterior.

## Valorar desde WhatsApp

Activa en el `.env` local y reinicia Uvicorn:

```dotenv
FEEDBACK_ENABLED=true
```

Después de una respuesta puedes enviar:

```text
/buena
/mala Usó la fecha de captura en vez de la fecha de pago
```

La valoración se refiere a la última respuesta entregada a ese remitente. No llama
al modelo ni modifica la respuesta. Puedes corregir la valoración; hacerlo devuelve
el caso a revisión pendiente. `/reiniciar` y 30 minutos sin actividad eliminan la
referencia en memoria. Un diagnóstico o confirmación de valoración no sustituye la
última respuesta del agente. Los comandos de control no esperan el límite de diez
segundos aplicado a preguntas, pero sí pasan autorización, firma y deduplicación.

Si una consulta falla y se entrega el mensaje de error, se invalida la referencia de
valoración anterior. `/mala` después de ese error no puede etiquetar por accidente
la consulta que sí había funcionado. El historial de conversación se conserva.
La evaluación separada de fallos de disponibilidad queda para la entrega de observabilidad.

El registro se crea solo al valorar y solo cuando está habilitado. Guarda ID aleatorio,
fecha, pregunta, respuesta, historial reciente, motivo, modelo y versiones de prompt/contexto.
No guarda el teléfono del remitente, tokens, SQL ni filas completas de resultados.
La pregunta o respuesta puede contener datos privados: `data/` es local e ignorado por
Git. Evita sincronizar esta carpeta fuera del equipo autorizado; decide una retención
según tu operación. Desactivar el flag no borra registros anteriores.

## Revisar localmente

Desde la raíz del repositorio, con el entorno virtual activado:

```cmd
python -m dbagg.evaluation summary
python -m dbagg.evaluation pending
python -m dbagg.evaluation show ID_DE_LA_EVALUACION
```

`summary` y `pending` no muestran contenidos de chats. `show` sí muestra el caso local
para revisión: no compartas esa salida sin anonimizar. El ID lo devuelve WhatsApp al valorar.

Aplica [la rúbrica](../evals/rubric.md). Si la respuesta es mala, determina si falló la
fuente, una columna, la relación, el período, el cálculo, el contexto conversacional o
la redacción. Corrige primero el contexto de negocio o el código responsable. Añade una
prueba de regresión cuando el fallo sea comprobable, no cambies el prompt para memorizar
un saldo de un cliente.

## Aprobar un caso corregido y anonimizado

Crea un archivo **nuevo** en `data/reviewed/caso.json` tomando como forma
`evals/reviewed-case.example.json`. Sustituye nombres, códigos, importes y otros datos
sensibles por datos sintéticos coherentes. Incluye el historial necesario para un
seguimiento como “¿y su último pago?”, siempre con roles alternados user/assistant.
La respuesta final debe ser la correcta incluso si el original se valoró como malo.

`anonymized:true` y `verified:true` son declaraciones del revisor, no detecciones
automáticas de privacidad o exactitud. No marques esos campos sin revisar el contenido.

```cmd
python -m dbagg.evaluation review ID --decision approved --reviewer RESPONSABLE --case data/reviewed/caso.json
python -m dbagg.evaluation review OTRO_ID --decision rejected --reviewer RESPONSABLE
python -m dbagg.evaluation export --output data/exports/revisados-v1.jsonl
```

La exportación incluye solo los mensajes preparados de casos aprobados. No exporta
la pregunta/respuesta original por accidente, ni notas privadas, ni datos de remitentes.
No sobrescribe un archivo existente. No envía el dataset a OpenAI ni entrena un modelo.
La cuenta de OpenAI no se necesita para usar estos comandos.

## Medir mejoras

Antes de cambiar prompt, contexto o modelo, ejecuta manualmente los casos sintéticos
de [evals/cases/customer-reports.json](../evals/cases/customer-reports.json) en una
base de pruebas o con servicios simulados. Estos casos no se ejecutan contra producción
automáticamente. Registra versión, criterio esperado y resultado por caso.

Compara proporción correcta sobre **casos revisados**, número de errores de fecha/fuente,
clarificaciones necesarias y latencia/costo observados. No uses la proporción de votos
positivos como sustituto de exactitud: los votos son una muestra voluntaria y sesgada.
No uses casos reservados para evaluación como datos de entrenamiento.
