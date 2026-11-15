"""Comandos externos y E/S con GitHub y Actions: correr gh y git, outputs y anotaciones del job, comentarios del PR y permisos de colaborador."""

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass

import review_document as document_codec
import review_domain as domain
from review_domain import find_model_findings_block
from review_sticky import MARKER


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


@dataclass(frozen=True)
class PublishReceipt:
    generacion: int
    recibo: dict
    trabajo: tuple


@dataclass(frozen=True)
class Unconfirmed:
    motivo: str


def _bot_comments(adapter, login):
    comments = []
    for comment in adapter.leer():
        actor = comment.get("user")
        if isinstance(actor, dict):
            actor = actor.get("login")
        if MARKER in (comment.get("body") or "") and (login is None or actor == login):
            comments.append(comment)
    return comments


def publish_checkpoint(prepared, observed, adapter, login=None, work_after_commit=()):
    document = document_codec.read_document(prepared.body)
    if (
        not isinstance(document, document_codec.ReviewDocument)
        or hashlib.sha256(prepared.body.encode("utf-8")).hexdigest() != prepared.body_digest
        or domain.read_snapshot(prepared.body).block != prepared.state_block
    ):
        return Unconfirmed("publicación inválida")
    work = tuple(work_after_commit)
    live = {request.id: request for request in document.memory.pending_requests if request.viva}
    if any(live.get(request.id) != request for request in work):
        return Unconfirmed("la publicación no contiene el trabajo solicitado")
    found = _bot_comments(adapter, login)
    if len(found) > 1:
        return Unconfirmed("múltiples comentarios del bot")
    current = found[0] if found else None
    if (observed is None) != (current is None) or (
        observed and (
            observed.get("id"), observed.get("body")
        ) != (current.get("id"), current.get("body"))
    ):
        return Unconfirmed("el comentario cambió desde la lectura")
    if current and isinstance(document_codec.read_document(current.get("body") or ""), document_codec.DocumentBlocked):
        return Unconfirmed("documento actual inválido o futuro")
    if current and current.get("body") == prepared.body:
        return PublishReceipt(
            prepared.generation,
            {"comentario_id": current["id"], "ya_aplicado": True},
            work,
        )
    if current is None and not (
        document.review or document.historical or document.explanations
        or document.notices or document.failure or
        any(
            request.kind == "review" and request.state == domain.PENDIENTE
            for request in document.memory.pending_requests
        )
    ):
        return PublishReceipt(0, {"sin_texto_visible": True}, ())
    try:
        if current:
            adapter.parchar(current["id"], prepared.body)
        else:
            adapter.crear(prepared.body)
    except (OSError, subprocess.SubprocessError) as exc:
        return Unconfirmed(f"respuesta de escritura incierta: {exc}")
    fresh = _bot_comments(adapter, login)
    if len(fresh) != 1 or fresh[0].get("body") != prepared.body:
        return Unconfirmed("la publicación no se confirmó por relectura")
    return PublishReceipt(
        prepared.generation, {"comentario_id": fresh[0]["id"]}, work,
    )


def publish_capacity_notice(observed, adapter, login=None, secrets=()):
    reason = "desborde de memoria: se conserva el checkpoint previo"
    if observed is None:
        return Unconfirmed(reason)
    document = document_codec.read_document(observed.get("body") or "")
    if isinstance(document, document_codec.DocumentBlocked):
        return Unconfirmed(f"{reason}; comentario previo inválido")
    updated = document_codec.update_presentation(
        document, document.memory, notices=(document_codec.Notice("capacity-exceeded"),),
    )
    prepared = document_codec.prepare_publication(updated, domain.StorageBudget(), secrets)
    if not isinstance(prepared, document_codec.PreparedPublication):
        return Unconfirmed(f"{reason}; el aviso no cabe")
    receipt = publish_checkpoint(prepared, observed, adapter, login=login)
    suffix = "aviso publicado" if isinstance(receipt, PublishReceipt) else "aviso no confirmado"
    return Unconfirmed(f"{reason}; {suffix}")
