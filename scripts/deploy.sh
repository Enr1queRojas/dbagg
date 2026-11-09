#!/usr/bin/env bash
# Actualiza el checkout de la VM desde git, reconstruye la imagen y reinicia el bot.
#
#   bash scripts/deploy.sh [directorio] [revisión]
#
# Lo ejecuta el workflow .github/workflows/deploy.yml por SSH, pero también sirve a mano en la VM.
# Por omisión usa /opt/dbagg y la punta de origin/deploy. Si el bot no queda sano, vuelve a la
# imagen y revisión anteriores. Guía: docs/deploy-ssh.md.
set -euo pipefail

DIR=${1:-${DBAGG_DIR:-/opt/dbagg}}
BRANCH=${DBAGG_BRANCH:-deploy}
REV=${2:-origin/$BRANCH}
HEALTH_TIMEOUT=${DBAGG_HEALTH_TIMEOUT:-120}

log() { printf '==> %s\n' "$*"; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

cd "$DIR" || fail "No existe $DIR: clona el repositorio primero (docs/deploy-ssh.md)."
[ -d .git ] || fail "$DIR no es un checkout de git."
# Sin el archivo, Docker crearía una carpeta .env y el bot no arrancaría.
[ -f .env ] || fail "Falta $DIR/.env: créalo antes de desplegar."

# Un solo despliegue a la vez. El candado vive en .git para no ensuciar el checkout.
exec 9>.git/dbagg-deploy.lock
flock -n 9 || fail "Ya hay otro despliegue en curso."

log "Descargando $BRANCH desde origin"
git fetch --prune origin "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH"
PREVIOUS_REV=$(git rev-parse HEAD)
# Sobrescribe solo archivos versionados: .env, config/llm.toml, config/prompts/ y data/ se conservan.
git checkout --quiet --force --detach "$REV"
REVISION=$(git rev-parse --short HEAD)
log "Revisión $(git log -1 --format='%h %s')"

HAD_PREVIOUS=false
if docker image inspect dbagg:local >/dev/null 2>&1; then
    docker tag dbagg:local dbagg:previous
    HAD_PREVIOUS=true
fi

rollback() {
    printf 'ERROR: %s\n' "$1" >&2
    docker compose logs --tail=50 dbagg >&2 || true
    git checkout --quiet --force --detach "$PREVIOUS_REV"
    if [ "$HAD_PREVIOUS" = true ]; then
        log "Volviendo a la imagen y revisión anteriores ($(git rev-parse --short HEAD))"
        docker tag dbagg:previous dbagg:local
        docker compose up -d --no-build --remove-orphans || true
    fi
    exit 1
}

log "Construyendo la imagen"
docker compose build --pull dbagg || rollback "Falló la construcción de la imagen."
docker tag dbagg:local "dbagg:$REVISION"

# COMPOSE_PROFILES=tunnel en .env arranca también Caddy y cloudflared (después del cambio de equipo).
log "Reiniciando servicios"
docker compose up -d --no-build --remove-orphans || rollback "docker compose up falló."

log "Esperando a que el bot esté sano (máximo ${HEALTH_TIMEOUT}s)"
CONTAINER=$(docker compose ps -q dbagg)
[ -n "$CONTAINER" ] || rollback "El contenedor dbagg no arrancó."
deadline=$((SECONDS + HEALTH_TIMEOUT))
while :; do
    status=$(docker inspect --format '{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null || echo missing)
    case "$status" in
        healthy) break ;;
        unhealthy | missing) rollback "El bot quedó $status." ;;
    esac
    [ "$SECONDS" -lt "$deadline" ] || rollback "El bot no quedó sano en ${HEALTH_TIMEOUT}s."
    sleep 3
done

# Conserva dbagg:local, dbagg:previous y las 3 revisiones más recientes para volver atrás a mano.
docker images dbagg --format '{{.CreatedAt}}\t{{.Tag}}' | sort -r | cut -f2 \
    | grep -vxE 'local|previous' | tail -n +4 | sed 's/^/dbagg:/' \
    | xargs -r docker rmi >/dev/null 2>&1 || true
docker image prune -f >/dev/null

log "Desplegado $REVISION"
docker compose ps
