#!/usr/bin/env python3
"""Glue for the AI PR review action."""

import argparse
from dataclasses import replace
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from review_commands import dismiss_targets
from review_config import (
    ERROR_KEY,
    env,
    get_provider,
    politica_de_revision,
)
import review_coordinator
import review_domain
import review_github
import review_run
from review_render import (
    caution_banner,
    compose,
    cuerpo_publicable,
    insert_caution_banner,
    redact,
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
# (never after: leer_cierre drops everything behind it, and the 50k/65k trims cut
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
        "reconcile": review_coordinator.cmd_reconcile,
        "execute-request": review_coordinator.cmd_execute_request,
        "close-result": review_coordinator.cmd_close_result,
    }[args.command](args)


if __name__ == "__main__":
    main()
