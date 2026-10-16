#!/usr/bin/env bash
# Instala los ganchos de .githooks en el gancho real de ESTE checkout
# (solo este worktree; no toca la config global ni otros worktrees).
set -euo pipefail
cd "$(dirname "$0")/.."
dest="$(git rev-parse --git-dir)/hooks"
mkdir -p "$dest"
for hook in .githooks/*; do
  nombre="$(basename "$hook")"
  ln -sf "$PWD/$hook" "$dest/$nombre"
  echo "install-hooks: $nombre -> $dest/$nombre"
done
