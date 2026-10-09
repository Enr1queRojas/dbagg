# Desarrollo de dbagg

Usa el checkout existente. Conserva `.env` y los ajustes locales; no imprimas
credenciales ni datos de clientes. Mantén los cambios dentro del alcance solicitado.

## Dónde implementar

- HTTP y firma: `src/dbagg/api/`; Meta: `src/dbagg/integrations/`.
- Agente y prompts: `src/dbagg/agent/`; coordinación y memoria: `src/dbagg/services/`.
- Acceso SQL y validación: `src/dbagg/database/`. La lista de objetos permitidos se
  aplica en código y los permisos de la cuenta SQL deben ser de solo lectura.
- Reglas confirmadas: `src/dbagg/context/default.json`. Consulta `docs/business-context.md`;
  distingue una columna verificada de una fuente candidata por su nombre.
- Los scripts de la raíz son adaptadores de compatibilidad; no dupliques allí lógica.

## Cambios al comportamiento del agente

1. Describe la pregunta de negocio y el comportamiento esperado antes del cambio.
2. Usa ejemplos sintéticos. No agregues chats, filas reales, `.env`, reportes ni
   bases de evaluaciones al repositorio, fixtures o prompts.
3. Mantén las validaciones SQL y los límites fuera del modelo. No amplíes permisos
   ni elimines controles para resolver un fallo de razonamiento.
4. Si cambias instrucciones, actualiza `PROMPT_VERSION` en `src/dbagg/agent/prompts.py`.
   La versión del contexto se calcula a partir de su contenido combinado.
5. Reproduce errores deterministas con pruebas de regresión. Para exactitud contable,
   aplica `evals/rubric.md` y los casos sintéticos; una prueba con modelos simulados
   no demuestra que un modelo real responderá correctamente.
6. Una valoración positiva no autoriza entrenamiento. Solo exporta ejemplos revisados,
   corregidos y anonimizados mediante el flujo de `docs/evaluation.md`.

## Verificación

Desde la raíz, con `.venv` activado:

```sh
python -m ruff check .
python -m ruff format --check .
python -m unittest discover -s tests -v
python -m build
```

Ejecuta las comprobaciones pertinentes al cambio. Al reorganizar archivos, comprueba
también los comandos compatibles y los recursos del paquete instalado. Documenta
migraciones, limitaciones y pruebas reales pendientes. No declares validación contra
SQL Server, Meta u OpenAI cuando solo ejecutaste dobles de prueba.
