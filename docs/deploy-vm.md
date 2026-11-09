# Despliegue en una VM

Guía para mover el bot a una VM Linux x86-64 con el paquete de `scripts/package_deploy.py`.
El tráfico entra así: Meta → Cloudflare → `cloudflared` → Caddy → bot. La VM no abre puertos
de entrada.

## 1. Antes de empezar

- **Docker:** Docker Engine con Compose v2 (`docker compose version`), arquitectura `x86_64`
  (`uname -m`). Para otra arquitectura hay que construir la imagen en ella.
- **Salida a internet:** HTTPS hacia Meta, el proveedor del modelo y Cloudflare, y el puerto de
  SQL Server.
- **SQL Server:** si su firewall filtra por IP, autoriza la IP de salida de la VM. Usa una cuenta
  de solo lectura ([`scripts/sql/dbagg_readonly.sql`](../scripts/sql/dbagg_readonly.sql)).
- **Token de Meta:** debe ser de usuario del sistema, sin caducidad. Uno temporal deja al bot sin
  poder responder cuando caduca.

## 2. Crear el paquete (en el equipo actual)

```powershell
.\.venv\Scripts\python.exe scripts\package_deploy.py
```

Genera en `dist/` el archivo `dbagg-deploy-<revisión>.tar.gz` y su `.sha256`. Incluye prompts y
configuración privada, pero **no** `.env` ni `data/`. Trátalo como confidencial.

## 3. Instalar en la VM

Copia el paquete y su `.sha256` (por ejemplo, con `scp`). Después, usa el nombre exacto del paquete
que copiaste:

```sh
PAQUETE=dbagg-deploy-<revisión>.tar.gz
sha256sum -c "$PAQUETE.sha256"
sudo mkdir -p /opt/dbagg
sudo chown "$USER" /opt/dbagg
tar -xzf "$PAQUETE" -C /opt/dbagg --strip-components=1
cd /opt/dbagg
docker load -i images.tar
rm images.tar
```

## 4. Crear `.env`

Crea el archivo **antes** de arrancar: si falta, Docker crearía una carpeta con ese nombre. Parte
de `.env.example` o trae el de tu equipo por un canal seguro, nunca por correo o chat.

```sh
cp .env.example .env
nano .env
sudo chown "$USER":10001 .env
chmod 640 .env
```

El grupo 10001 es el del usuario del contenedor: así lee el archivo sin que sea público.

## 5. Probar sin tráfico público

Esto arranca solo el bot, sin túnel, así que los mensajes siguen llegando al equipo actual.

```sh
docker compose up -d --no-build dbagg
docker compose ps
curl -s http://127.0.0.1:8000/health
docker compose exec dbagg python -c "import pyodbc; from dbagg.config import Settings; c = pyodbc.connect(Settings.from_env().connection, timeout=10); print('SQL OK', c.execute('SELECT 1').fetchone()[0])"
```

Debes ver `healthy`, `{"status":"ok",…}` y `SQL OK 1`. Si SQL falla, revisa el firewall del paso 1
antes de seguir.

## 6. Cambiar de equipo

Habrá un corte breve. **No dejes dos conectores activos:** con el mismo token, Cloudflare reparte
el tráfico entre ambos y parte de los mensajes fallaría.

1. En el equipo actual, cierra `cloudflared` y Uvicorn.
2. En Cloudflare, abre la ruta `proadel.app/webhook` del túnel y cambia el servicio a
   `http://caddy:8080`.
3. En la VM:
   ```sh
   docker compose --profile tunnel up -d --no-build
   docker compose --profile tunnel ps
   docker compose --profile tunnel logs --tail=20 cloudflared
   ```
   En el log debe aparecer `Registered tunnel connection`.
4. Confirma en *Overview* del túnel que solo queda el conector de la VM.
5. Envía una pregunta por WhatsApp desde un número autorizado.

No hace falta tocar Meta: la URL sigue siendo `https://proadel.app/webhook`.

**Para volver atrás:** en la VM ejecuta `docker compose --profile tunnel down`, devuelve el servicio
de la ruta a `http://localhost:8000` y arranca Uvicorn y `cloudflared` en el equipo anterior.

## 7. Operación

```sh
docker compose --profile tunnel ps                # estado
docker compose --profile tunnel logs -f dbagg     # logs del bot
docker compose --profile tunnel restart dbagg     # tras cambiar .env
sudo systemctl enable docker                      # arrancar con la VM
```

**Actualizar:** con el despliegue automático por SSH basta hacer push a la rama `deploy`
([deploy-ssh.md](deploy-ssh.md)). Sin él, crea un paquete nuevo y, en la VM, repite la copia, `tar` y `docker load` del paso 3.
Luego ejecuta `docker compose --profile tunnel up -d --no-build`. Tu `.env` y el volumen se conservan.

**Datos:** el volumen `dbagg-data` guarda respuestas enviadas con datos del ERP. `down` lo conserva;
`down -v` lo **borra**. Para respaldarlo:

```sh
docker run --rm -v dbagg-data:/d:ro -v "$PWD":/b alpine tar -czf /b/dbagg-data.tar.gz -C /d .
```

El respaldo contiene datos de clientes: guárdalo cifrado o fuera de carpetas compartidas. El
historial del equipo actual no se migra; el bot empieza con el volumen vacío.

## Problemas comunes

| Síntoma | Causa probable |
|---|---|
| `PermissionError` con `.env` al arrancar | Faltan los permisos del paso 4 |
| Carpeta `.env` creada por Docker | Arrancaste sin el archivo: bórrala y repite el paso 4 |
| 502 en `proadel.app/webhook` | Conector caído, o la ruta no apunta a `http://caddy:8080` |
| Error de login o de conexión con SQL | Credenciales, o el firewall no autoriza la VM |
| `Session has expired` o `131030` en el log | Token de Meta caducado, o Meta en modo de prueba |
