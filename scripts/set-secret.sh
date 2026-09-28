#!/usr/bin/env bash
# Pide una API key una vez (sin eco) y la guarda como secret en cada repo dado.
# Uso: SECRET_NAME=DEEPSEEK_API_KEY scripts/set-secret.sh owner/repo [owner/repo ...]
set -euo pipefail
secret_name="${SECRET_NAME:-AI_REVIEW_API_KEY}"
read -rsp "$secret_name: " key
echo
for repo in "$@"; do
  printf '%s' "$key" | gh secret set "$secret_name" -R "$repo"
done
