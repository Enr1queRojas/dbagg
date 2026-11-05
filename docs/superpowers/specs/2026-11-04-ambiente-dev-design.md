# Ambiente dev de dbagg

## Para qué

Probar cambios del bot conversando por WhatsApp, sin riesgo para producción. Hoy, probar con Meta
implica tocar credenciales y callbacks a mano. El último intento falló sin dar pistas: el secreto no
coincidía, los mensajes no llegaban y no estaba claro si el número de prueba era el de producción.

El criterio para publicar un cambio es que el responsable quede conforme con los mensajes que envía
y recibe en el WhatsApp de prueba. Los tests automáticos son necesarios, pero no bastan.

## Flujo de trabajo

1. Crear una rama y desarrollar.
2. Levantar dev en la PC con un solo comando y conversar con el número de prueba.
3. Repetir hasta quedar conforme.
4. Abrir un PR a `main`, esperar el CI e integrar.
5. Desde `main`, crear el paquete, que lleva la imagen y `config/` de la PC.
6. En la VM: instalar el paquete, validar sin afectar el servicio, levantar Docker y mandar
   `/diagnostico` al número real.
7. Si falla, reinstalar el paquete anterior.

## Decisiones

**Del responsable**

- Dev corre en la PC, solo mientras se trabaja. Producción corre en la VM, siempre encendida.
- Producción usa el número propio de la empresa; dev, el número de prueba de Meta.
- Los cambios de prompts y modelo pasan a producción junto con la imagen, en el mismo paquete.
- No se usa una carpeta aparte: la PC trabaja con el `.env` de la raíz del repo.
- Dev usa el puerto 8001.

**Propuestas en este diseño, pendientes de aprobación**

- Dev tiene su propia app de Meta. La app de producción no se toca.
- El `.env` de la PC solo tiene credenciales de dev. Las de producción viven solo en la VM.
- Dev comparte SQL Server con producción, con una cuenta de solo lectura.
- Dev usa quick tunnel de Cloudflare, sin token. El túnel de producción y su token no se usan en la
  PC: dos conectores con el mismo token repartirían el tráfico de producción.
- Dev corre con Uvicorn en la PC, no en Docker. La imagen se valida en la VM antes de activarla.
- Los paquetes se crean solo desde `main`, con el árbol limpio.
- Volver atrás es reinstalar el paquete anterior, porque contiene la imagen y `config/`.
- `WEBHOOK_PUBLIC_URL`, guardada en un stash, no se retoma: el script de callback no corre en la VM,
  y en la PC la URL cambia con cada túnel. La protección pasa a ser el bloqueo de la pieza 2.

## Objetivos

- Levantar dev con un solo comando.
- Que un error de configuración de Meta diga cuál es, en vez de que los mensajes no lleguen sin más.
- Que desde la PC no se pueda mover el callback de producción, ni por error.
- Ver qué cambió en `config/` antes de publicarlo.
- Detectar una variable faltante antes de reemplazar el bot en producción.

## Fuera de alcance

- Un ambiente intermedio o dev encendido permanentemente.
- Una base de datos de prueba o datos ficticios.
- Despliegue automático desde GitHub.
- Versionar `config/` en git.
- Correr dev en Docker.

## Lo que sería un fracaso aunque funcione

- Que una prueba cambie el callback, el token o el comportamiento de producción.
- Que un experimento de `config/` llegue a producción sin que el responsable lo vea antes.
- Que subir dev o publicar requiera volver a editar credenciales a mano.
- Que un secreto aparezca en logs, salida de consola, git o el paquete.

## Piezas

### 1. Revisión de configuración de dev (`scripts/dev_check.py`, nuevo)

Solo lectura, sin mostrar secretos. Lee el `.env` e informa cada punto como correcto o con el arreglo
concreto:

- El token es válido e indica nombre e ID de su app.
- `META_APP_SECRET` pertenece a esa app. Se comprueba con la consulta de suscripciones usando el
  token de app; un error `190` significa que el secreto no coincide.
- El número existe y está conectado; muestra número visible, ID y callback actual.
- El campo `messages` está suscrito en la app.
- La cuenta de WhatsApp está suscrita a la app (`GET /{WABA}/subscribed_apps`). El token no permite
  descubrir el ID de la cuenta, así que se agrega la variable opcional `META_WABA_ID`. Sin ella, ese
  punto se informa como no comprobado, nunca como correcto.
- `WHATSAPP_ALLOWED_NUMBERS` tiene un formato válido. Si un número es de México de 12 dígitos, avisa
  que Meta puede enviarlo como `521` + 10 dígitos.
- Bloqueo: si el callback actual apunta al host de producción, termina con error y lo explica.

### 2. Protección en `scripts/update_webhook_url.py`

Antes de escribir en Meta, consulta el callback actual del número y de la app. Si alguno apunta a
`proadel.app`, se niega a cambiarlo, aunque las credenciales sean válidas. Sin opción para saltarse
el bloqueo: mover producción de forma deliberada se hace desde el panel de Meta. También debe mostrar
número y callback antes de escribir. El host de producción queda en una constante.

### 3. Arranque de dev (`scripts/dev.ps1`, nuevo)

Con un solo comando:

1. Corre la revisión de la pieza 1; se detiene si encuentra un error.
2. Levanta Uvicorn en `127.0.0.1:8001`, con un worker y sin log de acceso.
3. Levanta el quick tunnel hacia ese puerto.
4. Espera a tener la URL y corre `update_webhook_url.py`.
5. Muestra la URL y el siguiente paso: mandar `/diagnostico` al número de prueba.
6. Al terminar con `Ctrl+C`, detiene Uvicorn y el túnel sin dejar procesos sueltos.

### 4. Paquete desde `main` (`scripts/package_deploy.py`)

- Se niega a crear el paquete fuera de `main` o con cambios sin commit. Hoy solo avisa si hay cambios.
- Incluye un manifiesto con el SHA-256 de cada archivo de `config/`. Compara con el último paquete de
  `dist/` y lista los archivos agregados, cambiados y eliminados. Sin contenidos.
- Pide confirmación si hubo cambios en `config/`, con una opción no interactiva para los tests.
- `.env` y `data/` siguen excluidos, como hoy.

### 5. Validación y regreso en la VM (`docs/deploy-vm.md`)

Sección "Actualizar" revisada:

1. Conservar el paquete anterior en la VM.
2. Instalar el nuevo.
3. Antes de `up`, validar la imagen con el `.env` real:
   ```sh
   docker compose run --rm --no-deps dbagg python -c "from dbagg.config import Settings; Settings.from_env(); print('OK')"
   ```
   El bot en servicio sigue con la imagen anterior hasta el `up`.
4. Si imprime `OK`, ejecutar `docker compose --profile tunnel up -d --no-build`.
5. Mandar `/diagnostico` al número real.

Hay que documentar y ensayar el regreso con el paquete anterior. `tar` no borra archivos eliminados
entre versiones, así que `config/prompts` debe limpiarse antes de reinstalar. Ese ensayo debe
hacerse sin afectar el servicio, o en una ventana acordada.

### 6. Guía y plantilla

- `docs/dev-environment.md`, nueva: crear la app de Meta, llenar `.env`, usar `dev.ps1`, y una tabla
  de síntomas por línea de log.
- `.env.example`: agregar `META_WABA_ID`, comentado como opcional.
- Enlaces desde `README.md` y `docs/whatsapp-setup.md`, y entrada en `CHANGELOG.md`.

## Limitaciones

- Dev lee datos reales, y las respuestas pasan por el proveedor LLM y Meta. Dev solo responde a los
  números de `WHATSAPP_ALLOWED_NUMBERS`.
- Dev consume la misma cuenta LLM si usa la misma clave. Una clave aparte con límite de gasto es
  recomendable.
- El número de prueba de Meta solo responde a destinatarios verificados.
- `data/` de la PC conserva historial del tiempo en que el bot corría ahí, con posibles datos de
  clientes. Dev lo usaría tal cual. Una respuesta pendiente de menos de 1 hora se reenviaría al
  arrancar. Con credenciales de dev, ese envío saldría del número de prueba.
- Dev corre en Windows y producción en Linux. La validación de la VM cubre configuración e imagen,
  no todo el comportamiento.

## Pasos manuales previos

- Crear la app de Meta de dev y llenar el `.env`. Hoy tiene datos de la app ya borrada.
- Confirmar que el `META_PHONE_NUMBER_ID` de dev sea distinto del de la VM.
- Confirmar que producción responde a `/diagnostico` en `proadel.app`: la app borrada pudo afectar el
  número si era compartido.

## Pruebas

- Tests unitarios, con Meta simulado, para cada resultado de la revisión, el bloqueo de
  `proadel.app` y las reglas del paquete. Deben cubrir también que los secretos no aparezcan en la
  salida.
- Prueba manual completa: `dev.ps1`, `/diagnostico` y una pregunta real.
- Prueba manual del bloqueo: confirmar que el script se niega sin llegar a escribir en Meta.

## Decisiones libres para quien lo implemente

Formato de salida de la revisión, cómo se obtiene la URL del túnel en `dev.ps1`, formato del
manifiesto y cómo se detienen los procesos en Windows.
