# Despliegue automático por SSH

Cada push a `main` cuyas pruebas (workflow `checks`) pasan ejecuta
[`.github/workflows/deploy.yml`](../.github/workflows/deploy.yml):
GitHub Actions entra por SSH a la VM y corre [`scripts/deploy.sh`](../scripts/deploy.sh), que:

1. Descarga `origin/main` y deja el checkout de la VM en esa revisión exacta.
2. Construye la imagen `dbagg:local` en la VM (y la etiqueta también como `dbagg:<revisión>`).
3. Reinicia los servicios con `docker compose up -d` y espera a que el bot quede `healthy`.
4. Si la construcción o el arranque fallan, vuelve a la imagen y revisión anteriores.

`.env`, `config/llm.toml`, `config/prompts/`, `config/business_context.json` y el volumen
`dbagg-data` no están en git: viven solo en la VM y cada despliegue los conserva.

También puede lanzarse a mano desde *Actions → deploy → Run workflow*, o en la VM con
`bash scripts/deploy.sh`.

## 1. Preparar la VM (una vez)

Requisitos de [deploy-vm.md](deploy-vm.md#1-antes-de-empezar): Docker Engine con Compose v2,
salida a internet y acceso a SQL Server. Además, `git` y un usuario dedicado que pueda usar Docker:

```sh
sudo apt-get install -y git
sudo useradd --create-home --shell /bin/bash deploy
sudo usermod -aG docker deploy
sudo mkdir -p /opt/dbagg
sudo chown deploy /opt/dbagg
sudo systemctl enable docker
```

El grupo `docker` equivale a acceso root en la VM: úsalo solo para este usuario.

### Clave para leer el repositorio (deploy key)

Como el repositorio es privado, la VM necesita una clave de solo lectura. Como usuario `deploy`:

```sh
ssh-keygen -t ed25519 -N '' -C dbagg-vm -f ~/.ssh/github_dbagg
cat >> ~/.ssh/config <<'EOF'
Host github.com
    IdentityFile ~/.ssh/github_dbagg
    IdentitiesOnly yes
EOF
ssh-keyscan github.com >> ~/.ssh/known_hosts
cat ~/.ssh/github_dbagg.pub
```

En GitHub: *Settings → Deploy keys → Add deploy key*, pega la clave pública y **no** marques
*Allow write access*.

### Clonar y configurar

```sh
git clone git@github.com:Enr1queRojas/dbagg.git /opt/dbagg
cd /opt/dbagg
cp .env.example .env
nano .env
sudo chown deploy:10001 .env
chmod 640 .env
```

Copia también `config/llm.toml`, `config/prompts/` y `config/business_context.json` desde el
equipo actual por un canal seguro (por ejemplo `scp`): git no los lleva.

Primer despliegue a mano, para comprobar todo antes de automatizar:

```sh
bash scripts/deploy.sh
curl -s http://127.0.0.1:8000/health
```

Sigue el [paso 5 de deploy-vm.md](deploy-vm.md#5-probar-sin-tráfico-público) para probar SQL.

## 2. Clave para que GitHub Actions entre a la VM

En tu equipo (no en la VM) genera un par de claves solo para Actions:

```sh
ssh-keygen -t ed25519 -N '' -C github-actions-dbagg -f dbagg_actions
```

Agrega `dbagg_actions.pub` al `~/.ssh/authorized_keys` del usuario `deploy` en la VM,
anteponiendo `restrict` para quitarle reenvío de puertos, agente y terminal (el workflow no los usa):

```text
restrict ssh-ed25519 AAAA... github-actions-dbagg
```

Obtén la huella del servidor para fijarla (evita ataques de intermediario):

```sh
ssh-keyscan -p 22 IP_O_HOST_DE_LA_VM
```

## 3. Secretos del repositorio

En GitHub: *Settings → Secrets and variables → Actions → New repository secret*.

| Secreto | Valor |
|---|---|
| `DEPLOY_HOST` | IP o nombre de la VM |
| `DEPLOY_USER` | `deploy` |
| `DEPLOY_SSH_KEY` | Contenido completo de `dbagg_actions` (la clave **privada**) |
| `DEPLOY_KNOWN_HOSTS` | Salida de `ssh-keyscan` del paso anterior |
| `DEPLOY_PORT` | Opcional; `22` por omisión |
| `DEPLOY_PATH` | Opcional; `/opt/dbagg` por omisión |

Mientras falten `DEPLOY_HOST` o `DEPLOY_USER`, el workflow termina con un aviso sin desplegar.
Después de guardar la clave privada en GitHub, borra `dbagg_actions` de tu equipo.

## 4. Puerto SSH

GitHub Actions entra desde IPs públicas que cambian, así que la VM debe aceptar SSH desde internet
en `DEPLOY_PORT`. Esto contradice la regla de [deploy-vm.md](deploy-vm.md) de no abrir puertos
de entrada; si no es aceptable, hay dos alternativas:

- Un [runner autohospedado](https://docs.github.com/actions/hosting-your-own-runners) en la VM:
  cambia `runs-on: ubuntu-latest` por `runs-on: self-hosted` y ejecuta `bash scripts/deploy.sh`
  directamente, sin SSH.
- Exponer SSH por el túnel de Cloudflare con Cloudflare Access y un token de servicio.

Si abres el puerto: solo autenticación por clave (`PasswordAuthentication no`), sin login de root
(`PermitRootLogin no`) y, de preferencia, `fail2ban`.

## 5. Túnel y tráfico público

`scripts/deploy.sh` arranca solo los servicios sin perfil (el bot). Cuando la VM ya atiende el
tráfico público ([paso 6 de deploy-vm.md](deploy-vm.md#6-cambiar-de-equipo)), agrega a `.env`:

```sh
COMPOSE_PROFILES=tunnel
```

Desde entonces cada despliegue también mantiene Caddy y `cloudflared`. **No lo agregues antes del
cambio de equipo:** dos conectores con el mismo token reparten el tráfico y parte de los mensajes
fallaría.

## 6. Desplegar

Haz merge de un PR a `main` (o push directo). Cuando el workflow `checks` termina en verde, el
workflow `deploy` despliega esa revisión; si las pruebas fallan, no despliega. Sigue el progreso en
*Actions → deploy*. Antes de desplegar revisa la sección de migración de
[CHANGELOG.md](../CHANGELOG.md): los `GRANT` de SQL y los prompts de `config/prompts/` se aplican
a mano en la VM.

## Volver atrás

El despliegue revierte solo si el bot no queda sano. Para volver a mano a una revisión anterior:

```sh
cd /opt/dbagg
docker images dbagg                     # revisiones disponibles
bash scripts/deploy.sh /opt/dbagg <revisión>
```

O revierte el commit en `main` y haz push: el workflow despliega la reversión.

## Problemas comunes

| Síntoma | Causa probable |
|---|---|
| `Host key verification failed` | `DEPLOY_KNOWN_HOSTS` no coincide con la huella de la VM |
| `Permission denied (publickey)` | La clave pública no está en `authorized_keys` de `DEPLOY_USER` |
| `Permission denied` al hablar con Docker | `DEPLOY_USER` no está en el grupo `docker` (cierra la sesión tras `usermod`) |
| `git fetch` pide credenciales o falla | Falta la deploy key de la VM en GitHub |
| `Falta /opt/dbagg/.env` | Crea el archivo como en el paso 1 |
| `Ya hay otro despliegue en curso` | Otro despliegue sigue corriendo; espera a que termine |
