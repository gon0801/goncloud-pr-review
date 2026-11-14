#!/usr/bin/env python3
"""Glue for the AI PR review action."""

import argparse
from dataclasses import dataclass, replace
import html
import json
import os
import re
import shutil
import subprocess
import tempfile
import sys
from pathlib import Path

from review_commands import dismiss_targets
from review_config import (
    ERROR_KEY,
    IDENTIDADES,
    PROVIDERS,
    env,
    get_provider,
    politica_de_identidad,
    politica_de_revision,
)
import review_domain
import review_github
import review_run
from review_render import (
    ALCANCE_INICIO,
    DETALLE_DEL_REVISOR,
    GITHUB_COMMENT_MAX,
    VERDICT_RE,
    aviso_md,
    caution_banner,
    compose,
    cuerpo_publicable,
    insert_caution_banner,
    neutralizar_marcadores,
    prosa_del_modelo,
    redact,
    sections_for,
    strip_model_verdict,
    verdict_for,
)
from review_sticky import (
    COMPLETION_PREFIX,
    COVERED_PREFIX,
    MARKER,
    SHA_PREFIX,
    cobertura_declarada,
    continuacion_terminal,
    libro_de,
    libro_fusionado,
    modelo_utilizable,
    prev_de_memoria,
    recortes_de_continuacion,
    reviewed_sha,
    sha_hexa,
    sticky_from_comments,
)
from review_domain import (
    DISMISSED,
    FINDING_FILES_MAX,
    FINDING_ID_DIGITS,
    FINDING_ID_RE,
    FINDINGS_MAX_COUNT,
    FINDINGS_PREFIX,
    FINDINGS_SUFFIX,
    FINDINGS_TITLE_MAX,
    OPEN,
    RESOLVED,
    SEVERITIES,
    derive_next,
    find_findings_block,
    find_model_findings_block,
    finding_number,
    normalize_severity,
    one_line,
    parse_model_findings,
    same_issue,
    sanitize_finding,
    split_coverage,
    strip_findings_block,
    strip_model_findings_block,
    unique_paths,
)
from review_context import (
    CALLERS_MAX_BYTES,
    CALLERS_MAX_MATCHES,
    CONVENTIONS_MAX_BYTES,
    DEFAULT_EXCLUDES,
    DEFAULT_MAX_TURNS,
    GitRepository,
    INCREMENTAL_MAX_TURNS,
    INCREMENTAL_SMALL_BYTES,
    INCREMENTAL_SMALL_FILES,
    LARGE_DIFF_MAX_TURNS,
    SearchComplete,
    SearchFailed,
    SearchTruncated,
    TESTS_MAX_BYTES,
    TESTS_MAX_RESULTS,
    build_callers,
    build_conventions,
    build_tests,
    blobs_de_head,
    changed_since,
    decide_mode,
    delta_real,
    digest_de_politica,
    excluded_by,
    files_matching_base,
    files_reverted,
    grep_files,
    is_ancestor,
    looks_like_test,
    max_turns_for_diff,
    prepare_review,
    prev_findings_markdown,
    priority,
    resolve_max_turns,
    trim_utf8,
)

# Superficie de compatibilidad: los tests y el adaptador consumen estos nombres
# via review.* aunque el cuerpo de review.py ya no los use directamente.
__all__ = [
    "CALLERS_MAX_BYTES",
    "CALLERS_MAX_MATCHES",
    "CONVENTIONS_MAX_BYTES",
    "DEFAULT_EXCLUDES",
    "DEFAULT_MAX_TURNS",
    "DISMISSED",
    "FINDING_FILES_MAX",
    "FINDING_ID_RE",
    "FINDINGS_MAX_BYTES",
    "FINDINGS_MAX_COUNT",
    "FINDINGS_PREFIX",
    "FINDINGS_SUFFIX",
    "FINDINGS_TITLE_MAX",
    "INCREMENTAL_MAX_TURNS",
    "INCREMENTAL_SMALL_BYTES",
    "INCREMENTAL_SMALL_FILES",
    "LARGE_DIFF_MAX_TURNS",
    "OPEN",
    "RESOLVED",
    "SEVERITIES",
    "SearchComplete",
    "SearchFailed",
    "SearchTruncated",
    "TESTS_MAX_BYTES",
    "TESTS_MAX_RESULTS",
    "decide_mode",
    "derive_next",
    "digest_de_politica",
    "excluded_by",
    "files_matching_base",
    "find_findings_block",
    "find_model_findings_block",
    "finding_number",
    "grep_files",
    "is_ancestor",
    "looks_like_test",
    "max_turns_for_diff",
    "normalize_severity",
    "one_line",
    "parse_findings_block",
    "parse_model_findings",
    "prev_findings_markdown",
    "priority",
    "resolve_max_turns",
    "same_issue",
    "sanitize_finding",
    "serialize_findings",
    "strip_findings_block",
    "strip_model_findings_block",
    "trim_utf8",
    "unique_paths",
]

FINDINGS_MAX_BYTES = 8000

# Findings memory (PR B). The model emits one hidden block BEFORE the COVERAGE line
# (never after: split_coverage drops everything behind it, and the 50k/65k trims cut
# from the end). Publish re-emits the canonical block right after the SHA marker, so
# tail trims can never eat it; the review text budget reserves its bytes.
# The constants and the state functions live in review_domain; re-exported above.

# Dismiss commands (B4): `ai-review: descartar F3` / `ai-review: descartar todo`.
# Only applied when the comment author has push access, checked with the same token
# that reads/writes the review (inputs.github_token via GH_TOKEN) against:
#   GET /repos/{repo}/collaborators/{username}/permission
# That endpoint only needs Metadata:read, which every GITHUB_TOKEN carries
# implicitly (`metadata` is not even a settable `permissions:` key), so the
# minimal template token (contents:read + pull-requests:write) is enough.
DISMISS_RE = re.compile(r"ai-review:\s*descartar\s+(.+)", re.IGNORECASE)
WRITE_PERMISSIONS = frozenset({"admin", "maintain", "write"})
CLAUDE_CODE_VERSION = "2.1.282"
LITELLM_VERSION = "1.102.1"


def libro_actualizado(sticky, manifest, result, repo, model_ok):
    previo = libro_de((sticky or {}).get("body") or "")[1] or {}
    return libro_fusionado(previo, manifest, result, repo, model_ok)


def parse_findings_block(text, *, last=False):
    """Estado legado crudo, o None (sin bloque, roto o de otra versión).

    La lectura completa con migración (Valid/Legacy/Missing/Invalid/Future)
    vive en review_domain.read_snapshot.
    """
    load = review_domain.read_snapshot(text, last=last)
    return load.raw if isinstance(load, review_domain.Legacy) else None


def serialize_findings(state):
    """Bloque canónico dentro del presupuesto del adaptador (parcheable)."""
    return review_domain.serialize_findings(state, max_bytes=FINDINGS_MAX_BYTES)


def merge_findings(
    prev, model, *, changed_files, reverted_files, dismiss_ids, dismiss_all
):
    """Join previous state with what the model reported. Pure; git stays outside.

    - Ids from prev are stable, but only for the same primary file: a prev id the
      model reuses for another file is a different finding and gets a new id.
    - A finding flips to resolved only if one of its files (where the problem is or
      where the fix lands) changed since the last review (B3 lock).
    - A prev finding whose primary file changed in this push and is back to base
      content auto-resolves (the change that caused it was reverted). New findings
      never auto-resolve and never start resolved.
    - Prev findings the model dropped stay with their old data, never vanish.
    - Dismissed always wins, is sticky, and the model can't bring it back as new.
    Returns (merged_state, new_ids).
    """
    prev_list = (prev or {}).get("findings", []) if prev else []
    prev_by_id = {f["id"]: f for f in prev_list if f.get("id")}
    changed = set(changed_files or ())
    reverted = set(reverted_files or ())
    dismissed = set(dismiss_ids or ())
    if dismiss_all:
        dismissed |= {f["id"] for f in prev_list if f["state"] == OPEN and f.get("id")}
    gone = [f for f in prev_list if f["state"] == DISMISSED or f.get("id") in dismissed]

    merged, new_ids, seen = [], [], set()
    counter = (prev or {}).get("next") or derive_next(prev_list)
    for entry in (model or {}).get("findings", []) if model else []:
        fid = entry.get("id")
        old = prev_by_id.get(fid) if fid else None
        if old is None or old["file"] != entry["file"]:
            # The model repeated a previous issue without its id (e.g. as F-new on a full review).
            old = next(
                (
                    f
                    for f in prev_list
                    if f.get("id")
                    and f["id"] not in seen
                    and f["state"] != DISMISSED
                    and same_issue(entry, f)
                ),
                None,
            )
            fid = old["id"] if old else fid
        if old is not None and old["file"] == entry["file"]:
            if fid in seen:
                continue  # first wins: the model repeated a previous id
            seen.add(fid)
            if old["state"] == DISMISSED:
                merged.append(dict(old))
                continue
            others = [
                path
                for path in old.get("files", [old["file"]]) + entry.get("files", [])
                if path != old["file"]
            ]
            files = unique_paths(
                [old["file"]]
                + [path for path in others if path in changed]
                + [path for path in others if path not in changed]
            )
            state = entry["state"]
            # The lock guards the open -> resolved flip: one of the finding's files (registered
            # before, or named now as where the fix landed) must have changed in this pass. Changed
            # files are stored first, so the cap never drops the one that unlocks it.
            if (
                state == RESOLVED
                and old["state"] != RESOLVED
                and not changed.intersection(files)
            ):
                state = OPEN
            if old["file"] in reverted:
                state = RESOLVED
            merged.append(
                {
                    "id": fid,
                    "file": entry["file"],
                    "files": files,
                    "line": entry["line"],
                    "severity": entry["severity"],
                    "title": entry["title"],
                    "state": state,
                }
            )
            continue
        if entry["state"] == RESOLVED or any(same_issue(entry, g) for g in gone):
            continue  # a brand-new finding can't already be fixed, nor revive a dismissed one
        fid = f"F{counter}"
        counter += 1
        new_ids.append(fid)
        merged.append(
            {
                "id": fid,
                "file": entry["file"],
                "files": entry.get("files", [entry["file"]]),
                "line": entry["line"],
                "severity": entry["severity"],
                "title": entry["title"],
                "state": OPEN,
            }
        )
    for old in prev_list:
        if old.get("id") in seen or not old.get("id"):
            continue
        if old["state"] in (DISMISSED, RESOLVED):
            merged.append(dict(old))
        elif old["file"] in reverted:
            merged.append(dict(old, state=RESOLVED))
        else:
            merged.append(dict(old, state=OPEN))
    for finding in merged:
        if finding["id"] in dismissed and finding["id"] in prev_by_id:
            finding["state"] = DISMISSED
    merged.sort(key=lambda f: finding_number(f["id"]) or 0)
    counter = max(counter, derive_next(merged))
    return {"findings": merged, "next": counter}, new_ids


def apply_dismissals(state, dismiss_ids, dismiss_all):
    """Mark prev findings dismissed before the review, so the model is told about them."""
    if not state:
        return state
    findings = []
    for finding in state["findings"]:
        wanted = finding["id"] in dismiss_ids or (
            dismiss_all and finding["state"] == OPEN
        )
        findings.append(
            dict(finding, state=DISMISSED)
            if wanted and finding.get("id")
            else dict(finding)
        )
    return dict(state, findings=findings)


def parse_comando_reconcile(body):
    """Dict con acción y argumento del comentario-comando ai-review:, o None."""
    texto = (body or "").strip()
    m = re.match(
        rf"ai-review:\s*explicar\s+(F{FINDING_ID_DIGITS})\b", texto, re.IGNORECASE
    )
    if m:
        return {"action": "explicar", "arg": f"F{int(m.group(1)[1:])}"}
    if re.fullmatch(r"ai-review:\s*revisar\s*", texto, re.IGNORECASE):
        return {"action": "revisar", "arg": ""}
    m = re.match(r"ai-review:\s*descartar\s+(.+)", texto, re.IGNORECASE)
    if m:
        ids, todo = dismiss_targets(m.group(1))
        if todo:
            return {"action": "descartar", "arg": "todo"}
        if len(ids) == 1:
            return {"action": "descartar", "arg": next(iter(ids))}
    return None


def parse_dismiss_command(body):
    """Ids (normalized) and whether `todo` was asked in one comment body."""
    ids, all_open = set(), False
    for match in DISMISS_RE.finditer(body or ""):
        found, todo = dismiss_targets(match.group(1))
        ids |= found
        all_open = all_open or todo
    return ids, all_open


def collect_dismissals(repo, pr, bot_login, comments, after=0):
    """Dismiss commands from writer+ commenters posted after comment id `after`.

    Each command applies once, to the findings that existed when it was processed:
    the state remembers the highest comment id handled, so an old "descartar todo"
    never dismisses findings reported later. Returns (ids, all_open, last_id).
    """
    ids, all_open, checked, last = set(), False, {}, after
    for comment in comments or []:
        user = comment.get("user")
        cid = comment.get("id") if isinstance(comment.get("id"), int) else 0
        if not user or user == bot_login or cid <= after:
            continue
        found, wants_all = parse_dismiss_command(comment.get("body"))
        if not found and not wants_all:
            continue
        if user not in checked:
            checked[user] = review_github.collaborator_permission(repo, user)
        if checked[user] is None:
            break  # permission unknown (API failure): this and later commands wait for the next push
        last = max(last, cid)
        if checked[user] in WRITE_PERMISSIONS:
            ids |= found
            all_open = all_open or wants_all
    return ids, all_open, last


def shb(*args, check=True, **kw):
    return subprocess.run(args, check=check, capture_output=True, **kw)


def _repo_actual():
    return GitRepository(Path(review_github.sh("git", "rev-parse", "--show-toplevel").stdout.strip()))


def _snapshot_previo(prev):
    """El snapshot v2/v3 auténtico del sticky cuando prev.json lo transporta:
    revisión (base, head, política), hallazgos y generación tal como quedaron
    persistidos. None para memorias legadas sin bloque versionado."""
    bloque = prev.get("snapshot") or ""
    if not bloque:
        return None
    load = review_domain.read_snapshot(bloque)
    return load.snapshot if isinstance(load, review_domain.Valid) else None


def cmd_gate(args):
    repo, pr, head = env("REPO"), env("PR_NUMBER"), env("HEAD_SHA")
    login = os.environ.get("BOT_LOGIN") or "github-actions[bot]"
    comments = review_github.fetch_all_comments(repo, pr)
    sticky = sticky_from_comments(comments, login)
    rerun = int(os.environ.get("RUN_ATTEMPT", "1")) > 1
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    prev = prev_de_memoria(sticky["body"] if sticky else None)
    if prev["state"]:
        try:
            dismiss_ids, dismiss_all, _ = collect_dismissals(
                repo, pr, login, comments, prev["state"].get("seen", 0)
            )
            prev["state"] = apply_dismissals(prev["state"], dismiss_ids, dismiss_all)
        except Exception as exc:
            print(
                f"ai-review: no se pudieron leer los descartes ({exc}); se aplican al publicar",
                file=sys.stderr,
            )
    (work / "prev.json").write_text(json.dumps(prev))
    if sticky and prev["sha"] == head and not rerun:
        print(
            f"ai-review: {head[:7]} ya tiene revisión (comentario {sticky['id']}); se omite."
        )
        review_github.set_output("skip", "true")
    else:
        review_github.set_output("skip", "false")


def cmd_prepare(args):
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    head = env("HEAD_SHA")
    base = env("BASE_SHA")
    merge_base = review_github.sh("git", "merge-base", base, head).stdout.strip()
    repo = GitRepository(Path.cwd())

    prev_file = work / "prev.json"
    prev = (
        json.loads(prev_file.read_text())
        if prev_file.exists()
        else {"sha": None, "state": None}
    )
    prev_sha = prev.get("sha")
    if prev_sha:
        have = (
            review_github.sh(
                "git", "cat-file", "-e", f"{prev_sha}^{{commit}}", check=False
            ).returncode
            == 0
        )
        if not have:
            review_github.sh("git", "fetch", "--no-tags", "--quiet", "origin", prev_sha, check=False)
    policy = replace(
        politica_de_revision(),
        diff_max_bytes=int(os.environ.get("MAX_DIFF_BYTES", "1500000")),
        strict_budget=False,
        exclude_patterns=tuple(
            p.strip()
            for p in os.environ.get("EXTRA_EXCLUDES", "").splitlines()
            if p.strip()
        ),
    )
    digest = digest_de_politica(policy)
    try:
        mode, reason = decide_mode(repo, prev_sha, head)
    except OSError:
        mode, reason = "full", "git-unavailable"
    if mode == "incremental" and not prev.get("state"):
        # A sticky from before findings memory existed: without the previous findings an
        # incremental pass would drop them, so review the whole PR once to rebuild state.
        mode, reason = "full", "no-state"
    if mode == "incremental" and not review_domain.is_complete(
        review_domain.parse_coverage(prev.get("completion"))
    ):
        if (
            prev.get("completion") == "partial"
            and prev.get("covered")
            and prev.get("state")
        ):
            mode, reason = "continuation", ""
        else:
            mode, reason = "full", "incomplete-prev"
    if (
        mode == "full"
        and reason == "same-sha"
        and prev.get("completion") == "partial"
        and prev.get("covered")
        and prev.get("state")
    ):
        # Re-run manual en el mismo head tras una parcial con libro: continuar
        # con lo pendiente en vez de repetir la revisión completa.
        mode, reason = "continuation", ""

    previo_snapshot = _snapshot_previo(prev)
    if previo_snapshot is not None:
        # El snapshot auténtico del sticky, con la revisión previa real (base,
        # head, política). La cobertura viaja por el marcador publicado: un
        # unknown del snapshot se publica como partial y con libro continúa.
        current = replace(
            previo_snapshot,
            completion=review_domain.parse_coverage(prev.get("completion")),
        )
    elif prev_sha and sha_hexa(prev_sha) and prev.get("state"):
        current = review_domain.snapshot_a_v3(
            review_domain.Snapshot(
                schema=2,
                generation=1,
                revision=review_domain.Revision(
                    base_sha=base,
                    head_sha=prev_sha,
                    policy_digest=digest,
                ),
                next_id=1,
                completion=review_domain.parse_coverage(prev.get("completion")),
                findings=[],
                command_cursor=0,
            )
        )
    else:
        current = review_domain.Snapshot(
            schema=3,
            generation=1,
            revision=None,
            next_id=1,
            completion=review_domain.UNKNOWN,
            findings=[],
            command_cursor=0,
        )
    if mode in ("incremental", "continuation") and current.revision is not None:
        if current.revision.policy_digest != digest:
            mode, reason = "full", "política: el digest previo difiere"
        elif current.revision.base_sha != base:
            reason = (
                "base: la base previa difiere de la solicitada; "
                "el alcance se acota por la cobertura válida"
            )
    changed = []
    if prev_sha and prev_sha != head:
        try:
            changed = changed_since(repo, prev_sha, head)
        except subprocess.CalledProcessError:
            changed = None
    request = review_domain.WorkRequest(
        id=1,
        kind="review",
        origin="push",
        basis_generation=current.generation if current.revision is not None else 1,
        state="pending",
        target=review_domain.ReviewTarget(
            repository=os.environ.get("REPO", ""),
            pr_number=int(os.environ.get("PR_NUMBER") or 0),
            head_sha=head,
            base_sha=base,
            merge_base_sha=merge_base,
            policy_digest=digest,
        ).json(),
        solicitante="",
    )
    preparacion = prepare_review(
        repo, request, current, policy, cobertura_previa=prev.get("covered")
    )

    reviewed = [path.text for path in preparacion.entregados]
    excluded = [{"path": om.ruta, "reason": om.causa} for om in preparacion.omisiones]
    if mode == "incremental":
        excluded = [
            {"path": ruta, "reason": "encoding"} for ruta in preparacion.sin_codificar
        ] + excluded
    parche = "".join(preparacion.partes[ruta] for ruta in reviewed).encode("utf-8")
    (work / "diff.patch").write_bytes(parche)
    manifest = {
        "base": merge_base,
        "requested_base": base,
        "head": head,
        "reviewed": reviewed,
        "excluded": excluded,
        "diff_bytes": len(parche),
        "over_budget_bytes": preparacion.over_budget_bytes,
        "mode": mode,
        "prev_sha": prev_sha,
        "changed_files": (
            None if changed is None else [path.label() for path in changed]
        ),
        "reason": reason,
        "has_prev_findings": bool((prev.get("state") or {}).get("findings")),
    }
    (work / "manifest.json").write_text(json.dumps(manifest, indent=2))
    (work / "prev_findings.md").write_text(prev_findings_markdown(prev.get("state")))

    (work / "callers.txt").write_text(build_callers(repo, reviewed, preparacion.partes))
    (work / "tests.txt").write_text(build_tests(repo, reviewed))
    (work / "conventions.md").write_text(build_conventions(repo, base))

    event = json.loads(Path(env("GITHUB_EVENT_PATH")).read_text())
    pr = event.get("pull_request")
    if pr is None:
        pr = _pr_via_api()
    (work / "pr.md").write_text(f"# {pr.get('title', '')}\n\n{pr.get('body') or ''}\n")

    rules = review_github.sh("git", "show", f"{base}:.github/ai-review.md", check=False)
    system = Path(args.prompt).read_text()
    if rules.returncode == 0 and rules.stdout.strip():
        system += (
            "\n\n## Repository-specific rules (from the base branch, trusted)\n\n"
            + rules.stdout
        )
    (work / "system.md").write_text(system)

    print(
        f"ai-review: {len(reviewed)} archivo(s) a revisar, {len(excluded)} excluido(s), "
        f"diff {len(parche):,} bytes ({mode}{f'/{reason}' if reason else ''})"
    )
    for e in excluded:
        print(f"  excluido: {e['path']} ({e['reason']})")


def _pr_via_api():
    repo, pr = os.environ.get("REPO", ""), os.environ.get("PR_NUMBER", "")
    if not repo or not pr:
        return {}
    try:
        respuesta = review_github.sh(
            "gh",
            "api",
            f"repos/{repo}/pulls/{pr}",
            "--jq",
            "{title: .title, body: .body}",
            check=False,
        )
        datos = json.loads(respuesta.stdout) if respuesta.returncode == 0 else {}
    except Exception:
        return {}
    return datos if isinstance(datos, dict) else {}


def litellm_venv():
    override = os.environ.get("LITELLM_VENV")
    if override:
        return Path(override)
    return Path.home() / ".cache" / "ai-review" / "litellm-venv"


def venv_stamp():
    return f"{LITELLM_VERSION} py{sys.version_info[0]}.{sys.version_info[1]}"


def venv_ready(venv):
    marker = venv / "ai-review-version.txt"
    if not marker.exists() or marker.read_text().strip() != venv_stamp():
        return False
    try:
        probe = subprocess.run(
            [str(venv / "bin" / "python"), "-c", "import litellm"],
            capture_output=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return probe.returncode == 0


def build_litellm_venv(venv):
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    subprocess.run(
        [
            str(venv / "bin/pip"),
            "install",
            "--quiet",
            "--disable-pip-version-check",
            f"litellm[proxy]=={LITELLM_VERSION}",
        ],
        check=True,
    )


def ensure_litellm_venv(venv, build=None):
    """Reuse a cached venv only if it was built for this litellm AND this Python, and imports."""
    if venv_ready(venv):
        print(
            f"ai-review: litellm {LITELLM_VERSION} ya instalado (caché); se omite pip"
        )
        return
    if venv.exists():
        print("ai-review: la caché de litellm no sirve con este Python; se reconstruye")
        shutil.rmtree(venv)
    (build or build_litellm_venv)(venv)
    (venv / "ai-review-version.txt").write_text(venv_stamp() + "\n")


def claude_prefix():
    """npm global prefix inside the cached dir, so a warm cache skips npm entirely."""
    override = os.environ.get("CLAUDE_PREFIX")
    if override:
        return Path(override)
    return Path.home() / ".cache" / "ai-review" / "npm-global"


def claude_version_ok(claude_bin):
    try:
        found = review_github.sh(str(claude_bin), "--version", check=False)
    except OSError:
        return False
    return found.returncode == 0 and CLAUDE_CODE_VERSION in (found.stdout or "")


def cmd_install(args):
    name, provider = get_provider()
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    stage = claude_prefix()
    try:
        prefix = claude_prefix()
        if claude_version_ok(prefix / "bin" / "claude"):
            print(
                f"ai-review: claude-code {CLAUDE_CODE_VERSION} ya instalado (caché); se omite npm"
            )
        else:
            subprocess.run(
                [
                    "npm",
                    "install",
                    "-g",
                    "--prefix",
                    str(prefix),
                    "--no-fund",
                    "--no-audit",
                    f"@anthropic-ai/claude-code@{CLAUDE_CODE_VERSION}",
                ],
                check=True,
            )
        with open(os.environ["GITHUB_PATH"], "a") as fh:
            fh.write(f"{prefix / 'bin'}\n")
        if provider["via_proxy"] or (
            name == "deepseek" and os.environ.get("FALLBACK_ENABLED") == "true"
        ):
            venv = litellm_venv()
            stage = venv
            try:
                ensure_litellm_venv(venv)
            except (subprocess.CalledProcessError, OSError) as exc:
                if provider["via_proxy"]:
                    raise
                shutil.rmtree(venv, ignore_errors=True)
                print(
                    f"::warning::ai-review: no se pudo instalar LiteLLM para el respaldo ({exc})"
                )
            else:
                with open(os.environ["GITHUB_PATH"], "a") as fh:
                    fh.write(f"{venv / 'bin'}\n")
    except (subprocess.CalledProcessError, OSError) as exc:
        # Drop only the half-built piece: actions/cache saves this dir after the job under a
        # key that is never rewritten, so a broken copy (or an empty dir) would be restored
        # on every later run. A good Claude Code prefix stays when only pip failed.
        shutil.rmtree(stage, ignore_errors=True)
        try:
            stage.parent.rmdir()  # only succeeds when nothing else is left in the cache root
        except OSError:
            pass
        reason = f"no se pudo instalar las herramientas de revisión ({exc})"
        (work / "install_error.txt").write_text(reason)
        print(f"::warning::ai-review: {reason}")
        return


# F0: identidad y evidencia. El dominio trae match_finding / accept_report /
# validar_reporte; el adaptador construye los RepositoryFacts con Git. Ambas
# identidades (current y anchors) pasan por la aceptación del dominio; la
# emisión de anclas por el modelo llega con la activación del CIERRE.


def hechos_de_repo(
    manifest, reverted_files, renames=(), blobs=None, policy_digest="", omissions=()
):
    """RepositoryFacts que el adaptador verifica con Git.

    `changed_paths` es el delta real entre revisiones (prev_sha..head con
    prev_sha hex40, en cualquier modo; base..head en el resto); sin SHAs
    hex40 o con delta no calculable cae al alcance del manifiesto y la
    omisión queda registrada para degradar la cobertura.
    """
    base, head = manifest.get("base"), manifest.get("head")
    omissions = tuple(omissions or ())
    desde = base
    if sha_hexa(manifest.get("prev_sha")):
        desde = manifest["prev_sha"]
    calculado = False
    if sha_hexa(desde) and sha_hexa(head):
        delta, omis_delta, calculado = delta_real(
            GitRepository(Path.cwd()), desde, head
        )
        omissions = omissions + omis_delta
        if not calculado:
            delta = tuple(review_domain.rutas_de_cambio(manifest))
    else:
        delta = tuple(review_domain.rutas_de_cambio(manifest))
        omissions = omissions + ("delta real no calculable",)
    return review_domain.RepositoryFacts(
        revision=review_domain.Revision(
            base_sha=base,
            head_sha=head,
            policy_digest=policy_digest,
        ),
        changed_paths=delta,
        delta_calculado=calculado,
        reverted_paths=tuple(reverted_files or ()),
        renames=tuple(renames or ()),
        blobs=dict(blobs or {}),
        omissions=omissions,
    )


def blobs_para_aceptar(snapshot, manifest, policy, rutas_delta=()):
    """Blobs de HEAD y de las anclas localizadas persistidas, para aceptar
    con identidad `anchors`. Las rutas del delta real van también: una cita
    sobre un archivo cambiado ausente del manifiesto no debe degradar a
    coincidencia por título."""
    if policy.finding_identity != review_domain.IDENTIDAD_ANCLAS:
        return {}
    rutas = sorted(
        set(manifest.get("reviewed", []) or [])
        | set(review_domain.rutas_de_cambio(manifest))
        | set(rutas_delta)
    )
    blobs = blobs_de_head(GitRepository(Path.cwd()), rutas)
    for ancla in anclas_locadas(snapshot):
        if (ancla.path, ancla.blob_sha) in blobs:
            continue
        try:
            contenido = review_github.sh("git", "cat-file", "-p", ancla.blob_sha, check=False).stdout
        except Exception:
            continue
        if contenido:
            blobs[(ancla.path, ancla.blob_sha)] = tuple(contenido.splitlines())
    return blobs


def anclas_locadas(snapshot):
    vistas = []
    for f in snapshot.findings:
        for ancla in [
            f.primary_anchor,
            *f.related_anchors,
            *[
                e.anchor
                for e in f.evidence
                if isinstance(e, review_domain.EvidenceSource)
            ],
        ]:
            if isinstance(ancla, review_domain.AnchorLocated):
                vistas.append(ancla)
    return vistas


def actualizar_memoria_valida(
    load, result, manifest, repo, pr, login, comments, policy=None
):
    """Escritor compatible: memoria v2/v3 válida, identidad de la política
    normalizada.

    Conserva el schema del estado, aplica los descartes de comentarios,
    avanza la revisión a la del manifiesto cuando head y base son hex40, e
    integra las observaciones del modelo por la aceptación del dominio con
    los hechos reales (delta, blobs, reversiones). Desborde: Keep.
    """
    policy = policy or politica_de_revision(manifest)
    snapshot = load.snapshot
    model = parse_model_findings(
        (result or {}).get("result") or "",
        conservar_anclas=policy.finding_identity == review_domain.IDENTIDAD_ANCLAS,
    )
    model_ok = modelo_utilizable(
        parse_model_findings((result or {}).get("result") or "", conservar_anclas=True)
    )
    try:
        dismiss_ids, dismiss_all, seen = collect_dismissals(
            repo, pr, login, comments, snapshot.command_cursor
        )
    except Exception as exc:
        print(
            f"ai-review: no se pudieron leer los descartes ({exc}); se sigue sin aplicarlos",
            file=sys.stderr,
        )
        dismiss_ids, dismiss_all, seen = set(), False, snapshot.command_cursor
    snapshot = review_domain.aplicar_descartes(
        snapshot, dismiss_ids, dismiss_all, comment_id=seen
    )
    snapshot = review_domain.avanzar_cursor(snapshot, seen)
    observaciones = [
        review_domain.observation_de_entrada(entry)
        for entry in (model or {}).get("findings", [])
    ]
    runtime_terminado = (result or {}).get("subtype") != "error_max_turns"
    cobertura = cobertura_declarada((result or {}).get("result") or "", model)
    if model is None:
        cobertura = review_domain.PARTIAL
    if continuacion_terminal(manifest):
        # Continuación terminal: el libro ya cubre el alcance y no queda nada
        # por leer; sin esto una pasada sin archivos degradaría a parcial.
        cobertura = review_domain.COMPLETE_CLAIM
    if model is not None and not model_ok:
        cobertura = review_domain.PARTIAL
    omisiones_alcance = tuple(
        f"archivo excluido ({e.get('reason')}): {e.get('path')}"
        for e in recortes_de_continuacion(manifest)
    )

    head = manifest.get("head")
    revision = None
    digest = digest_de_politica(policy)
    if sha_hexa(head) and sha_hexa(manifest.get("base")):
        # El digest viaja en la revisión persistida: la siguiente preparación
        # compara contra él para decidir si la cobertura previa sigue válida.
        # La base persistida es la SOLICITADA (el tip de BASE_SHA): el
        # merge-base movió con la rama base y compararlo contra el tip
        # solicitado forzaba un full sin cambio real de base. Manifiestos
        # legados sin base solicitada degradan una vez a un alcance acotado
        # por cobertura y queda documentado en el motivo.
        revision = review_domain.Revision(
            base_sha=manifest.get("requested_base") or manifest.get("base"),
            head_sha=head,
            policy_digest=digest,
        )
    facts = hechos_de_repo(
        manifest,
        set(),
        policy_digest=digest,
    )
    blobs = blobs_para_aceptar(
        snapshot, manifest, policy, rutas_delta=facts.changed_paths
    )
    revertidas = set()
    if revision is not None:
        cambiadas = set(review_domain.rutas_de_cambio(manifest)) | set(
            manifest.get("reviewed", []) or []
        )
        abiertas = {
            review_domain._ruta_primaria(f)
            for f in snapshot.findings
            if isinstance(f.status, review_domain.StatusOpen)
        }
        candidatos = sorted(abiertas & cambiadas)
        if candidatos:
            try:
                revertidas = files_reverted(
                    _repo_actual(),
                    candidatos,
                    manifest["base"],
                    manifest.get("prev_sha"),
                    head,
                )
            except Exception:
                revertidas = set()
    facts = replace(
        facts,
        reverted_paths=tuple(revertidas),
        blobs=blobs,
        omissions=facts.omissions + omisiones_alcance,
    )
    plan = review_domain.ReviewPlan(
        revision=revision,
        changed_paths=review_domain.rutas_de_cambio(manifest),
        previous_sha=manifest.get("prev_sha"),
        delivered=tuple(manifest.get("reviewed", []) or []),
        policy=policy,
    )
    report = review_domain.validar_reporte(
        observaciones, cobertura, facts, runtime_terminado=runtime_terminado
    )
    decision = review_domain.accept_report(snapshot, plan, report)
    if isinstance(decision, review_domain.Keep):
        return {
            "merged": [],
            "new_ids": [],
            "block": load.block,
            "model_ok": model_ok,
            "keep": decision.reason,
        }
    encoded = review_domain.encode_snapshot(
        decision.snapshot, review_domain.StorageBudget()
    )
    if isinstance(encoded, review_domain.CapacityExceeded):
        motivo = f"desborde ({encoded.needed} bytes para {encoded.limit})"
        print(
            f"ai-review: memoria {motivo} conservada sin modificar (Keep)",
            file=sys.stderr,
        )
        return {
            "merged": [],
            "new_ids": [],
            "block": load.block,
            "model_ok": model_ok,
            "keep": motivo,
        }
    previos = {f.id for f in snapshot.findings}
    return {
        "merged": review_domain.hallazgos_legacy(decision.snapshot),
        "new_ids": [f.id for f in decision.snapshot.findings if f.id not in previos],
        "block": encoded.block if snapshot.schema == 3 else encoded,
        "model_ok": model_ok,
        "completion": decision.snapshot.completion,
        "no_acreditados": review_domain.resoluciones_sin_acreditar(decision.snapshot),
    }


def build_findings(result, manifest, sticky, repo, pr, login, comments, policy=None):
    """Merge previous state with the model's block. Broken block: keep last parseable (B6).

    Memoria v2/v3 válida pasa por la aceptación del dominio con la política
    normalizada (`current` coincidencia compatible, `anchors` la de F0).
    Versiones futuras y memoria inválida se conservan (Keep); el legado
    sigue su propio camino de siempre.
    """
    policy = policy or politica_de_revision(manifest)
    load = (
        review_domain.read_snapshot(sticky["body"])
        if sticky
        else review_domain.Missing()
    )
    if isinstance(load, review_domain.Valid):
        return actualizar_memoria_valida(
            load, result, manifest, repo, pr, login, comments, policy
        )
    model = parse_model_findings((result or {}).get("result") or "")
    model_ok = modelo_utilizable(
        parse_model_findings((result or {}).get("result") or "", conservar_anclas=True)
    )
    if isinstance(load, (review_domain.Valid, review_domain.Future)):
        motivo = (
            f"schema {load.snapshot.schema}"
            if isinstance(load, review_domain.Valid)
            else f"versión {load.version}"
        )
        print(
            f"ai-review: memoria {motivo} conservada sin modificar (Keep)",
            file=sys.stderr,
        )
        return {
            "merged": [],
            "new_ids": [],
            "block": load.block,
            "model_ok": model_ok,
            "keep": motivo,
        }
    if isinstance(load, review_domain.Invalid):
        motivo = f"inválida ({load.reason})"
        print(
            f"ai-review: memoria {motivo} conservada sin modificar (Keep)",
            file=sys.stderr,
        )
        return {
            "merged": [],
            "new_ids": [],
            "block": load.block,
            "model_ok": model_ok,
            "keep": motivo,
        }
    prev = load.raw if isinstance(load, review_domain.Legacy) else None
    try:
        dismiss_ids, dismiss_all, last_seen = collect_dismissals(
            repo, pr, login, comments, (prev or {}).get("seen", 0)
        )
    except Exception as exc:
        print(
            f"ai-review: no se pudieron leer los descartes ({exc}); se sigue sin aplicarlos",
            file=sys.stderr,
        )
        dismiss_ids, dismiss_all, last_seen = set(), False, (prev or {}).get("seen", 0)
    has_previous_changes = manifest.get("mode") in (
        "incremental",
        "continuation",
    ) or manifest.get("reason") == "incomplete-prev"
    changed = review_domain.rutas_de_cambio(manifest)
    watched = {
        f["file"] for f in (prev or {}).get("findings", []) if f["state"] == OPEN
    }
    candidates = sorted(watched & set(changed)) if has_previous_changes else []
    base, head = manifest.get("base"), manifest.get("head")
    try:
        reverted = (
            files_reverted(
                _repo_actual(), candidates, base, manifest.get("prev_sha"), head
            )
            if candidates and base and head
            else set()
        )
    except Exception:
        reverted = set()
    merged, new_ids = merge_findings(
        prev,
        model,
        changed_files=changed,
        reverted_files=reverted,
        dismiss_ids=dismiss_ids,
        dismiss_all=dismiss_all,
    )
    merged["seen"] = last_seen
    bloque = review_domain.serialize_findings(
        merged, max_bytes=review_domain.StorageBudget().max_bytes
    )
    if isinstance(bloque, review_domain.CapacityExceeded):
        motivo = f"desborde ({bloque.needed} bytes para {bloque.limit})"
        print(
            f"ai-review: memoria {motivo} conservada sin modificar (Keep)",
            file=sys.stderr,
        )
        return {
            "merged": [],
            "new_ids": [],
            "block": load.block,
            "model_ok": model_ok,
            "keep": motivo,
        }
    antes = {f["id"]: f["state"] for f in (prev or {}).get("findings", []) if f.get("id")}
    resueltos_por_el_modelo = [
        e for e in (model or {}).get("findings", []) if e.get("state") == RESOLVED
    ]
    return {
        "merged": merged["findings"],
        "new_ids": new_ids,
        "block": bloque,
        "model_ok": model_ok,
        "no_acreditados": tuple(
            f["id"]
            for f in merged["findings"]
            if f["state"] == OPEN
            and antes.get(f["id"]) == OPEN
            and any(
                e.get("id") == f["id"] or same_issue(e, f)
                for e in resueltos_por_el_modelo
            )
        ),
    }


@dataclass
class PublishReceipt:
    generacion: int
    recibo: dict
    trabajo: tuple


@dataclass
class Unconfirmed:
    """Escritura incierta: el siguiente evento relee antes de reintentar."""

    motivo: str


@dataclass
class AuthenticatedResult:
    observaciones: tuple
    cobertura: str
    run_id: int
    attempt: int


@dataclass
class Rejected:
    """Resultado rechazado: nunca ejecuta contenido ni satisface la solicitud."""

    motivo: str


def _cuerpo_con_checkpoint(decision):
    encoded = review_domain.encode_snapshot(
        decision.snapshot, review_domain.StorageBudget()
    )
    if isinstance(encoded, review_domain.CapacityExceeded):
        return encoded
    return encoded.block if decision.snapshot.schema == 3 else encoded


def _parchar_aviso(observado, adaptador, banner):
    """El banner CAUTION va al sticky EXISTENTE, best-effort. Sin sticky no
    se crea comentario: un MARKER sin bloque válido secuestraría el sticky
    futuro. Si el PATCH falla, manda el Unconfirmed del llamador."""
    if observado is None:
        return False
    try:
        adaptador.parchar(
            observado["id"],
            insert_caution_banner(observado.get("body") or "", banner),
        )
    except Exception:
        return False
    return True


def _avisar_desborde(desborde, decision, observado, adaptador):
    revision = decision.snapshot.revision
    banner = caution_banner(
        f"memoria de desborde ({desborde.needed} bytes para {desborde.limit}) "
        "conservada; esta versión no la modifica",
        revision.head_sha if revision else "",
        has_previous=True,
    )
    return _parchar_aviso(observado, adaptador, banner)


def _avisar_sin_espacio(decision, observado, adaptador):
    """El comentario no trae el bloque de estado (no cabe el fijo del
    sticky): el checkpoint previo se conserva y el sticky queda visible con
    el aviso. Nunca se escribe un cuerpo sin bloque válido."""
    revision = decision.snapshot.revision
    banner = caution_banner(
        "comentario sin espacio para el estado; el checkpoint previo se "
        "conserva y esta versión no la modifica",
        revision.head_sha if revision else "",
        has_previous=True,
    )
    return _parchar_aviso(observado, adaptador, banner)


def _login_de(comentario):
    user = comentario.get("user")
    if isinstance(user, str):
        return user
    return comentario.get("login") or (user or {}).get("login")


def cuerpo_con_cabecera(bloque, resto):
    """MARKER, sha=, completion= y después el bloque, el orden de compose:
    reviewed_completion exige esas dos líneas justo tras el marcador."""
    lineas = resto.split("\n") if resto else []
    cabecera = []
    while lineas and lineas[0].startswith((SHA_PREFIX, COMPLETION_PREFIX)):
        cabecera.append(lineas.pop(0))
    cuerpo = "\n".join([MARKER, *cabecera, bloque])
    cola = "\n".join(lineas)
    return f"{cuerpo}\n{cola}" if cola else cuerpo


def texto_visible(cuerpo):
    return re.sub(r"<!--.*?-->", "", cuerpo or "", flags=re.S).strip()


def _resto_de(cuerpo):
    """Lo que `visible(bloque, resto)` recibe de un cuerpo: sin el marcador, sin
    el bloque y sin las líneas de estado, que se recalculan en cada escritura."""
    lineas = cuerpo.split("\n")
    resto = "\n".join(lineas[1:]) if lineas and lineas[0] == MARKER else cuerpo
    return "\n".join(
        linea
        for linea in strip_findings_block(resto, last=False).split("\n")
        if not linea.startswith((AVISO_EN_CURSO_PREFIX, AVISO_SIN_REVISION_PREFIX))
    ).strip()


def encadenar(*pasos):
    """Re-renders en orden: cada uno parte del cuerpo que armó el anterior."""

    def armar(bloque, resto):
        cuerpo = pasos[0](bloque, resto)
        for paso in pasos[1:]:
            cuerpo = paso(bloque, _resto_de(cuerpo))
        return cuerpo

    return armar


def publish_checkpoint(decision, observado, adaptador, login=None, visible=None):
    """El sticky gobierna por su PRIMER bloque: la escritura reemplaza ese
    bloque y conserva el resto (la prosa puede citar otros), salvo que
    `visible(bloque, resto)` arme el cuerpo completo. Relee antes de
    escribir (duplicados del bot y checkpoint ya aplicado); una respuesta de
    PATCH/POST perdida devuelve Unconfirmed para que el siguiente paso relea.
    Un Keep no escribe: es un no-op confirmado, igual que crear un sticky que
    no tendría texto visible. La línea de revisión en curso no se arrastra:
    solo estado_visible la vuelve a poner mientras siga pendiente.
    """
    if not isinstance(decision, review_domain.Commit):
        return PublishReceipt(generacion=0, recibo={"sin_cambios": True}, trabajo=())
    encoded = _cuerpo_con_checkpoint(decision)
    if isinstance(encoded, review_domain.CapacityExceeded):
        motivo = "desborde de memoria: se conserva el checkpoint previo"
        if _avisar_desborde(encoded, decision, observado, adaptador):
            motivo += "; aviso publicado en el comentario"
        return Unconfirmed(motivo)
    bloque = encoded
    marcados = [
        c
        for c in adaptador.leer()
        if MARKER in (c.get("body") or "") and (login is None or _login_de(c) == login)
    ]
    if len(marcados) > 1:
        return Unconfirmed("múltiples comentarios con el marcador; escritura detenida")
    carga_observada = review_domain.read_snapshot(
        (observado or {}).get("body") or "", last=False
    )
    if (
        observado is not None
        and isinstance(carga_observada, review_domain.Valid)
        and carga_observada.block == bloque
    ):
        return PublishReceipt(
            generacion=decision.snapshot.generation,
            recibo={"comentario_id": observado.get("id"), "ya_aplicado": True},
            trabajo=decision.work_after_commit,
        )
    if visible is None:
        visible = cuerpo_con_cabecera

    if observado is None:
        cuerpo = visible(bloque, "")
        if not texto_visible(cuerpo):
            return PublishReceipt(
                generacion=0, recibo={"sin_texto_visible": True}, trabajo=()
            )
        if not cuerpo_publicable(cuerpo, bloque):
            return Unconfirmed(
                "el comentario no trae el bloque de estado; no se crea sticky"
            )
        try:
            adaptador.crear(cuerpo)
        except Exception as exc:
            return Unconfirmed(f"respuesta de POST incierta: {exc}")
        creado = next(
            (
                c
                for c in adaptador.leer()
                if MARKER in (c.get("body") or "")
                and (login is None or _login_de(c) == login)
            ),
            None,
        )
        return PublishReceipt(
            generacion=decision.snapshot.generation,
            recibo={"comentario_id": creado.get("id") if creado else None},
            trabajo=decision.work_after_commit,
        )
    cuerpo = visible(bloque, _resto_de(observado.get("body") or ""))
    if not texto_visible(cuerpo):
        # Un sticky que ya existe no puede quedar como un comentario en blanco.
        cuerpo = f"{cuerpo}\n\n{LINEA_SIN_REVISION}"
    if not cuerpo_publicable(cuerpo, bloque):
        motivo = (
            "el comentario no trae el bloque de estado: se conserva el "
            "checkpoint previo"
        )
        if _avisar_sin_espacio(decision, observado, adaptador):
            motivo += "; aviso publicado en el comentario"
        return Unconfirmed(motivo)
    try:
        adaptador.parchar(observado["id"], cuerpo)
    except Exception as exc:
        return Unconfirmed(f"respuesta de PATCH incierta: {exc}")
    return PublishReceipt(
        generacion=decision.snapshot.generation,
        recibo={"comentario_id": observado["id"]},
        trabajo=decision.work_after_commit,
    )


def request_de_solicitud(
    solicitud, repository="", workflow="ai-review-worker", ref=None, workflow_sha=None
):
    """Contexto de confianza del resultado: el target sale de la solicitud
    persistida, nunca del artifact que el propio worker escribió."""
    return {
        "id": solicitud.id,
        "target": dict(solicitud.target),
        "repository": repository,
        "workflow": workflow,
        "ref": ref,
        "workflow_sha": workflow_sha,
    }


def dispatch_confirmed(recibo: PublishReceipt, *, despachar):
    for solicitud in recibo.trabajo:
        despachar(solicitud)


def forma_utilizable_del_artifact(artifact):
    modelo = parse_model_findings(artifact.get("result") or "", conservar_anclas=True)
    return modelo_utilizable({"findings": artifact.get("observaciones") or ()}) and (
        modelo is None or modelo_utilizable(modelo)
    )


def rechazo_del_run(run_metadata, request):
    """Por qué el run no es el worker confiable (repo, workflow, ref y SHA del
    código), o None si lo es."""
    if run_metadata.get("repository") != request.get("repository"):
        return "repositorio del run incorrecto"
    if run_metadata.get("workflow") != request.get("workflow"):
        return "workflow del run incorrecto"
    if run_metadata.get("ref") != request.get("ref"):
        return "ref del run incorrecta"
    if run_metadata.get("sha") != request.get("workflow_sha"):
        return "SHA del workflow no es el código confiable"
    return None


def authenticate_result(run_metadata, artifact, request):
    """El SHA del workflow (código confiable) y el HEAD del PR se validan por
    separado; el contenido del artifact nunca se ejecuta.
    """

    def rechazo(motivo):
        return Rejected(motivo=motivo)

    motivo = rechazo_del_run(run_metadata, request)
    if motivo is not None:
        return rechazo(motivo)
    if run_metadata.get("run_id") != artifact.get("run_id"):
        return rechazo("run_id no coincide con el artifact")
    if run_metadata.get("attempt") != artifact.get("attempt"):
        return rechazo("attempt no coincide con el artifact")
    if artifact.get("request_id") != request.get("id"):
        return rechazo("request_id del artifact incorrecto")
    target = request.get("target") or {}
    if artifact.get("pr_head_sha") != target.get("head_sha"):
        return rechazo("HEAD del PR del artifact no es el target vigente")
    if artifact.get("policy_digest") != target.get("policy_digest"):
        return rechazo("digest de política del artifact incorrecto")
    modelo = parse_model_findings(
        artifact.get("result") or "",
        conservar_anclas=artifact.get("finding_identity") == "anchors",
    )
    return AuthenticatedResult(
        observaciones=tuple(
            modelo["findings"]
            if modelo is not None
            else artifact.get("observaciones") or ()
        ),
        cobertura=(artifact.get("cobertura") or review_domain.UNKNOWN)
        if forma_utilizable_del_artifact(artifact)
        else review_domain.PARTIAL,
        run_id=artifact.get("run_id"),
        attempt=artifact.get("attempt"),
    )


def summary_of(body):
    return "\n".join(
        line
        for line in body.split("\n")
        if line != MARKER
        and not line.startswith(
            (SHA_PREFIX, COMPLETION_PREFIX, FINDINGS_PREFIX, COVERED_PREFIX)
        )
    )


def cmd_publish(args):
    work = Path(args.work)
    repo, pr, head = env("REPO"), env("PR_NUMBER"), env("HEAD_SHA")
    login = os.environ.get("BOT_LOGIN") or "github-actions[bot]"
    name, _ = get_provider()
    result = json.loads((work / "result.json").read_text())
    name = result.get("review_provider", name)
    review_github.avisar_bloque_sin_cierre(result.get("result") or "")
    manifest = json.loads((work / "manifest.json").read_text())
    comments = review_github.fetch_all_comments(repo, pr)
    sticky = sticky_from_comments(comments, login)

    revisado = False
    escribir = True
    if ERROR_KEY in result:
        # Infra failure: don't mark this sha as reviewed, keep whatever review was there before.
        reason = result[ERROR_KEY]
        has_previous = bool(sticky and reviewed_sha(sticky.get("body") or ""))
        banner = caution_banner(reason, head, has_previous=has_previous)
        body = (
            insert_caution_banner(sticky["body"], banner)
            if sticky
            else f"{MARKER}\n{banner}"
        )
        body = redact(
            body,
            [
                os.environ.get("API_KEY", ""),
                os.environ.get("FALLBACK_API_KEY", ""),
                os.environ.get("GH_TOKEN", ""),
            ],
        )
        summary_text = redact(
            banner,
            [
                os.environ.get("API_KEY", ""),
                os.environ.get("FALLBACK_API_KEY", ""),
                os.environ.get("GH_TOKEN", ""),
            ],
        )
    else:
        findings = build_findings(result, manifest, sticky, repo, pr, login, comments)
        if findings.get("keep"):
            # Memoria v2/v3, de versión futura, inválida o desbordada: se conserva
            # el comentario sin modificar (o MARKER + banner si no hay sticky),
            # el sha= y la cobertura no avanzan y se avisa en visible, como en
            # la ruta de falla de infraestructura.
            banner = caution_banner(
                f"memoria de {findings['keep']} conservada; esta versión no la modifica",
                head,
                has_previous=bool(sticky),
            )
            if sticky:
                body = insert_caution_banner(sticky["body"], banner)
            else:
                body = f"{MARKER}\n{banner}"
            body = redact(
                body,
                [
                    os.environ.get("API_KEY", ""),
                    os.environ.get("FALLBACK_API_KEY", ""),
                    os.environ.get("GH_TOKEN", ""),
                ],
            )
            summary_text = redact(
                banner,
                [
                    os.environ.get("API_KEY", ""),
                    os.environ.get("FALLBACK_API_KEY", ""),
                    os.environ.get("GH_TOKEN", ""),
                ],
            )
        else:
            cuerpo = redact(
                compose(
                    result,
                    manifest,
                    sha=head,
                    provider=name,
                    findings=findings,
                    libro=libro_actualizado(
                        sticky,
                        manifest,
                        result,
                        _repo_actual(),
                        findings.get("model_ok", True),
                    )
                    if manifest.get("head")
                    else None,
                    budget=review_domain.StorageBudget(),
                ),
                [
                    os.environ.get("API_KEY", ""),
                    os.environ.get("FALLBACK_API_KEY", ""),
                    os.environ.get("GH_TOKEN", ""),
                ],
            )
            if not cuerpo_publicable(cuerpo, findings["block"]):
                # El fijo del comentario no cabe (ni sin libro): el cuerpo no
                # trae el bloque y escribirlo borraría la memoria. Keep + banner
                # como en el desborde; sin sticky previo no se crea nada.
                revisado = False
                banner = caution_banner(
                    "comentario sin espacio para el estado; el checkpoint "
                    "previo se conserva y esta versión no la modifica",
                    head,
                    has_previous=bool(sticky),
                )
                if sticky:
                    cuerpo = insert_caution_banner(sticky["body"], banner)
                else:
                    escribir = False
                    print(
                        "ai-review: comentario sin espacio para el estado; "
                        "no se crea sticky"
                    )
                body = cuerpo
                summary_text = banner
            else:
                revisado = True
                body = cuerpo
                summary_text = summary_of(body)

    payload = work / "comment.json"
    payload.write_text(json.dumps({"body": body}))

    if not escribir:
        print("ai-review: sin escritura en esta corrida (comentario sin espacio)")
    elif sticky:
        review_github.sh(
            "gh",
            "api",
            "-X",
            "PATCH",
            f"repos/{repo}/issues/comments/{sticky['id']}",
            "--input",
            str(payload),
        )
        print(f"ai-review: comentario {sticky['id']} actualizado")
    else:
        review_github.sh(
            "gh",
            "api",
            "-X",
            "POST",
            f"repos/{repo}/issues/{pr}/comments",
            "--input",
            str(payload),
        )
        print("ai-review: comentario creado")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as fh:
            fh.write(summary_text + "\n")
    review_github.informar_revision(revisado)


def _parser():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=[
            "gate",
            "prepare",
            "install",
            "run",
            "publish",
            "reconcile",
            "execute-request",
            "close-result",
        ],
    )
    parser.add_argument("--request-id", default="")
    parser.add_argument(
        "--work",
        default=os.path.join(os.environ.get("RUNNER_TEMP", "/tmp"), "ai-review"),
    )
    parser.add_argument("--prompt", default=str(Path(__file__).with_name("prompt.md")))
    return parser


def main():
    args = _parser().parse_args()
    {
        "gate": cmd_gate,
        "prepare": cmd_prepare,
        "install": cmd_install,
        "run": review_run.cmd_run,
        "publish": cmd_publish,
        "reconcile": cmd_reconcile,
        "execute-request": cmd_execute_request,
        "close-result": cmd_close_result,
    }[args.command](args)


class ComentariosGh:
    def __init__(self, repo, pr):
        self.repo, self.pr = repo, pr

    def leer(self):
        return review_github.fetch_all_comments(self.repo, self.pr)

    def parchar(self, cid, body):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write(json.dumps({"body": body}))
            ruta = fh.name
        try:
            review_github.sh(
                "gh",
                "api",
                "-X",
                "PATCH",
                f"repos/{self.repo}/issues/comments/{cid}",
                "--input",
                ruta,
            )
        finally:
            Path(ruta).unlink(missing_ok=True)

    def crear(self, body):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write(json.dumps({"body": body}))
            ruta = fh.name
        try:
            review_github.sh(
                "gh",
                "api",
                "-X",
                "POST",
                f"repos/{self.repo}/issues/{self.pr}/comments",
                "--input",
                ruta,
            )
        finally:
            Path(ruta).unlink(missing_ok=True)


def despachar_worker(solicitud, *, repo, ref, run_id, pr_number):
    review_github.sh(
        "gh",
        "workflow",
        "run",
        "ai-review-worker.yml",
        "--repo",
        repo,
        "--ref",
        ref,
        "-f",
        f"request_id={solicitud.id}",
        "-f",
        f"kind={solicitud.kind}",
        "-f",
        f"pr_number={pr_number}",
        "-f",
        f"head_sha={solicitud.target.get('head_sha', '')}",
        "-f",
        f"base_sha={solicitud.target.get('base_sha', '')}",
        "-f",
        f"coordinator_run_id={run_id}",
        "-f",
        f"finding_identity={politica_de_identidad()}",
    )


def _pr_admisible(pr_data, repo):
    """Lo que el `if:` del workflow exige a un push: PR abierto, no borrador y
    del propio repo. Sin datos del PR es un push, que el workflow ya filtró."""
    if pr_data is None:
        return True
    cabeza = pr_data.get("head") or {}
    return (
        pr_data.get("state") == "open"
        and pr_data.get("draft") is False
        and (cabeza.get("repo") or {}).get("full_name") == repo
    )


def _admision(target, pr_data, repo):
    """La revisión que pide cualquier evento. El push trae su head y reintenta
    una revisión fallida; la recuperación manual siempre pide una nueva; el
    resto admite el head vivo solo si el PR es admisible."""
    nombre = os.environ.get("GITHUB_EVENT_NAME", "")
    recuperacion = nombre == "workflow_dispatch" and not os.environ.get(
        "WORKER_RUN_ID"
    )
    if not recuperacion and not _pr_admisible(pr_data, repo):
        return None
    return review_domain.RequestReview(
        origin=review_domain.Origin(
            kind="re-run" if recuperacion else "push", run_id=_run_id()
        ),
        target=target,
        reintenta=nombre in ("pull_request", "pull_request_target"),
    )


def _run_id():
    valor = os.environ.get("GITHUB_RUN_ID") or "0"
    return int(valor) if valor.isdigit() else 0


def _estado_actual(sticky):
    """Estado migrado a v3 para el coordinador; None sólo con memoria
    inválida o de versión futura (se conserva, como en cmd_publish). Un
    sticky sin bloque de memoria es ausencia real: arranca vacío."""
    if sticky:
        load = review_domain.read_snapshot(sticky["body"])
        if isinstance(load, review_domain.Valid):
            if load.snapshot.schema == 3:
                return load.snapshot
            return review_domain.snapshot_a_v3(load.snapshot)
        if isinstance(load, review_domain.Legacy):
            return review_domain.snapshot_a_v3(load.snapshot)
        if isinstance(load, review_domain.Missing):
            return review_domain.Snapshot(
                schema=3,
                generation=1,
                revision=None,
                next_id=1,
                completion=review_domain.UNKNOWN,
                findings=[],
                command_cursor=0,
            )
        return None
    return review_domain.Snapshot(
        schema=3,
        generation=1,
        revision=None,
        next_id=1,
        completion=review_domain.UNKNOWN,
        findings=[],
        command_cursor=0,
    )


def _comandos_de_comentarios(comentarios, login, cursor):
    """El checkpoint es datos, nunca comandos: fuera los comentarios del
    propio bot y el texto que no arranca con el comando."""

    comandos = []
    for c in comentarios or []:
        cid = c.get("id")
        if not isinstance(cid, int) or cid <= cursor:
            continue
        autor = _login_de(c)
        if login and autor == login:
            continue
        cuerpo = c.get("body") or ""
        parsed = parse_comando_reconcile(cuerpo)
        if parsed is None:
            if not re.match(r"\s*ai-review:", cuerpo, re.IGNORECASE):
                continue
            parsed = {"action": review_domain.ACCION_NO_ENTENDIDA, "arg": ""}
        comandos.append(
            {
                "cid": cid,
                "login": autor,
                "creado": c.get("created_at") or "",
                "editado": c.get("updated_at") or "",
                **parsed,
            }
        )
    return tuple(comandos)


def fallo_de_resultado(artifact, run_id, attempt):
    # La presencia de la clave decide, como reviewed= en el worker y el aviso
    # de revision_visible: un motivo vacío sigue siendo un fallo.
    if ERROR_KEY not in (artifact or {}):
        return None
    return review_domain.ReportFailed(
        request_id=artifact.get("request_id"),
        run_id=run_id,
        attempt=attempt,
        motivo=artifact[ERROR_KEY] or "el worker falló sin motivo",
        retryable=True,
    )


TITULO_DEL_WORKER = re.compile(r"PR ([0-9]{1,9}) solicitud ([0-9]{1,9})")
CONCLUSIONES_DE_GITHUB = frozenset(
    {
        "action_required",
        "cancelled",
        "failure",
        "neutral",
        "skipped",
        "stale",
        "startup_failure",
        "success",
        "timed_out",
    }
)


@dataclass(frozen=True)
class Entrega:
    """Lo que dejó el run de una solicitud viva: su artifact autenticado, o
    None si terminó sin subirlo."""

    solicitud: object
    meta: dict
    conclusion: str | None
    artifact: dict | None = None
    autenticado: AuthenticatedResult | None = None


def _run_avisado():
    """(run, attempt) del worker que avisó; las entradas del dispatch solo se
    comparan como enteros."""
    run_id = os.environ.get("WORKER_RUN_ID") or ""
    attempt = os.environ.get("WORKER_ATTEMPT") or "1"
    if re.fullmatch(r"[0-9]{1,19}", run_id) and re.fullmatch(r"[0-9]{1,9}", attempt):
        return int(run_id), int(attempt)
    return None


def corridas_por_solicitud(repo, pr):
    """Los runs del worker de este PR por solicitud, del más nuevo al más viejo.
    El run-name del worker es `PR <n> solicitud <id>`: así se atribuye también
    un run que terminó sin artifact."""
    datos = json.loads(
        review_github.sh(
            "gh",
            "api",
            f"repos/{repo}/actions/workflows/ai-review-worker.yml/runs"
            "?event=workflow_dispatch&per_page=100",
        ).stdout
    )
    corridas = {}
    for run in datos.get("workflow_runs") or []:
        titulo = TITULO_DEL_WORKER.fullmatch(str(run.get("display_title") or ""))
        if (
            titulo
            and int(titulo.group(1)) == int(pr)
            and type(run.get("id")) is int
            and type(run.get("run_attempt")) is int
        ):
            corridas.setdefault(int(titulo.group(2)), []).append(run)
    for runs in corridas.values():
        runs.sort(key=lambda run: -run["id"])
    return corridas


def sha_de_confianza(repo, worker_ref, sha_del_run, cache):
    """El SHA del código confiable para un run: la punta de worker_ref, o el del
    run si es un ancestro suyo (main pudo avanzar mientras el worker corría)."""
    if "punta" not in cache:
        cache["punta"] = json.loads(
            review_github.sh("gh", "api", f"repos/{repo}/commits/{worker_ref}").stdout
        ).get("sha")
    if not sha_hexa(sha_del_run) or sha_del_run == cache["punta"]:
        return cache["punta"]
    if sha_del_run not in cache:
        comparacion = json.loads(
            review_github.sh("gh", "api", f"repos/{repo}/compare/{sha_del_run}...{worker_ref}").stdout
        )
        cache[sha_del_run] = comparacion.get("status") in ("ahead", "identical")
    return sha_del_run if cache[sha_del_run] else cache["punta"]


def artefacto_del_run(repo, run, request_id, work):
    """El result.json del artifact del attempt vigente, o None si no lo subió.
    El nombre y la carpeta salen de enteros, nunca del texto de la API."""
    nombre = (
        f"ai-review-result-request-{request_id}"
        f"-run-{run['id']}-attempt-{run['run_attempt']}"
    )
    listado = json.loads(
        review_github.sh(
            "gh", "api", f"repos/{repo}/actions/runs/{run['id']}/artifacts?per_page=100"
        ).stdout
    )
    if not any(
        a.get("name") == nombre and not a.get("expired")
        for a in listado.get("artifacts") or []
    ):
        return None
    destino = Path(work) / "cosecha" / f"{run['id']}-{run['run_attempt']}"
    review_github.sh(
        "gh",
        "run",
        "download",
        str(run["id"]),
        "--repo",
        repo,
        "--name",
        nombre,
        "--dir",
        str(destino),
    )
    encontrados = list(destino.rglob("result.json"))
    if len(encontrados) != 1:
        raise RuntimeError(f"{nombre} trae {len(encontrados)} result.json, no uno")
    return json.loads(encontrados[0].read_text())


def cosechar(estado, repo, pr, worker_ref, work, avisado):
    """Lo que el worker ya dejó para las solicitudes vivas. De cada una cuenta
    su run más nuevo y confiable: con artifact del attempt vigente, o
    terminado sin él (el avisado ya terminó su execute aunque su job de aviso
    siga en curso); si sigue en curso, espera. Una pendiente sin ningún run
    perdió su despacho; una fallida sin run no se despacha, porque reintentar
    es cosa de un push, de `revisar` o de la recuperación. De una fallida
    solo cuenta un artifact, el de un "Re-run jobs" exitoso del worker:
    volver a reportar su fallo pintaría el aviso encima de la revisión nueva.
    Devuelve (entregas, sin_corrida, hubo_rechazo)."""
    vivas = {r.id: r for r in estado.pending_requests if r.state != "finished"}
    if avisado is None and not vivas:
        return [], [], False
    corridas, rechazo, rid_avisado = corridas_por_solicitud(repo, pr), False, None
    for rid, runs in corridas.items():
        if avisado and any(run["id"] == avisado[0] for run in runs):
            rid_avisado = rid
    if avisado and rid_avisado is None:
        print(
            f"ai-review: el run avisado {avisado[0]} no es un worker de este PR",
            file=sys.stderr,
        )
        rechazo = True
    elif rid_avisado is not None and rid_avisado not in vivas:
        if not 0 < rid_avisado <= estado.request_count:
            sys.exit(f"ai-review: resultado de solicitud desconocida {rid_avisado!r}")
        # La podó una solicitud más nueva, cuyo despacho cancela este worker;
        # si el run alcanzó a avisar antes, no queda nada que publicar.
        print(
            f"ai-review: resultado de la solicitud {rid_avisado}"
            " ignorado: ya no está pendiente",
            file=sys.stderr,
        )
    confianza, entregas, sin_corrida = {}, [], []
    for solicitud in vivas.values():
        fallida = solicitud.state == "failed_retryable" and solicitud.id != rid_avisado
        if solicitud.id not in corridas:
            if not fallida:
                sin_corrida.append(solicitud)
            continue
        for run in corridas[solicitud.id]:
            forma = forma_del_run(run)
            meta = {
                "repository": repo,
                "workflow": forma["name"],
                "ref": forma["head_branch"],
                "sha": forma["head_sha"],
                "run_id": forma["id"],
                "attempt": forma["run_attempt"],
            }
            request = request_de_solicitud(
                solicitud,
                repository=repo,
                workflow="ai-review-worker",
                ref=worker_ref,
                workflow_sha=sha_de_confianza(repo, worker_ref, meta["sha"], confianza),
            )
            motivo = rechazo_del_run(meta, request)
            if motivo is not None:
                print(
                    f"ai-review: run {run['id']} ignorado ({motivo})", file=sys.stderr
                )
                rechazo = True
                continue
            conclusion = run.get("conclusion")
            if conclusion not in CONCLUSIONES_DE_GITHUB:
                conclusion = None
            terminado = run.get("status") == "completed" or avisado == (
                run["id"],
                run["run_attempt"],
            )
            artifact = None
            if terminado or run.get("status") == "in_progress":
                artifact = artefacto_del_run(repo, run, solicitud.id, work)
            if artifact is not None:
                autenticado = authenticate_result(meta, artifact, request)
                if isinstance(autenticado, Rejected):
                    print(
                        f"ai-review: resultado rechazado ({autenticado.motivo})",
                        file=sys.stderr,
                    )
                    rechazo = True
                    break
                entregas.append(
                    Entrega(
                        solicitud, meta, conclusion, artifact, autenticado
                    )
                )
            elif terminado and not fallida:
                entregas.append(Entrega(solicitud, meta, conclusion))
            break
    return entregas, sin_corrida, rechazo


def evento_de_entrega(entrega, facts, estado):
    run_id, attempt = entrega.meta["run_id"], entrega.meta["attempt"]
    if entrega.artifact is None:
        motivo = (
            f"el worker terminó sin resultado (run {run_id}, "
            f"{entrega.conclusion or 'sin conclusión'})"
        )
        return (
            review_domain.ReportFailed(
                request_id=entrega.solicitud.id,
                run_id=run_id,
                attempt=attempt,
                motivo=motivo,
            ),
            facts,
        )
    fallo = fallo_de_resultado(entrega.artifact, run_id, attempt)
    if fallo is not None:
        return fallo, facts
    hechos = hechos_con_delta_del_worker(facts, entrega.artifact, estado)
    hechos = hechos_con_recortes_del_worker(hechos, entrega.artifact)
    return (
        review_domain.ReportReady(
            origin=review_domain.Origin(kind="re-run", run_id=run_id),
            request_id=entrega.solicitud.id,
            run_id=entrega.autenticado.run_id,
            attempt=entrega.autenticado.attempt,
            observaciones=tuple(
                review_domain.observation_de_entrada(e)
                for e in entrega.autenticado.observaciones
            ),
            cobertura=entrega.autenticado.cobertura,
        ),
        hechos,
    )


def forma_del_run(datos):
    """Un run de la lista de runs del worker en la forma que autentica el
    coordinador. Con run-name, "name" trae el título del run; el workflow sale
    del path, que el título no puede imitar."""
    ruta = datos.get("path") or ""
    return {
        "id": datos.get("id"),
        "name": Path(ruta).stem if ruta.startswith(".github/workflows/") else None,
        "head_branch": datos.get("head_branch"),
        "head_sha": datos.get("head_sha"),
        "run_attempt": datos.get("run_attempt"),
    }


def hechos_con_delta_del_worker(facts, artifact, current):
    """Los hechos del coordinador con el delta que calculó el worker, para que
    una resolución del modelo se pueda verificar contra un cambio pertinente.

    Se llama solo con un artifact autenticado (run del worker en el ref de
    confianza): el manifest lo escribe el CLI fijado, que trata el PR como
    datos. Además el delta debe partir del head que esta memoria acreditó por
    última vez; si no, es de otra base y se degrada a «sin delta» (el resuelto
    del modelo queda sin verificar), con aviso."""
    manifest = artifact.get("manifest") or {}
    desde = manifest.get("prev_sha")
    cambiados = manifest.get("changed_files")
    esperado = current.revision.head_sha if current.revision else None
    if (
        sha_hexa(desde)
        and desde == esperado
        and isinstance(cambiados, list)
        and all(isinstance(ruta, str) and ruta for ruta in cambiados)
    ):
        return replace(facts, changed_paths=tuple(cambiados), delta_calculado=True)
    if not current.findings:
        return facts  # nada que resolver: la primera revisión no lleva delta
    print(
        f"ai-review: sin delta verificable del worker (prev_sha {str(desde)[:7]} "
        f"contra el último revisado {str(esperado)[:7]}); las resoluciones del "
        f"modelo quedan sin verificar",
        file=sys.stderr,
    )
    return facts


def hechos_con_recortes_del_worker(facts, artifact):
    """Las omisiones que el worker ya aplicó (archivos fuera por tamaño del
    diff o por encoding), plegadas en las omisiones con el mismo formato de la
    ruta directa: sin él, el «complete» que declara el modelo acredita
    archivos que nadie leyó. El manifest viaja en el artifact autenticado y el
    dominio baja la cobertura con cualquier omisión."""
    excluidos = (artifact.get("manifest") or {}).get("excluded") or []
    recortes = tuple(
        f"archivo excluido ({e.get('reason')}): {e.get('path')}"
        for e in excluidos
        if e.get("reason") in ("budget", "encoding")
    )
    if not recortes:
        return facts
    return replace(facts, omissions=facts.omissions + recortes)


def _ultimo_comentario(adaptador):
    return max(
        (c["id"] for c in adaptador.leer() if isinstance(c.get("id"), int)),
        default=0,
    )


def cmd_reconcile(args):
    """Un run converge el PR sin importar qué evento lo despertó: los comandos
    sin procesar (el cursor los hace idempotentes), los resultados que el
    worker ya dejó, la admisión del head y al final una sola escritura."""
    repo, pr = env("REPO"), env("PR_NUMBER")
    worker_ref = env("WORKER_REF")
    login = os.environ.get("BOT_LOGIN") or "github-actions[bot]"
    policy = politica_de_revision()
    digest = digest_de_politica(policy)
    adaptador = ComentariosGh(repo, pr)
    comments = adaptador.leer()
    sticky = sticky_from_comments(comments, login)
    current = _estado_actual(sticky)
    if current is None:
        print(
            "ai-review: memoria inválida o de versión futura; se conserva sin tocar",
            file=sys.stderr,
        )
        return
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    if event_name == "issue_comment":
        payload = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        if payload.get("action") != "created":
            accion = payload.get("action")
            print(
                f"ai-review: issue_comment {accion!r} no admite comandos",
                file=sys.stderr,
            )
            return
    head = os.environ.get("HEAD_SHA") or ""
    base = os.environ.get("BASE_SHA") or ""
    pr_data = None
    if event_name in ("issue_comment", "workflow_dispatch"):
        # el head/base vivos salen del PR por API, nunca del checkpoint
        try:
            pr_data = json.loads(review_github.sh("gh", "api", f"repos/{repo}/pulls/{pr}").stdout)
            head = pr_data["head"]["sha"]
            base = pr_data["base"]["sha"]
        except Exception as exc:
            print(
                f"ai-review: no se pudo consultar el PR ({exc}); "
                "queda pendiente para el próximo evento",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        head = head or (current.revision.head_sha if current.revision else "")
        base = base or (current.revision.base_sha if current.revision else "")
        if not head or not base:
            sys.exit(
                "ai-review: faltan HEAD_SHA/BASE_SHA y el checkpoint no tiene revisión"
            )
    facts = review_domain.RepositoryFacts(
        revision=review_domain.Revision(
            base_sha=base, head_sha=head, policy_digest=digest
        )
    )
    target = review_domain.ReviewTarget(
        repository=repo,
        pr_number=int(pr),
        head_sha=head,
        base_sha=base,
        policy_digest=digest,
    )

    def permisos(login_consultado):
        return review_github.collaborator_permission(repo, login_consultado)

    estado, trabajo, rechazos = review_domain.procesar_comandos(
        current,
        _comandos_de_comentarios(comments, login=login, cursor=current.command_cursor),
        permisos,
        target,
    )
    for cid, effect in rechazos:
        print(f"ai-review: comando {cid} rechazado ({effect})")
    avisos = tuple(
        AVISOS_DE_RECHAZO[effect](cid)
        for cid, effect in rechazos
        if effect in AVISOS_DE_RECHAZO
    )

    if event_name not in (
        "issue_comment",
        "workflow_dispatch",
        "pull_request",
        "pull_request_target",
    ):
        sys.exit(
            "ai-review: evento sin admisión en reconcile "
            "(pull_request, pull_request_target, issue_comment o workflow_dispatch)"
        )

    try:
        entregas, sin_corrida, rechazo = cosechar(
            estado, repo, pr, worker_ref, args.work, _run_avisado()
        )
    except Exception as exc:
        print(
            f"ai-review: no se pudo cosechar el worker ({exc}); "
            "se reintenta en el próximo evento",
            file=sys.stderr,
        )
        entregas, sin_corrida, rechazo = [], [], True
    # Se despacha otra vez; si el run solo tardó en aparecer en la lista, el
    # grupo del worker cancela al anterior y cuenta el más nuevo. Un PR
    # cerrado o en borrador no gasta otra corrida del modelo.
    if _pr_admisible(pr_data, repo):
        por_despachar = {w.id for w in trabajo}
        trabajo = (*trabajo, *(s for s in sin_corrida if s.id not in por_despachar))
    previo = estado
    revision_entregada, explicaciones = None, []
    for entrega in entregas:
        evento, hechos = evento_de_entrega(entrega, facts, estado)
        decision = review_domain.reconcile(estado, evento, hechos, policy)
        if not isinstance(decision, review_domain.Commit):
            continue
        solicitud, artifact = entrega.solicitud, entrega.artifact
        # Solo un reporte del target vigente se muestra como revisión: el
        # dominio también hace Commit al descartar un head viejo.
        if review_domain._target_vigente(solicitud, facts, policy):
            if solicitud.kind == "review":
                revision_entregada = (
                    artifact or {ERROR_KEY: evento.motivo},
                    {f.id for f in estado.findings},
                    solicitud.target.get("head_sha") or head,
                )
            elif artifact is not None and ERROR_KEY not in artifact:
                explicaciones.append(
                    (solicitud.finding_id, texto_de_explicacion(artifact))
                )
        estado = decision.snapshot
    estado = review_domain.marcar_hallazgos_nuevos(
        previo, estado, lambda: _ultimo_comentario(adaptador)
    )
    admision = _admision(target, pr_data, repo)
    if admision is not None:
        admitido = review_domain.reconcile(estado, admision, facts, policy)
        estado = admitido.snapshot
        # Una revisión nueva poda la anterior, que ya no se despacha.
        vivas = {r.id for r in estado.pending_requests}
        trabajo = tuple(w for w in trabajo if w.id in vivas)
        trabajo = (*trabajo, *admitido.work_after_commit)

    if trabajo:
        snapshot_ra, despachables, terminadas = review_domain.reautorizar(
            estado, trabajo, permisos
        )
        estado, trabajo = snapshot_ra or estado, tuple(despachables)
        for terminada in terminadas:
            print(f"ai-review: solicitud {terminada.id} terminada ({terminada.motivo})")

    if estado == current and not trabajo:
        print("ai-review: nada cambió; el sticky queda como está")
        if rechazo:
            sys.exit(1)
        return

    # Varias explicaciones pueden llegar en la misma cosecha: una por re-render.
    pasos = [estado_visible(estado, e) for e in explicaciones[:-1]]
    pasos.append(estado_visible(estado, (explicaciones or [None])[-1], avisos))
    if revision_entregada is not None:
        artifact, previos, cabeza = revision_entregada
        revision = revision_visible(artifact, estado, previos, cabeza)
        pasos = [revision, *pasos] if avisos or explicaciones else [revision]
    visible = encadenar(*pasos)
    resultado = publish_checkpoint(
        review_domain.Commit(snapshot=estado, work_after_commit=trabajo),
        sticky or None,
        adaptador,
        login=login,
        visible=visible,
    )
    if isinstance(resultado, Unconfirmed):
        print(
            f"ai-review: escritura incierta ({resultado.motivo}); se relee en el próximo evento",
            file=sys.stderr,
        )
        sys.exit(1)
    if resultado.trabajo:
        dispatch_confirmed(
            resultado,
            despachar=lambda s: despachar_worker(
                s, repo=repo, ref=worker_ref, run_id=_run_id(), pr_number=pr
            ),
        )
        print(
            f"ai-review: {len(resultado.trabajo)} solicitud(es) confirmada(s) y despachada(s)"
        )
    elif resultado.recibo.get("sin_texto_visible"):
        print("ai-review: nada visible que mostrar; no se crea sticky")
    else:
        print("ai-review: checkpoint publicado sin trabajo nuevo")
    if rechazo:
        sys.exit(1)


def cmd_execute_request(args):
    """Lee la solicitud persistida y escribe el paquete de datos para el
    artifact. Nunca publica ni ejecuta contenido del PR."""
    repo, pr = env("REPO"), env("PR_NUMBER")
    head = env("HEAD_SHA")
    base = os.environ.get("BASE_SHA", "")
    adaptador = ComentariosGh(repo, pr)
    sticky = sticky_from_comments(
        adaptador.leer(), os.environ.get("BOT_LOGIN") or "github-actions[bot]"
    )
    current = _estado_actual(sticky)
    if current is None:
        sys.exit(
            "ai-review: memoria inválida o de versión futura; "
            "no se ejecuta la solicitud"
        )
    solicitud = next(
        (
            r
            for r in current.pending_requests
            if r.id == int(args.request_id)
            and r.state in ("pending", "failed_retryable")
        ),
        None,
    )
    if solicitud is None:
        sys.exit(f"ai-review: la solicitud {args.request_id!r} no está pendiente")
    policy = replace(
        politica_de_revision(),
        diff_max_bytes=int(os.environ.get("MAX_DIFF_BYTES", "1500000")),
        strict_budget=False,
    )
    previo = prev_de_memoria(sticky["body"] if sticky else None)
    paquete = {
        "request_id": solicitud.id,
        "kind": solicitud.kind,
        "finding_id": solicitud.finding_id,
        "run_id": _run_id(),
        "attempt": int(os.environ.get("GITHUB_RUN_ATTEMPT") or 0),
        "pr_head_sha": head,
        "base_sha": base,
        "policy_digest": digest_de_politica(policy),
        "finding_identity": politica_de_identidad(),
        "target": dict(solicitud.target),
        "observaciones": [],
        "cobertura": review_domain.UNKNOWN,
    }
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    destino = work / "request-package.json"
    destino.write_text(json.dumps(paquete))
    # La misma memoria autenticada que autorizó la solicitud; los comandos ya
    # los aplicó el coordinador hasta su cursor.
    (work / "prev.json").write_text(json.dumps(previo))
    print(
        f"ai-review: paquete de la solicitud {solicitud.id} preparado "
        f"(run {paquete['run_id']} attempt {paquete['attempt']})"
    )


def cmd_close_result(args):
    work = Path(args.work)
    paquete = json.loads((work / "request-package.json").read_text())
    resultado = json.loads((work / "result.json").read_text())
    secretos = [
        os.environ.get(n, "")
        for n in ("API_KEY", "FALLBACK_API_KEY", "GH_TOKEN", "GITHUB_TOKEN")
    ]
    texto = redact(resultado.get("result") or "", secretos)
    # El directorio de trabajo se sube completo como artifact: las salidas
    # crudas de las vueltas extra llevan el mismo borrado que la respuesta.
    for cruda in ("repair.json", "cierre.json"):
        ruta = work / cruda
        if ruta.exists():
            ruta.write_text(redact(ruta.read_text(), secretos))
    review_github.avisar_bloque_sin_cierre(texto)
    # La identidad viaja en el paquete desde la solicitud del coordinador.
    identidad = paquete.get("finding_identity") or politica_de_identidad()
    if identidad not in IDENTIDADES:
        sys.exit(f"ai-review: identidad de hallazgos '{identidad}' no admitida")
    modelo = parse_model_findings(texto, conservar_anclas=identidad == "anchors")
    model_ok = modelo_utilizable(parse_model_findings(texto, conservar_anclas=True))
    manifiesto = work / "manifest.json"
    datos = json.loads(manifiesto.read_text()) if manifiesto.exists() else None
    cobertura = cobertura_declarada(texto, modelo)
    if datos is not None and continuacion_terminal(datos):
        # Continuación terminal: el libro ya cubre el alcance y no queda nada
        # por leer; sin esto una pasada sin archivos degradaría a parcial.
        cobertura = review_domain.COMPLETE_CLAIM
    if modelo is not None and not model_ok:
        cobertura = review_domain.PARTIAL
    if (
        resultado.get("subtype") == "error_max_turns"
        and cobertura == review_domain.COMPLETE_CLAIM
    ):
        # El coordinador no mira el subtype: la vuelta de cierre no completa
        # una revisión que agotó el tope, la declare como la declare.
        cobertura = review_domain.PARTIAL
    paquete.update(
        {
            "result": texto,
            "subtype": resultado.get("subtype"),
            "observaciones": (modelo or {}).get("findings", []),
            "cobertura": cobertura,
        }
    )
    for campo in ("total_cost_usd", "usage", "num_turns"):
        if campo in resultado:
            paquete[campo] = resultado[campo]
    paquete["review_provider"] = resultado.get("review_provider") or get_provider()[0]
    paquete["model_ok"] = model_ok
    if datos is not None:
        paquete["manifest"] = {k: datos[k] for k in CAMPOS_DEL_ALCANCE if k in datos}
        if datos.get("head"):
            previo = (
                json.loads((work / "prev.json").read_text())
                if (work / "prev.json").exists()
                else {}
            ).get("covered") or {}
            paquete["covered"] = libro_fusionado(
                previo, datos, resultado, GitRepository(Path.cwd()), model_ok
            )
    if ERROR_KEY in resultado:
        paquete[ERROR_KEY] = redact(resultado[ERROR_KEY], secretos)
    (work / "result.json").write_text(json.dumps(paquete))
    review_github.informar_revision(ERROR_KEY not in paquete)
    resumen = os.environ.get("GITHUB_STEP_SUMMARY")
    if ERROR_KEY in paquete and resumen:
        with open(resumen, "a") as fh:
            aviso = caution_banner(
                paquete[ERROR_KEY], paquete.get("pr_head_sha") or "", has_previous=False
            )
            fh.write(aviso + "\n")


# Lo que el comentario visible necesita del manifest del worker; el diff y el
# contexto se quedan en el worker.
CAMPOS_DEL_ALCANCE = (
    "reviewed",
    "excluded",
    "max_turns",
    "mode",
    "prev_sha",
    "changed_files",
)


def revision_visible(artifact, snapshot, previos, head):
    """Arma el comentario del resultado del worker con el mismo render que el
    revisor directo. Un fallo no marca el SHA como revisado: antepone el aviso
    a la revisión anterior."""

    def armar(bloque, resto):
        if ERROR_KEY in artifact:
            banner = caution_banner(artifact[ERROR_KEY], head, has_previous=bool(resto))
            return cuerpo_con_cabecera(
                bloque, insert_caution_banner(resto, banner).rstrip()
            )
        proveedor = artifact.get("review_provider")
        if proveedor not in PROVIDERS:
            proveedor = get_provider()[0]
        manifest = {"reviewed": [], "excluded": [], **(artifact.get("manifest") or {})}
        model_ok = artifact.get("model_ok", True) and forma_utilizable_del_artifact(
            artifact
        )
        libro = artifact.get("covered")
        if not model_ok and libro is not None:
            previo = libro_de(resto)[1] or {}
            libro = {ruta: blob for ruta, blob in libro.items() if previo.get(ruta) == blob}
        return compose(
            artifact,
            manifest,
            sha=head,
            provider=proveedor,
            findings={
                "merged": review_domain.hallazgos_legacy(snapshot),
                "new_ids": [f.id for f in snapshot.findings if f.id not in previos],
                "block": bloque,
                "model_ok": model_ok,
                "completion": snapshot.completion,
                "no_acreditados": review_domain.resoluciones_sin_acreditar(snapshot),
            },
            libro=libro,
            budget=review_domain.StorageBudget(),
        )

    return armar


TITULO_DE_REVISION = "### Revisión automática · "
EXPLICACION_PREFIX = "## Explicación de "
EXPLICACION_MAX = 8000
EXPLICACIONES_VISIBLES = 3
AVISO_COMANDO_PREFIX = aviso_md("Comando no entendido", "")
AVISOS_VISIBLES = 3
AVISO_EN_CURSO_PREFIX = aviso_md("Revisión en curso", "")
AVISO_SIN_REVISION_PREFIX = aviso_md("Sin revisión publicada", "")
LINEA_SIN_REVISION = (
    f"{AVISO_SIN_REVISION_PREFIX}este PR no tiene una revisión pendiente ni "
    "publicada; la pide `ai-review: revisar`, o un push cuando el PR está "
    "abierto y listo para revisión."
)


def linea_en_curso(snapshot):
    """La línea visible de la revisión pendiente, para que un sticky sin
    revisión publicada nunca quede vacío; None sin revisión pendiente."""
    pendiente = next(
        (
            r
            for r in reversed(snapshot.pending_requests)
            if r.kind == "review" and r.state == "pending"
        ),
        None,
    )
    if pendiente is None:
        return None
    head = pendiente.target.get("head_sha", "")
    commit = f"el commit `{head[:7]}`" if sha_hexa(head) else "el PR"
    return (
        f"{AVISO_EN_CURSO_PREFIX}el revisor está revisando {commit}; este "
        "comentario se actualiza cuando termine."
    )


def es_aviso_de_comando(linea):
    """Solo la línea exacta que escribe el programa para un comentario real se
    conserva entre re-renders; cualquier otra que imite el prefijo se descarta."""
    m = re.search(r"el comentario (\d{1,19}) ", linea)
    return bool(m) and any(
        linea == aviso(int(m.group(1))) for aviso in AVISOS_DE_RECHAZO.values()
    )


def aviso_de_comando_no_entendido(cid):
    return (
        f"{AVISO_COMANDO_PREFIX}el comentario {cid} empieza con `ai-review:` pero no "
        "es un comando que entienda; usa `ai-review: descartar F3`, "
        "`ai-review: descartar todo`, `ai-review: explicar F3` o `ai-review: revisar`."
    )


def aviso_de_todo_tardio(cid):
    return (
        f"{aviso_md('Comando no aplicado', '')}`ai-review: descartar todo` del "
        f"comentario {cid} es de antes de la última revisión con hallazgos nuevos y "
        "no se aplicó; si también quieres descartarlos, comenta de nuevo "
        "`ai-review: descartar todo`."
    )


AVISOS_DE_RECHAZO = {
    review_domain.RECHAZO_NO_ENTENDIDO: aviso_de_comando_no_entendido,
    review_domain.RECHAZO_TODO_TARDIO: aviso_de_todo_tardio,
}


def texto_de_explicacion(artifact):
    """Prosa de una explicación del worker, sin cobertura, bloque ni veredicto.
    Además de neutralizar_marcadores, no puede imitar las secciones que el
    re-render busca (encabezados bajan a ####, también con sangría) ni los
    avisos del programa (una cita `> **Comando no entendido:**` deja de serlo)."""
    texto, _, _ = split_coverage(artifact.get("result") or "")
    texto = strip_model_verdict(strip_model_findings_block(texto)).strip()
    texto = re.sub(r"^[ \t]{0,3}#{1,6}[ \t]+", "#### ", texto, flags=re.M)
    texto = neutralizar_marcadores(prosa_del_modelo(texto))
    if len(texto) > EXPLICACION_MAX:
        texto = texto[:EXPLICACION_MAX] + "\n\n_(Explicación recortada.)_"
    return texto or "_El revisor no devolvió texto._"


def estado_visible(snapshot, explicacion=None, avisos=()):
    """Re-render del comentario cuando un comando o una explicación cambian la
    memoria sin una revisión nueva: veredicto y secciones salen del snapshot;
    los marcadores sha=/completion=, los avisos, el título y el detalle del
    revisor se conservan (nada de esto acredita un SHA). explicacion es
    (finding_id, texto). avisos son líneas de comandos que no se entendieron:
    se ven tras las secciones y se conservan los últimos en los re-renders. Un
    cuerpo sin la forma de una revisión conserva el resto tal cual."""
    merged = review_domain.hallazgos_legacy(snapshot)
    titulos = {f["id"]: f["title"] for f in merged}
    nueva = []
    if explicacion is not None:
        fid, texto = explicacion
        titulo = html.escape(titulos.get(fid, ""), quote=False)
        nueva = [
            f"{EXPLICACION_PREFIX}{fid}" + (f" · {titulo}" if titulo else ""),
            "",
            texto,
        ]

    def armar(bloque, resto):
        lineas = resto.split("\n") if resto else []
        titulo = next(
            (
                i
                for i, linea in enumerate(lineas)
                if linea.startswith(TITULO_DE_REVISION)
            ),
            None,
        )
        detalle = next(
            (i for i, linea in enumerate(lineas) if linea == DETALLE_DEL_REVISOR), None
        )
        veredicto = (
            next(
                (i for i in range(titulo, detalle) if VERDICT_RE.match(lineas[i])),
                None,
            )
            if titulo is not None and detalle is not None and titulo < detalle
            else None
        )
        if veredicto is None:
            cuerpo = cuerpo_con_cabecera(bloque, resto)
            en_curso = linea_en_curso(snapshot)
            return "\n".join(
                [
                    cuerpo,
                    *(["", en_curso] if en_curso else []),
                    *(["", *avisos] if avisos else []),
                    *(["", *nueva] if nueva else []),
                ]
            )[:GITHUB_COMMENT_MAX]
        marcas = (SHA_PREFIX, COMPLETION_PREFIX, COVERED_PREFIX)
        # Los avisos de un cuerpo viejo citan motivos y detalle de cobertura del
        # modelo publicados antes de neutralizarse.
        previo = [
            neutralizar_marcadores(linea)
            for linea in lineas[:titulo]
            if not linea.startswith(marcas)
        ]
        while previo and not previo[0]:
            previo.pop(0)
        while previo and not previo[-1]:
            previo.pop()
        region = lineas[veredicto:detalle]
        nuevos = []
        if "## Nuevos en este push" in region and "## Siguen abiertos" in region:
            tramo = region[
                region.index("## Nuevos en este push") : region.index(
                    "## Siguen abiertos"
                )
            ]
            nuevos = [
                m.group(1) for linea in tramo if (m := re.search(r" · (\S+)$", linea))
            ]
        explicaciones = []
        previos_avisos = []
        for linea in region:
            if linea.startswith(EXPLICACION_PREFIX):
                explicaciones.append([linea])
            elif explicaciones:
                explicaciones[-1].append(linea)
            elif es_aviso_de_comando(linea):
                previos_avisos.append(linea)
        todos_avisos = [a for a in previos_avisos if a not in avisos] + list(avisos)
        if nueva:
            explicaciones = [
                e
                for e in explicaciones
                if e[0].split(" · ")[0] != nueva[0].split(" · ")[0]
            ]
            explicaciones.append(nueva)
        conservadas = [linea for linea in lineas[:titulo] if linea.startswith(marcas)]
        # Con un aviso previo, la forma de revision_visible ante un fallo: el
        # aviso tras la cabecera, para que el próximo fallo lo reemplace.
        cabecera = [MARKER, *conservadas, bloque, *previo, *([""] if previo else [])]
        cuerpo = [
            *cabecera,
            lineas[titulo],
            *(
                neutralizar_marcadores(linea)
                for linea in lineas[titulo + 1 : veredicto]
            ),
            verdict_for(merged),
            "",
            *sections_for(
                merged, nuevos, review_domain.resoluciones_sin_acreditar(snapshot)
            ),
        ]
        for aviso in todos_avisos[-AVISOS_VISIBLES:]:
            cuerpo += ["", aviso]
        for e in explicaciones[-EXPLICACIONES_VISIBLES:]:
            while e and not e[-1]:
                e = e[:-1]
            cuerpo += ["", *e]
        # La prosa del detalle de un cuerpo publicado antes de neutralizarse
        # tampoco puede llevar marcadores vivos al cuerpo nuevo. El alcance real
        # es el último: la prosa vieja puede traer una línea idéntica.
        alcance = max(
            (i for i in range(detalle, len(lineas)) if lineas[i] == ALCANCE_INICIO),
            default=len(lineas),
        )
        # Dentro del Alcance las rutas salen del PR: solo su <details> de apertura
        # y su </details> de cierre quedan vivos.
        cierre = max(
            (i for i in range(alcance, len(lineas)) if lineas[i] == "</details>"),
            default=None,
        )
        if alcance < len(lineas) and cierre is not None:
            cola = [
                lineas[alcance],
                *(
                    neutralizar_marcadores(linea)
                    for linea in lineas[alcance + 1 : cierre]
                ),
                lineas[cierre],
                *(neutralizar_marcadores(linea) for linea in lineas[cierre + 1 :]),
            ]
        else:
            cola = [neutralizar_marcadores(linea) for linea in lineas[alcance:]]
        cuerpo += [
            "",
            lineas[detalle],
            *(neutralizar_marcadores(linea) for linea in lineas[detalle + 1 : alcance]),
            *cola,
        ]
        return "\n".join(cuerpo)[:GITHUB_COMMENT_MAX]

    return armar


if __name__ == "__main__":
    main()
