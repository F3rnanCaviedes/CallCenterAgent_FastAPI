#!/bin/sh
# Despliegue en el VPS. Lo invoca GitHub Actions por SSH con una clave
# restringida en ~/.ssh/authorized_keys del usuario de despliegue:
#
#   command="/opt/sofia/deploy.sh",restrict ssh-ed25519 AAAA... ci-deploy
#
# Con command= la clave no abre shell: sólo corre este script, y lo único que
# llega del CI es el SHA en SSH_ORIGINAL_COMMAND. Rollback manual:
#   ssh deploy@vps <sha-anterior>
set -eu

IMAGE_REPO="ghcr.io/f3rnancaviedes/callcenteragent_fastapi"
cd /opt/sofia

sha="${SSH_ORIGINAL_COMMAND:-${1:-}}"
# Sólo un SHA de 40 hex: nada que se interprete como opción o comando.
if ! printf '%s' "$sha" | grep -Eqx '[0-9a-f]{40}'; then
    echo "SHA inválido" >&2
    exit 1
fi

export SOFIA_IMAGE="$IMAGE_REPO:$sha"
docker compose pull migrate api
# --wait: falla si la API no llega a healthy (/health/ready).
docker compose up -d --no-build --wait
docker image prune -f >/dev/null
echo "Desplegado $sha"
