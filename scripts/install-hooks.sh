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
dest="$comun/hooks/pre-push"
tmp="$(mktemp)"
cat > "$tmp" <<'HOOK'
#!/usr/bin/env bash
set -euo pipefail
top="$(git rev-parse --show-toplevel)"
if [ -x "$top/scripts/check.sh" ]; then
  cd "$top" && bash scripts/check.sh
fi
HOOK
if [ -e "$dest" ] && ! cmp -s "$dest" "$tmp"; then
  respaldo="$dest.respaldo-$(date +%Y%m%d%H%M%S)"
  cp "$dest" "$respaldo"
  echo "install-hooks: respaldo del gancho previo en $respaldo"
fi
cp "$tmp" "$dest"
chmod +x "$dest"
rm -f "$tmp"
echo "install-hooks: $dest (condicional a scripts/check.sh)"
