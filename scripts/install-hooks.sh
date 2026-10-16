#!/usr/bin/env bash
# Instala el pre-push de .githooks SOLO para ESTE checkout (worktree):
# usa core.hooksPath por worktree (extensions.worktreeConfig), asi no toca
# los ganchos del checkout principal ni de otros worktrees.
set -euo pipefail
cd "$(dirname "$0")/.."
git config extensions.worktreeConfig true
git config --worktree core.hooksPath "$PWD/.githooks"
echo "install-hooks: core.hooksPath(worktree) = $(git config --worktree core.hooksPath)"
