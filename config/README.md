# Configuración local

Desde la raíz del proyecto, ejecuta `python -m dbagg.llm init` para crear:

- `config/llm.toml`: proveedor, modelo activo y parámetros.
- `config/prompts/agent.md`: instrucciones del agente.
- `config/prompts/tools.json`: descripciones de herramientas.

Edita `active_profile` en TOML y coloca la clave del proveedor en `.env`.
`python -m dbagg.llm check` valida los archivos y muestra si la variable de clave existe,
sin imprimirla ni llamar servicios externos. Reinicia el backend al cambiar modelo;
el contenido de los prompts se recarga con cada pregunta.

Los archivos generados son locales e ignorados por Git. El comando no reemplaza
archivos existentes. La configuración anterior OpenAI sigue funcionando sin generarlos.
Consulta [la guía completa](../docs/llm-configuration.md) para perfiles y precedencia.

El contexto de negocio local permanece en `config/business_context.json`; no concede
permisos SQL ni modifica automáticamente los contratos de saldos y pagos.
