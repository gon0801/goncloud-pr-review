#!/usr/bin/env bash
# Abre un PR que instala el conjunto de revisión en cada repo dado.
# Uso: scripts/install.sh [--coordinado] owner/repo [owner/repo ...]
#   --coordinado  instala el coordinador y el worker fijados al SHA confiable
#                 (ACTION_SHA, requerido) y retira el escritor anterior en un
#                 único commit atómico por la API Git de árboles y commits.
#   ACTION_SHA    SHA confiable del repo central. En el modo actual fija
#                 `uses:` (si no, queda @main); en el coordinado es obligatorio.
#   EXCLUDE       exclusiones extra por repo (solo modo actual):
#                 EXCLUDE=$'data/**\nout/**' scripts/install.sh owner/repo
set -euo pipefail

here="$(cd "$(dirname "$0")/.." && pwd)"
branch="chore/ai-review"
modo="actual"
if [ "${1:-}" = "--coordinado" ]; then
  modo="coordinado"
  shift
fi
if [ "$modo" = "coordinado" ] && [ -z "${ACTION_SHA:-}" ]; then
  echo "instalador: el modo coordinado requiere ACTION_SHA (SHA candidato del piloto)" >&2
  exit 2
fi

for repo in "$@"; do
  if [ "$modo" = "coordinado" ]; then
    REPO="$repo" BRANCH="$branch" ACTION_SHA="$ACTION_SHA" HERE="$here" python3 <<'PY'
import base64
import json
import os
import pathlib
import subprocess
import sys

repo = os.environ["REPO"]
rama = os.environ["BRANCH"]
action_sha = os.environ["ACTION_SHA"]
here = os.environ["HERE"]
central = "gon0801/goncloud-pr-review"
INTENTOS = 3


def api(*args, entrada=None):
    cmd = ["gh", "api", *args]
    if entrada is not None:
        cmd += ["--input", "-"]
    return subprocess.run(cmd, input=entrada, capture_output=True, text=True)


def consulta(*args, expr):
    resultado = api(*args, "--jq", expr)
    if resultado.returncode != 0:
        return None
    return resultado.stdout.strip()


def plantilla(nombre):
    texto = (pathlib.Path(here) / "templates" / nombre).read_text()
    ancla = "        with:\n          persist-credentials: false"
    paso_central = (
        "      - name: checkout del CLI confiable del repo central\n"
        "        uses: actions/checkout@v4\n"
        "        with:\n"
        f"          repository: {central}\n"
        f"          ref: {action_sha}\n"
        "          path: ai-review-code\n"
        "          persist-credentials: false\n"
    )
    if ancla not in texto:
        sys.exit(f"instalador: no encontré el checkout confiable en {nombre}")
    return texto.replace(
        ancla, ancla + "\n" + paso_central.rstrip("\n"), 1
    ).replace(
        '"$GITHUB_WORKSPACE/review.py"',
        '"$GITHUB_WORKSPACE/ai-review-code/review.py"',
    ).replace(
        "python review.py reconcile",
        f'python "$GITHUB_WORKSPACE/ai-review-code/review.py" reconcile',
    )


def fail(motivo):
    print(f"instalador: {repo}: {motivo}", file=sys.stderr)
    sys.exit(1)


default = consulta(f"repos/{repo}", expr=".default_branch")
if not default:
    fail("no pude leer el repositorio")

punta = consulta(f"repos/{repo}/git/ref/heads/{rama}", expr=".object.sha")
if not punta:
    raiz = consulta(f"repos/{repo}/git/ref/heads/{default}", expr=".object.sha")
    creado = api(f"repos/{repo}/git/refs", "-f", f"ref=refs/heads/{rama}", "-f", f"sha={raiz}")
    if creado.returncode != 0:
        fail(f"no pude crear la rama ({creado.stderr.strip()})")
    punta = raiz

contenido_publicador = plantilla("ai-review-publish.yml")
contenido_worker = plantilla("ai-review-worker.yml")

for intento in range(1, INTENTOS + 1):
    arbol_base = consulta(f"repos/{repo}/git/commits/{punta}", expr=".tree.sha")
    if not arbol_base:
        fail("no pude leer el árbol de la punta; no se publica nada")
    blobs = []
    for contenido in (contenido_publicador, contenido_worker):
        hecho = api(
            f"repos/{repo}/git/blobs",
            "-f",
            f"content={base64.b64encode(contenido.encode()).decode()}",
            "-f",
            "encoding=base64",
        )
        if hecho.returncode != 0:
            fail(f"no pude crear el blob ({hecho.stderr.strip()})")
        blobs.append(json.loads(hecho.stdout)["sha"])
    cuerpo = json.dumps(
        {
            "base_tree": arbol_base,
            "tree": [
                {
                    "path": ".github/workflows/ai-review-publish.yml",
                    "mode": "100644",
                    "type": "blob",
                    "sha": blobs[0],
                },
                {
                    "path": ".github/workflows/ai-review-worker.yml",
                    "mode": "100644",
                    "type": "blob",
                    "sha": blobs[1],
                },
                {
                    "path": ".github/workflows/ai-review.yml",
                    "mode": "100644",
                    "type": "blob",
                    "sha": None,
                },
            ],
        }
    )
    arbol = api(f"repos/{repo}/git/trees", entrada=cuerpo)
    if arbol.returncode != 0:
        fail(f"no pude crear el árbol ({arbol.stderr.strip()})")
    arbol_sha = json.loads(arbol.stdout)["sha"]
    mensaje = (
        "ci: revisión coordinada de PRs con IA (coordinador + worker)\n\n"
        f"Instala ai-review-publish.yml y ai-review-worker.yml fijados al SHA\n"
        f"candidato {action_sha} de {central} y retira ai-review.yml.\n"
        "Requiere los secrets API_KEY y FALLBACK_API_KEY en este repo."
    )
    hecho = api(
        f"repos/{repo}/git/commits",
        "-f",
        f"message={mensaje}",
        "-F",
        f"tree={arbol_sha}",
        "-F",
        f"parents[]={punta}",
    )
    if hecho.returncode != 0:
        fail(f"no pude crear el commit ({hecho.stderr.strip()})")
    commit_sha = json.loads(hecho.stdout)["sha"]
    movida = api(
        "-X",
        "PATCH",
        f"repos/{repo}/git/refs/heads/{rama}",
        "-f",
        f"sha={commit_sha}",
    )
    if movida.returncode == 0:
        punta = commit_sha
        break
    if intento == INTENTOS:
        fail("la rama cambió durante la instalación y no se puede forzar")
    print(
        f"instalador: {repo}: la rama cambió durante la instalación; "
        "reintento sobre la punta nueva (sin forzar)",
        file=sys.stderr,
    )
    punta = consulta(f"repos/{repo}/git/ref/heads/{rama}", expr=".object.sha")
    if not punta:
        fail("no pude releer la punta para el reintento")

titulo = "ci: revisión coordinada de PRs con IA (coordinador + worker)"
cuerpo_pr = (
    f"Instala el conjunto coordinado (publicador + worker) fijado al SHA\n"
    f"candidato `{action_sha}` de {central} y retira el escritor anterior\n"
    "ai-review.yml en un único commit.\n\n"
    "Requiere el secret `AI_REVIEW_API_KEY` en este repo.\n"
    "Corte: detener admisión, drenar ejecuciones antiguas, verificar el\n"
    "checkpoint y solo entonces fusionar (docs/reviewer-rollout.md del repo central)."
)
abiertos = subprocess.run(
    [
        "gh",
        "pr",
        "list",
        "-R",
        repo,
        "--head",
        rama,
        "--state",
        "open",
        "--json",
        "number",
        "--jq",
        ".[].number",
    ],
    capture_output=True,
    text=True,
)
if abiertos.stdout.strip():
    print(f"{repo}: el PR ya existía, rama actualizada")
else:
    creado = subprocess.run(
        [
            "gh",
            "pr",
            "create",
            "-R",
            repo,
            "--head",
            rama,
            "--base",
            default,
            "--title",
            titulo,
            "--body",
            cuerpo_pr,
        ],
        capture_output=True,
        text=True,
    )
    if creado.returncode != 0:
        fail(f"no pude abrir el PR ({creado.stderr.strip()})")
    print(f"{repo}: PR del conjunto coordinado abierto")
PY
  else
    content="$(cat "$here/templates/ai-review.yml")"
    if [ -n "${ACTION_SHA:-}" ]; then
      content="${content//gon0801\/goncloud-pr-review@main/gon0801\/goncloud-pr-review@$ACTION_SHA}"
    fi
    if [ -n "${EXCLUDE:-}" ]; then
      content+=$'\n          exclude: |'
      while IFS= read -r line; do
        [ -n "$line" ] && content+=$'\n            '"$line"
      done <<< "$EXCLUDE"
    fi
    content+=$'\n'

    default="$(gh api "repos/$repo" --jq .default_branch)"
    punta="$(gh api "repos/$repo/git/ref/heads/$branch" --jq .object.sha 2>/dev/null || true)"
    if [ -z "$punta" ]; then
      punta="$(gh api "repos/$repo/git/ref/heads/$default" --jq .object.sha)"
      gh api "repos/$repo/git/refs" -f ref="refs/heads/$branch" -f sha="$punta" >/dev/null
    fi
    conjunto="$(gh api "repos/$repo/git/trees/$punta" --jq '.tree[].path' | grep -c 'workflows/ai-review-' || true)"

    if [ "${conjunto:-0}" -ge 2 ]; then
      # Retorno: un commit atómico repone ai-review.yml y retira coordinador
      # y worker, sobre la punta y sin forzar (misma disciplina del coordinado).
      for intento in 1 2 3; do
        arbol_base="$(gh api "repos/$repo/git/commits/$punta" --jq .tree.sha)"
        if [ -z "$arbol_base" ]; then
          echo "instalador: $repo: no pude leer el árbol; no se publica nada" >&2
          exit 1
        fi
        blob_b64="$(printf '%s' "$content" | base64 | tr -d '\n')"
        bsha="$(gh api "repos/$repo/git/blobs" -f content="$blob_b64" -f encoding=base64 --jq .sha)"
        cuerpo="$(printf '{"base_tree":"%s","tree":[{"path":".github/workflows/ai-review.yml","mode":"100644","type":"blob","sha":"%s"},{"path":".github/workflows/ai-review-publish.yml","mode":"100644","type":"blob","sha":null},{"path":".github/workflows/ai-review-worker.yml","mode":"100644","type":"blob","sha":null}]}' "$arbol_base" "$bsha")"
        arbol_sha="$(gh api "repos/$repo/git/trees" --input - <<< "$cuerpo" --jq .sha)"
        commit_sha="$(gh api "repos/$repo/git/commits" -f message="ci: retorno al escritor compatible de revisión con IA" -F "tree=$arbol_sha" -F "parents[]=$punta" --jq .sha)"
        if gh api -X PATCH "repos/$repo/git/refs/heads/$branch" -f sha="$commit_sha" >/dev/null 2>&1; then
          punta="$commit_sha"
          break
        fi
        if [ "$intento" = "3" ]; then
          echo "instalador: $repo: la rama cambió durante el retorno y no se puede forzar" >&2
          exit 1
        fi
        punta="$(gh api "repos/$repo/git/ref/heads/$branch" --jq .object.sha)"
      done
    else
      path=".github/workflows/ai-review.yml"
      existing="$(gh api "repos/$repo/contents/$path?ref=$branch" --jq .sha 2>/dev/null || true)"
      args=(-X PUT "repos/$repo/contents/$path" -f branch="$branch"
            -f message="ci: revisión automática de PRs con IA"
            -f content="$(printf '%s' "$content" | base64 | tr -d '\n')")
      [ -n "$existing" ] && args+=(-f sha="$existing")
      gh api "${args[@]}" >/dev/null
    fi

    if [ -n "$(gh pr list -R "$repo" --head "$branch" --state open --json number --jq '.[].number')" ]; then
      echo "$repo: el PR ya existía, rama actualizada"
    else
      gh pr create -R "$repo" --head "$branch" --base "$default" \
        --title "ci: revisión automática de PRs con IA" \
        --body "Agrega el workflow de revisión automática (gon0801/goncloud-pr-review). Requiere el secret \`AI_REVIEW_API_KEY\` en este repo."
    fi
  fi
done
