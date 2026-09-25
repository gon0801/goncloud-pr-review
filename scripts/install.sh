#!/usr/bin/env bash
# Abre un PR que agrega .github/workflows/ai-review.yml a cada repo dado.
# Uso: scripts/install.sh owner/repo [owner/repo ...]
# Exclusiones extra por repo: EXCLUDE=$'data/**\nout/**' scripts/install.sh owner/repo
set -euo pipefail

here="$(cd "$(dirname "$0")/.." && pwd)"
branch="chore/ai-review"

for repo in "$@"; do
  content="$(cat "$here/templates/ai-review.yml")"
  if [ -n "${EXCLUDE:-}" ]; then
    content+=$'\n          exclude: |'
    while IFS= read -r line; do
      [ -n "$line" ] && content+=$'\n            '"$line"
    done <<< "$EXCLUDE"
  fi
  content+=$'\n'

  default="$(gh api "repos/$repo" --jq .default_branch)"
  base_sha="$(gh api "repos/$repo/git/ref/heads/$default" --jq .object.sha)"
  gh api "repos/$repo/git/refs" -f ref="refs/heads/$branch" -f sha="$base_sha" >/dev/null 2>&1 \
    || gh api -X PATCH "repos/$repo/git/refs/heads/$branch" -f sha="$base_sha" -F force=true >/dev/null

  path=".github/workflows/ai-review.yml"
  existing="$(gh api "repos/$repo/contents/$path?ref=$branch" --jq .sha 2>/dev/null || true)"
  args=(-X PUT "repos/$repo/contents/$path" -f branch="$branch"
        -f message="ci: revisión automática de PRs con DeepSeek"
        -f content="$(printf '%s' "$content" | base64 | tr -d '\n')")
  [ -n "$existing" ] && args+=(-f sha="$existing")
  gh api "${args[@]}" >/dev/null

  gh pr create -R "$repo" --head "$branch" --base "$default" \
    --title "ci: revisión automática de PRs con DeepSeek" \
    --body "Agrega el workflow de revisión automática (gon0801/goncloud-pr-review). Requiere el secret \`DEEPSEEK_API_KEY\` en este repo." \
    2>/dev/null || echo "$repo: el PR ya existía, rama actualizada"
done
