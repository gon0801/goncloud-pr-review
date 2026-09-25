#!/usr/bin/env bash
# Pide la API key de DeepSeek una vez (sin eco) y la guarda como secret en cada repo dado.
# Uso: scripts/set-secret.sh owner/repo [owner/repo ...]
set -euo pipefail
read -rsp "DEEPSEEK_API_KEY: " key
echo
for repo in "$@"; do
  printf '%s' "$key" | gh secret set DEEPSEEK_API_KEY -R "$repo"
done
