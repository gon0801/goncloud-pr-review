"""Comandos externos y E/S con GitHub y Actions: correr gh y git, outputs y anotaciones del job, comentarios del PR y permisos de colaborador."""

import json
import os
import subprocess
import sys

from review_domain import find_model_findings_block


def collaborator_permission(repo, user):
    """Push access of a comment author, or None. A failed query is logged to
    stderr (never silent: without it B4 would die quietly); no-write is not."""
    try:
        proc = sh(
            "gh",
            "api",
            f"repos/{repo}/collaborators/{user}/permission",
            "--jq",
            ".permission",
            check=False,
        )
    except OSError as exc:
        print(
            f"ai-review: no se pudo verificar el permiso de {user} ({exc}); "
            f"su descarte se ignora",
            file=sys.stderr,
        )
        return None
    if proc.returncode != 0 and "HTTP 404" in (proc.stderr or ""):
        # A login that no longer exists: a definitive "no", not a failure to retry.
        print(
            f"ai-review: {user} no existe para este repo (HTTP 404); su descarte se ignora",
            file=sys.stderr,
        )
        return "none"
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()
        detail = f": {tail[-1][:200]}" if tail else ""
        print(
            f"ai-review: no se pudo verificar el permiso de {user} "
            f"(gh api salió {proc.returncode}{detail}); su descarte se ignora",
            file=sys.stderr,
        )
        return None
    return proc.stdout.strip() or None


def sh(*args, check=True, **kw):
    return subprocess.run(args, check=check, text=True, capture_output=True, **kw)


def set_output(key, value):
    with open(os.environ["GITHUB_OUTPUT"], "a") as fh:
        fh.write(f"{key}={value}\n")


def avisar_bloque_sin_cierre(texto):
    """El bloque del modelo se recuperó sin su ` -->`: se publica, pero queda a la vista."""
    found = find_model_findings_block(texto)
    if found is not None and not found[3]:
        print(
            "::warning::ai-review: el bloque de hallazgos del modelo no traía ` -->` justo "
            "tras el JSON; se recuperó del JSON completo"
        )


def informar_revision(revisado):
    """reviewed=true|false para el workflow: un fail-soft termina en verde y no
    debe leerse como una revisión del SHA."""
    if os.environ.get("GITHUB_OUTPUT"):
        set_output("reviewed", "true" if revisado else "false")


def fetch_all_comments(repo, pr):
    out = sh(
        "gh",
        "api",
        "--paginate",
        f"repos/{repo}/issues/{pr}/comments?per_page=100",
        "--jq",
        ".[] | {id: .id, user: .user.login, body: .body, created_at: .created_at, updated_at: .updated_at} | tojson",
    ).stdout
    return [json.loads(line) for line in out.splitlines() if line.strip()]
