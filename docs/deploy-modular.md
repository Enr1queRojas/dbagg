# Activar el flujo modular en producción

El código nuevo es compatible hacia atrás: al desplegarlo, el bot sigue con el intérprete completo
hasta que `config/llm.toml` de la VM active `[modular]`. Las correcciones del piloto (última nota de
clientes, «3» tras una lista, código dictado, cuenta bancaria tras «Cuenta X», rangos que terminan
hoy) llegan con el prompt empaquetado.

Resultado medido de esta configuración (T25-v2, `docs/evaluations/final-v2/summary.md`): costo por
consulta correcta −34 % frente al intérprete completo, 0.97 llamadas de enrutamiento por turno,
24 % de turnos en el intérprete completo y 3.8 puntos menos de acierto en la primera decisión.

## Orden

1. **Fusionar con `main`.** El workflow `deploy` corre solo cuando las pruebas pasan y reconstruye la
   imagen. No toca `.env`, `config/llm.toml` ni `config/prompts/`.
2. **En la VM, después del despliegue** (`cd /opt/dbagg` o tu `DEPLOY_PATH`). No pongas las claves
   nuevas en `config/llm.toml` antes: la versión anterior rechaza `min_margin`, `compact_history` y
   las demás, y el bot no arrancaría en el siguiente reinicio.

   1. Respaldar la configuración actual:

      ```bash
      cp config/llm.toml config/llm.toml.antes-modular
      ```

   2. Añadir a `.env` la clave de TypeSafe Jev (`TYPESAFE_API_KEY=...`). La VM debe poder salir por
      HTTPS a `api.typesafe.ai`. Sin la clave, cada turno usa el clasificador mini de respaldo: más
      lento y caro, pero funciona.
   3. Editar `config/llm.toml`:
      - copiar la sección `[modular]` de `config/llm.production.example.toml`;
      - **quitar la sección `[prompts]`** (`system_file`, `tools_file`). Con prompts locales el
        modular se apaga (`modular_fallback=custom_prompts`) y las correcciones del prompt
        empaquetado no llegan. `config/prompts/` puede quedarse en disco como respaldo.

      Conserva tu perfil activo y sus parámetros; solo cambian esas dos secciones.
   4. Validar sin llamar a ninguna API:

      ```bash
      docker compose run --rm --no-deps dbagg python -m dbagg.llm check
      ```

      Debe mostrar `"modular": {"active": true, ..., "jev_api_key_present": true}`, `"warnings": []`
      y `prompt_version` empezando por `catalog-domains-v7:`.
   5. Reiniciar y esperar a que quede `healthy`:

      ```bash
      docker compose up -d
      ```

3. **Prueba de humo por WhatsApp**, con `/reiniciar` antes de cada bloque:
   - «Saldo de [cliente]» y después «¿Y su última nota?»: última nota de crédito, no un pago.
   - «Saldo de [apellido con homónimos]» y responder «2»: elige la segunda fila.
   - «El banco de [cuenta]» y luego «Dame el reporte de este mes»: PDF de la cuenta.
   - «¿Cuánto gastamos en [apartado] este mes?»: exploración de gastos.
   - «Hola»: saludo sin consultar nada.

## Alternativa: desplegar a mano desde la rama

Sin fusionar con `main`, en la VM (`cd /opt/dbagg` o tu `DEPLOY_PATH`):

1. Desplegar el código de la rama: `DBAGG_BRANCH=claude/blissful-mayer-xgcf54 bash scripts/deploy.sh`.
   El bot arranca con el intérprete completo y la configuración actual.
2. Respaldar y reemplazar la configuración: copiar `config/llm.toml` a `config/llm.toml.antes-modular`,
   mover `config/prompts/` a `config/prompts.antes-modular/` y copiar
   `config/llm.production.example.toml` a `config/llm.toml`.
3. Añadir `TYPESAFE_API_KEY=...` a `.env`.
4. Validar con `python -m dbagg.llm check` (paso 2.4) y reiniciar con `docker compose up -d`.

Mientras la rama no esté en `main`, un despliegue automático de `main` volvería al código anterior,
que rechaza la nueva `config/llm.toml`; `deploy.sh` revierte solo, pero conviene fusionar la rama
antes del siguiente push a `main`.

## Seguimiento

El registro de uso guarda por turno la ruta, `routing_level` (`operation`, `domain` o `legacy`),
los respaldos (`modular_fallback`) y el consumo por tarea (`routing`, `interpretation`). Señales a
vigilar:
- muchos `modular_fallback=route_inconsistent` o `wrong_route`: el enrutador elige mal;
- `task=routing` con `provider=reference`: Jev está fallando y se usa el mini;
- respuestas «¿De qué cliente…?» inesperadas después de un seguimiento.

## Volver atrás

Sin desplegar nada: en `config/llm.toml` pon `enabled = false` en `[modular]` (o restaura
`config/llm.toml.antes-modular`) y ejecuta `docker compose up -d`. El bot vuelve al intérprete
completo con las mismas correcciones del prompt empaquetado.
