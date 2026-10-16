#!/usr/bin/env bash
# Piso determinista: un solo comando corre los mismos candados en la
# máquina, en el pre-push y en CI. Falla cerrado: cualquier paso rojo
# frena el push. Uso: bash scripts/check.sh [--no-precommit]
set -euo pipefail

NO_PRECOMMIT=0
for arg in "$@"; do
  case "$arg" in
    --no-precommit) NO_PRECOMMIT=1 ;;
    *) echo "check: argumento desconocido: $arg" >&2; exit 2 ;;
  esac
done

cd "$(dirname "$0")/.."

# Sin entorno git heredado: bajo un gancho git exporta GIT_DIR, y el
# hospedador de agentes a veces filtra GIT_* propias (techo de
# descubrimiento, dir explicito). Con cualquiera de ellas, los `git`
# de las pruebas en repos temporales mueren (128) y la bateria
# miente en verde o en rojo segun el turno. Se barre todo GIT_*:
# check.sh siempre trabaja sobre su propio arbol por descubrimiento.
while IFS= read -r linea; do unset "${linea%%=*}"; done < <(env | grep ^GIT_ || true)

# En macOS las herramientas viven en brew, fuera del PATH mínimo.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

# Piso de Python: review_domain.py usa `X | None` (3.10+). El python del
# sistema en macOS es 3.9; se elige el primero que cumpla, determinista.
PY=""
for candidato in python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$candidato" >/dev/null 2>&1; then
    if "$candidato" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
      PY="$candidato"
      break
    fi
  fi
done
if [ -z "$PY" ]; then
  echo "check: necesito python >= 3.10 (hay $(python3 --version 2>&1)); en macOS: brew install python" >&2
  exit 2
fi
echo "check: python=$PY ($("$PY" --version 2>&1))"

if [ "$NO_PRECOMMIT" -eq 0 ]; then
  if ! command -v pre-commit >/dev/null 2>&1; then
    echo "check: pre-commit no instalado (candados quality-kit); instalalo con: pip install pre-commit" >&2
    exit 2
  fi
  echo "check: pre-commit run --all-files"
  pre-commit run --all-files
else
  echo "check: pre-commit omitido (--no-precommit); solo CI puede pagarlo, nunca un push local"
fi

if command -v actionlint >/dev/null 2>&1; then
  echo "check: actionlint"
  actionlint -color
  actionlint -color templates/ai-review-publish.yml templates/ai-review-worker.yml
else
  echo "check: actionlint no instalado; los workflows los cubre CI (job workflows)"
fi

echo "check: particion determinista (union == bateria, sin duplicados)"
"$PY" scripts/run_test_shard.py --verify-partition 2

echo "check: bateria completa"
"$PY" -m unittest discover -s tests

echo "check: OK"
