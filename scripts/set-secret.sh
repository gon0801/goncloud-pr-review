#!/usr/bin/env bash
# Pide la API key del revisor (OpenCode Go o DeepSeek) una vez (sin eco) y la guarda como secret en cada repo dado.
# Uso: scripts/set-secret.sh owner/repo [owner/repo ...]
set -euo pipefail
read -rsp "AI_REVIEW_API_KEY: " key
echo
for repo in "$@"; do
  printf '%s' "$key" | gh secret set AI_REVIEW_API_KEY -R "$repo"
done
