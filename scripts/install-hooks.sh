#!/usr/bin/env bash
# Instala el pre-push en los ganchos COMUNES del clon (los worktrees
# enlazados no miran su propio hooks dir). El gancho es condicional:
# solo corre donde existe scripts/check.sh; en los demas checkouts
# sale 0 sin hacer nada.
set -euo pipefail
cd "$(dirname "$0")/.."
comun="$(git rev-parse --absolute-git-dir)"
case "$comun" in
  */worktrees/*) comun="$(git rev-parse --path-format=absolute --git-common-dir)" ;;
esac
cat > "$comun/hooks/pre-push" <<'HOOK'
#!/usr/bin/env bash
set -euo pipefail
top="$(git rev-parse --show-toplevel)"
if [ -x "$top/scripts/check.sh" ]; then
  cd "$top" && bash scripts/check.sh
fi
HOOK
chmod +x "$comun/hooks/pre-push"
echo "install-hooks: $comun/hooks/pre-push (condicional a scripts/check.sh)"
