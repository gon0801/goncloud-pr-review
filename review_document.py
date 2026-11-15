"""Codec del documento publicado; el estado y la vista se validan juntos."""

import hashlib
import json
import math
import re
from dataclasses import dataclass, replace

import review_domain as domain
from review_config import PROVIDERS
from review_render import (
    estimate_cost,
    neutralizar_marcadores,
    prosa_del_modelo,
    redact,
    render_document,
    strip_model_verdict,
)
from review_sticky import (
    COMPLETION_PREFIX,
    COVERED_PREFIX,
    MARKER,
    SHA_PREFIX,
    libro_a_linea,
    libro_de_linea,
    reviewed_completion,
    reviewed_sha,
)

VIEW_PREFIX = "<!-- ai-review:view="
SLOT_PREFIX = "<!-- ai-review:slot="
END_SLOT_PREFIX = "<!-- ai-review:end-slot="
SUFFIX = " -->"
RESERVED = re.compile(r"<!--\s*ai-review:(?:view|slot|end-slot)\b")
SLOT_MARKER = re.compile(r"(?m)^<!-- ai-review:(slot|end-slot)=([a-zA-Z0-9-]+) -->$")


def _valid_sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) is not None


def _valid_id(value):
    return isinstance(value, str) and re.fullmatch(r"F[1-9][0-9]*", value) is not None


def _valid_code(value):
    return isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value) is not None


@dataclass(frozen=True)
class SafeProse:
    text: str


def safe_prose(text, secrets=()):
    clean = str(text).encode("utf-8", "replace").decode("utf-8")
    return SafeProse(neutralizar_marcadores(prosa_del_modelo(redact(
        clean.replace("\r\n", "\n").replace("\r", "\n"), secrets,
    ))))


@dataclass(frozen=True)
class ReviewedHeader:
    head_sha: str
    completion: str


@dataclass(frozen=True)
class CoverageBook:
    head_sha: str
    entries: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class ScopeUsage:
    turns: int | None
    max_turns: int | None
    input_tokens: int
    cache_tokens: int
    output_tokens: int
    cost_usd: float | None


@dataclass(frozen=True)
class ScopeView:
    reviewed_count: int
    excluded_count: int
    diff_bytes: int
    mode: str
    exclusions: tuple[str, ...] = ()
    prev_sha: str | None = None
    changed_count: int | None = None
    usage: ScopeUsage | None = None


@dataclass(frozen=True)
class ReviewWarning:
    code: str
    count: int | None = None


@dataclass(frozen=True)
class ReviewView:
    head_sha: str
    provider: str
    new_ids: tuple[str, ...]
    scope: ScopeView
    warnings: tuple[ReviewWarning, ...]
    prose: SafeProse


@dataclass(frozen=True)
class ExplanationView:
    finding_id: str
    prose: SafeProse


@dataclass(frozen=True)
class Notice:
    kind: str
    finding_ids: tuple[str, ...] = ()
    comment_id: int | None = None


@dataclass(frozen=True)
class FailureView:
    head_sha: str
    code: str
    prose: SafeProse


@dataclass(frozen=True)
class ReviewDocument:
    memory: domain.Snapshot
    reviewed: ReviewedHeader | None
    covered: CoverageBook | None
    review: ReviewView | None
    explanations: tuple[ExplanationView, ...]
    notices: tuple[Notice, ...]
    failure: FailureView | None
    historical: SafeProse | None


@dataclass(frozen=True)
class PreparedPublication:
    body: str
    state_block: str
    body_digest: str
    generation: int
    summary: str = ""


def document_from_result(previous, snapshot, result, manifest, provider, head, new_ids, covered=None):
    cierre = domain.leer_cierre(result.get("result") or "")
    prose = domain.strip_model_findings_block(cierre.prosa)
    if snapshot.findings:
        prose = strip_model_verdict(prose)
    prose = prosa_del_modelo(prose)
    if cierre.cobertura == "partial" and cierre.detalle:
        prose = f"{prose}\n\nCobertura declarada por el revisor: parcial | {cierre.detalle}"
    excluded = manifest.get("excluded") or ()
    usage_raw = result.get("usage") or {}
    usage = None
    if usage_raw and all(
        type(usage_raw.get(key, 0)) is int and usage_raw.get(key, 0) >= 0
        for key in (
            "input_tokens", "cache_creation_input_tokens",
            "cache_read_input_tokens", "output_tokens",
        )
    ):
        turns = result.get("num_turns")
        cap = manifest.get("max_turns")
        usage = ScopeUsage(
            turns=turns if type(turns) is int and turns >= 0 else None,
            max_turns=cap if type(cap) is int and cap >= 0 else None,
            input_tokens=usage_raw.get("input_tokens", 0)
            + usage_raw.get("cache_creation_input_tokens", 0),
            cache_tokens=usage_raw.get("cache_read_input_tokens", 0),
            output_tokens=usage_raw.get("output_tokens", 0),
            cost_usd=estimate_cost(PROVIDERS[provider]["prices"], usage_raw),
        )
    scope = ScopeView(
        reviewed_count=len(manifest.get("reviewed") or ()),
        excluded_count=len(excluded),
        diff_bytes=manifest.get("diff_bytes") or 0,
        mode=manifest.get("mode") or "full",
        exclusions=tuple(
            f"`{item['path'][:200]}` ({item['reason']})" for item in excluded[:40]
        ),
        prev_sha=manifest.get("prev_sha") if manifest.get("mode") == "incremental" else None,
        changed_count=(
            len(manifest["changed_files"]) if manifest.get("changed_files") is not None
            else None
        ),
        usage=usage,
    )
    warnings = []
    if result.get("subtype") == "error_max_turns":
        warnings.append(ReviewWarning("max-turns"))
    if cierre.cobertura is None and scope.reviewed_count:
        warnings.append(ReviewWarning("missing-coverage"))
    elif cierre.cobertura == "partial":
        warnings.append(ReviewWarning("partial-coverage"))
    for reason, kind in (
        ("budget", "budget-exclusions"),
        ("encoding", "encoding-exclusions"),
    ):
        count = sum(entry["reason"] == reason for entry in excluded)
        if count:
            warnings.append(ReviewWarning(kind, count))
    if not result.get("model_ok", True) and scope.reviewed_count:
        warnings.append(ReviewWarning("invalid-findings"))
    if not prose:
        if not scope.reviewed_count:
            prose = (
                "No hubo archivos revisables en este push; los hallazgos anteriores se conservan."
                if snapshot.findings
                else "No hay archivos revisables en este PR (todo quedó excluido por filtro)."
            )
        elif snapshot.findings and result.get("model_ok", True):
            prose = "Sin hallazgos nuevos en este push."
        else:
            prose = "_El revisor no devolvió texto._"
    return replace(
        previous,
        memory=snapshot,
        reviewed=ReviewedHeader(head, domain.marker_of(snapshot.completion)),
        covered=CoverageBook(head, tuple(covered.items())) if covered is not None else None,
        review=ReviewView(head, provider, tuple(new_ids), scope, tuple(warnings), SafeProse(prose)),
        failure=None,
        historical=previous.historical if not result.get("model_ok", True) else None,
        notices=tuple(
            notice for notice in previous.notices
            if notice.kind != "capacity-exceeded" or not result.get("model_ok", True)
        ),
    )


def update_presentation(document, snapshot, explanations=(), notices=()):
    entries = list(document.explanations)
    for finding_id, prose in explanations:
        entries = [entry for entry in entries if entry.finding_id != finding_id]
        entries.append(ExplanationView(finding_id, SafeProse(prose)))
    visible = list(document.notices)
    for notice in notices:
        visible = [
            entry for entry in visible
            if entry != notice and (
                entry.comment_id is None or entry.comment_id != notice.comment_id
            )
        ]
        visible.append(notice)
    return replace(
        document,
        memory=snapshot,
        explanations=tuple(entries[-3:]),
        notices=tuple(visible[-3:]),
    )


@dataclass(frozen=True)
class DocumentBlocked:
    reason: str
    original_body: str


def has_document_marker(body):
    return bool(RESERVED.search(body or ""))


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _json(value):
    return json.dumps(value, ensure_ascii=True, separators=(",", ":")).replace(
        "<!--", "\\u003c\\u0021\\u002d\\u002d"
    ).replace("-->", "\\u002d\\u002d\\u003e").replace("--!>", "--!\\u003e")


def _render(document, block, *, visible_only=False):
    state = domain.read_snapshot(block)
    if not isinstance(state, domain.Valid):
        return DocumentBlocked("estado codificado ilegible", block)
    memory = state.snapshot
    if document.reviewed is not None and (
        not _valid_sha(document.reviewed.head_sha)
        or document.reviewed.completion not in ("complete", "partial")
    ):
        return DocumentBlocked("cabecera inválida", "")
    if document.review and (
        document.reviewed is None or document.review.head_sha != document.reviewed.head_sha
    ):
        return DocumentBlocked("revisión y cabecera no coinciden", "")
    if document.review and (
        not _valid_sha(document.review.head_sha)
        or len(set(document.review.new_ids)) != len(document.review.new_ids)
        or any(not _valid_id(fid) or fid not in {f.id for f in memory.findings} for fid in document.review.new_ids)
        or (document.review.scope.prev_sha is not None
            and not _valid_sha(document.review.scope.prev_sha))
        or any(
            not _valid_code(warning.code)
            or (
                warning.count is not None
                and (type(warning.count) is not int or warning.count < 1)
            )
            for warning in document.review.warnings
        )
    ):
        return DocumentBlocked("metadatos de revisión inválidos", "")
    if any(
        not _valid_id(item.finding_id)
        or item.finding_id not in {f.id for f in memory.findings}
        for item in document.explanations
    ) or any(
        not _valid_code(item.kind) or any(not _valid_id(fid) for fid in item.finding_ids)
        for item in document.notices
    ):
        return DocumentBlocked("datos de presentación inválidos", "")
    if document.failure and (
        not _valid_sha(document.failure.head_sha)
        or not _valid_code(document.failure.code)
    ):
        return DocumentBlocked("fallo inválido", "")
    lines = [] if visible_only else [MARKER]
    if document.reviewed and not visible_only:
        lines.extend([
            f"{SHA_PREFIX}{document.reviewed.head_sha} -->",
            f"{COMPLETION_PREFIX}{document.reviewed.head_sha}:{document.reviewed.completion} -->",
        ])
    covered_line = None
    if document.covered:
        if not document.reviewed or document.covered.head_sha != document.reviewed.head_sha:
            return DocumentBlocked("libro y cabecera no coinciden", "")
        covered_line = libro_a_linea(document.covered.head_sha, dict(document.covered.entries))
        if covered_line is None:
            return DocumentBlocked("libro inválido", "")
        if not visible_only:
            lines.append(covered_line)
    if not visible_only:
        lines.append(block)
    slots = []
    if document.review:
        slots.append(("review", document.review.prose.text))
    slots.extend(
        (f"explanation-{entry.finding_id}", entry.prose.text)
        for entry in document.explanations
    )
    if document.failure:
        slots.append(("failure", document.failure.prose.text))
    if document.historical is not None:
        slots.append(("history", document.historical.text))
    if len({name for name, _ in slots}) != len(slots):
        return DocumentBlocked("slots repetidos", "")
    review = document.review
    header = {
        "version": 1,
        "state_sha256": _digest(block),
        "reviewed": (
            {"head_sha": document.reviewed.head_sha, "completion": document.reviewed.completion}
            if document.reviewed else None
        ),
        "covered_sha256": _digest(covered_line) if covered_line else None,
        "review": (
            {
                "head_sha": review.head_sha,
                "provider": review.provider,
                "new_ids": list(review.new_ids),
                "scope": {
                    "reviewed_count": review.scope.reviewed_count,
                    "excluded_count": review.scope.excluded_count,
                    "diff_bytes": review.scope.diff_bytes,
                    "mode": review.scope.mode,
                    "exclusions": list(review.scope.exclusions),
                    **({"prev_sha": review.scope.prev_sha} if review.scope.prev_sha is not None else {}),
                    **({"changed_count": review.scope.changed_count} if review.scope.changed_count is not None else {}),
                    **({"usage": {
                        "turns": review.scope.usage.turns,
                        "max_turns": review.scope.usage.max_turns,
                        "input_tokens": review.scope.usage.input_tokens,
                        "cache_tokens": review.scope.usage.cache_tokens,
                        "output_tokens": review.scope.usage.output_tokens,
                        "cost_usd": review.scope.usage.cost_usd,
                    }} if review.scope.usage else {}),
                },
                "warnings": [
                    warning.code if warning.count is None
                    else {"code": warning.code, "count": warning.count}
                    for warning in review.warnings
                ],
            } if review else None
        ),
        "explanations": [entry.finding_id for entry in document.explanations],
        "notices": [
            {
                "kind": entry.kind,
                "finding_ids": list(entry.finding_ids),
                **({"comment_id": entry.comment_id} if entry.comment_id is not None else {}),
            }
            for entry in document.notices
        ],
        "failure": (
            {"head_sha": document.failure.head_sha, "code": document.failure.code}
            if document.failure else None
        ),
        "slots": [{"id": name, "sha256": _digest(text)} for name, text in slots],
    }
    if not visible_only:
        lines.append(f"{VIEW_PREFIX}{_json(header)}{SUFFIX}")
    contents = [
        (name, text if visible_only else
         f"{SLOT_PREFIX}{name}{SUFFIX}\n{text}\n{END_SLOT_PREFIX}{name}{SUFFIX}")
        for name, text in slots
    ]
    lines.extend(render_document(document, memory, contents))
    return "\n".join(lines)


def _capacity(body, budget):
    return (
        len(body.encode("utf-8")) <= budget.comment_max_bytes
        and len(body) <= budget.comment_max_chars
    )


def _fit_slot(text, candidate, block, budget):
    best = candidate(0)
    low, high = 0, len(text)
    while low <= high:
        middle = (low + high) // 2
        attempt = candidate(middle)
        rendered = _render(attempt, block)
        if isinstance(rendered, str) and _capacity(rendered, budget):
            best = attempt
            low = middle + 1
        else:
            high = middle - 1
    return best


def _with_notice(document, kind):
    if document.review and kind in (
        "book-omitted", "history-truncated", "explanation-truncated", "failure-truncated",
    ):
        warning = ReviewWarning(kind)
        if warning in document.review.warnings:
            return document
        return replace(
            document,
            review=replace(document.review, warnings=document.review.warnings + (warning,)),
        )
    notice = Notice(kind)
    if notice in document.notices:
        return document
    if len(document.notices) >= 3:
        return replace(document, notices=document.notices[-2:] + (notice,))
    return replace(document, notices=document.notices + (notice,))


def prepare_publication(document, budget=None, secrets=()):
    budget = budget or domain.StorageBudget()
    snapshot = domain.snapshot_a_v3(document.memory)
    encoded = domain.encode_snapshot(snapshot, budget=budget)
    if isinstance(encoded, domain.CapacityExceeded):
        return encoded
    block = encoded.block
    if secrets:
        source = json.loads(block[len(domain.FINDINGS_PREFIX):-len(domain.FINDINGS_SUFFIX)])

        def clean(value):
            if isinstance(value, str):
                return redact(value, secrets)
            if isinstance(value, list):
                return [clean(v) for v in value]
            if isinstance(value, dict):
                return {k: clean(v) for k, v in value.items()}
            return value

        sanitized = domain.read_snapshot(domain.FINDINGS_PREFIX + _json(clean(source)) + domain.FINDINGS_SUFFIX)
        if not isinstance(sanitized, domain.Valid):
            return DocumentBlocked("el estado redactado no se puede validar", "")
        encoded = domain.encode_snapshot(sanitized.snapshot, budget=budget)
        if isinstance(encoded, domain.CapacityExceeded):
            return encoded
        block = encoded.block
    omit_book = bool(
        document.covered and secrets and any(
            redact(path, secrets) != path for path, _ in document.covered.entries
        )
    )
    current = replace(
        document,
        covered=None if omit_book else document.covered,
        review=replace(
            document.review,
            provider=safe_prose(document.review.provider, secrets).text,
            scope=replace(
                document.review.scope,
                exclusions=tuple(
                    safe_prose(path, secrets).text
                    for path in document.review.scope.exclusions
                ),
            ),
            prose=safe_prose(document.review.prose.text, secrets),
        ) if document.review else None,
        explanations=tuple(replace(entry, prose=safe_prose(entry.prose.text, secrets)) for entry in document.explanations),
        historical=safe_prose(document.historical.text, secrets) if document.historical else None,
        failure=replace(
            document.failure, prose=safe_prose(document.failure.prose.text, secrets)
        ) if document.failure else None,
    )
    if omit_book:
        current = _with_notice(current, "book-omitted")
        if isinstance(current, domain.CapacityExceeded):
            return current
    if len(current.explanations) > 3 or len(current.notices) > 3:
        return DocumentBlocked("demasiadas explicaciones o avisos", "")
    capped = tuple(
        replace(entry, prose=SafeProse(entry.prose.text[:7999] + "…"))
        if len(entry.prose.text) > 8000 else entry
        for entry in current.explanations
    )
    if capped != current.explanations:
        current = _with_notice(current, "explanation-truncated")
        if isinstance(current, domain.CapacityExceeded):
            return current
        current = replace(current, explanations=capped)
    if current.covered and libro_a_linea(current.covered.head_sha, dict(current.covered.entries)) is None:
        current = _with_notice(current, "book-omitted")
        if isinstance(current, domain.CapacityExceeded):
            return current
        current = replace(current, covered=None)
    body = _render(current, block)
    if isinstance(body, DocumentBlocked):
        return body
    if not _capacity(body, budget) and current.covered:
        fixed = replace(
            current,
            review=replace(current.review, prose=SafeProse("")) if current.review else None,
            historical=SafeProse("") if current.historical else None,
            explanations=tuple(
                replace(entry, prose=SafeProse("")) for entry in current.explanations
            ),
            failure=replace(current.failure, prose=SafeProse("")) if current.failure else None,
        )
        if not _capacity(_render(fixed, block), budget):
            current = _with_notice(current, "book-omitted")
            if isinstance(current, domain.CapacityExceeded):
                return current
            current = replace(current, covered=None)
            body = _render(current, block)
    if not _capacity(body, budget) and current.review and current.review.prose.text:
        text = current.review.prose.text
        warnings = current.review.warnings
        if ReviewWarning("prose-truncated") not in warnings:
            warnings += (ReviewWarning("prose-truncated"),)
        current = _fit_slot(
            text,
            lambda n: replace(
                current,
                review=replace(
                    current.review,
                    warnings=warnings,
                    prose=SafeProse(text[:n].rstrip() + ("…" if n < len(text) else "")),
                ),
            ),
            block, budget,
        )
        body = _render(current, block)
    if not _capacity(body, budget) and current.historical and current.historical.text:
        text = current.historical.text
        current = _with_notice(current, "history-truncated")
        if isinstance(current, domain.CapacityExceeded):
            return current
        current = _fit_slot(
            text,
            lambda n: replace(
                current,
                historical=SafeProse(text[:n].rstrip() + ("…" if n < len(text) else "")),
            ),
            block, budget,
        )
        body = _render(current, block)
    if not _capacity(body, budget) and current.covered:
        current = _with_notice(current, "book-omitted")
        if isinstance(current, domain.CapacityExceeded):
            return current
        current = replace(current, covered=None)
        body = _render(current, block)
    for i, entry in enumerate(current.explanations):
        if _capacity(body, budget):
            break
        text = entry.prose.text
        if not text:
            continue
        current = _with_notice(current, "explanation-truncated")
        if isinstance(current, domain.CapacityExceeded):
            return current

        def shortened(n, index=i, base=current, source=text):
            explanations = list(base.explanations)
            explanations[index] = replace(
                explanations[index],
                prose=SafeProse(source[:n].rstrip() + ("…" if n < len(source) else "")),
            )
            return replace(base, explanations=tuple(explanations))

        current = _fit_slot(
            text, shortened, block, budget,
        )
        body = _render(current, block)
    if not _capacity(body, budget) and current.failure and current.failure.prose.text:
        text = current.failure.prose.text
        current = _with_notice(current, "failure-truncated")
        if isinstance(current, domain.CapacityExceeded):
            return current
        current = _fit_slot(
            text,
            lambda n: replace(
                current,
                failure=replace(
                    current.failure,
                    prose=SafeProse(text[:n].rstrip() + ("…" if n < len(text) else "")),
                ),
            ),
            block, budget,
        )
        body = _render(current, block)
    if not _capacity(body, budget):
        return domain.CapacityExceeded(
            min(budget.comment_max_bytes, budget.comment_max_chars),
            max(len(body.encode("utf-8")), len(body)),
        )
    decoded = read_document(body)
    if isinstance(decoded, DocumentBlocked):
        return DocumentBlocked(f"documento producido inválido: {decoded.reason}", body)
    return PreparedPublication(
        body, block, _digest(body), domain.read_snapshot(block).snapshot.generation,
        _render(current, block, visible_only=True),
    )


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("clave duplicada")
        result[key] = value
    return result


def _decode_warning(value):
    if isinstance(value, str):
        code, count = value, None
    elif isinstance(value, dict) and set(value) == {"code", "count"}:
        code, count = value["code"], value["count"]
    else:
        raise ValueError("aviso de revisión inválido")
    if not _valid_code(code) or (count is not None and (type(count) is not int or count < 1)):
        raise ValueError("aviso de revisión inválido")
    return ReviewWarning(code, count)


def _metadata(data, slots):
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("versión de vista no compatible")
    if set(data) != {
        "version", "state_sha256", "reviewed", "covered_sha256",
        "review", "explanations", "notices", "failure", "slots",
    }:
        raise ValueError("vista incompleta")
    if data["reviewed"] is not None:
        header = data["reviewed"]
        if (
            not isinstance(header, dict)
            or set(header) != {"head_sha", "completion"}
            or not isinstance(header["head_sha"], str)
            or not _valid_sha(header["head_sha"])
            or header["completion"] not in ("complete", "partial")
        ):
            raise ValueError("cabecera de vista inválida")
    reviewed = ReviewedHeader(**data["reviewed"]) if data["reviewed"] is not None else None
    review = data["review"]
    if review is not None:
        if not isinstance(review, dict) or set(review) != {
            "head_sha", "provider", "new_ids", "scope", "warnings",
        }:
            raise ValueError("revisión incompleta")
        scope = review["scope"]
        required = {
            "reviewed_count", "excluded_count", "diff_bytes", "mode", "exclusions",
        }
        if (
            not isinstance(scope, dict)
            or not required.issubset(scope)
            or not set(scope).issubset(required | {"prev_sha", "changed_count", "usage"})
        ):
            raise ValueError("alcance incompleto")
        if any(
            type(scope[key]) is not int or scope[key] < 0
            for key in ("reviewed_count", "excluded_count", "diff_bytes")
        ) or scope["mode"] not in ("full", "incremental", "continuation"):
            raise ValueError("alcance inválido")
        if (
            ("prev_sha" in scope and not _valid_sha(scope["prev_sha"]))
            or ("changed_count" in scope and (
                type(scope["changed_count"]) is not int or scope["changed_count"] < 0
            ))
        ):
            raise ValueError("delta inválido")
        if "usage" in scope:
            usage = scope["usage"]
            if (
                not isinstance(usage, dict)
                or set(usage) != {
                    "turns", "max_turns", "input_tokens",
                    "cache_tokens", "output_tokens", "cost_usd",
                }
                or any(
                    usage[key] is not None and (
                        type(usage[key]) is not int or usage[key] < 0
                    )
                    for key in ("turns", "max_turns")
                )
                or any(
                    type(usage[key]) is not int or usage[key] < 0
                    for key in ("input_tokens", "cache_tokens", "output_tokens")
                )
                or (
                    usage["cost_usd"] is not None
                    and (
                        type(usage["cost_usd"]) not in (float, int)
                        or not math.isfinite(usage["cost_usd"])
                        or usage["cost_usd"] < 0
                    )
                )
            ):
                raise ValueError("uso inválido")
        if (
            not isinstance(scope["exclusions"], list)
            or any(not isinstance(path, str) for path in scope["exclusions"])
            or not isinstance(review["provider"], str)
            or not review["provider"]
            or not isinstance(review["new_ids"], list)
            or len(set(review["new_ids"])) != len(review["new_ids"])
            or any(not _valid_id(fid) for fid in review["new_ids"])
            or not isinstance(review["warnings"], list)
            or not isinstance(review["head_sha"], str)
            or not _valid_sha(review["head_sha"])
        ):
            raise ValueError("metadatos de revisión inválidos")
    review = (
        ReviewView(
            review["head_sha"], review["provider"], tuple(review["new_ids"]),
            ScopeView(**{
                **review["scope"],
                "exclusions": tuple(review["scope"]["exclusions"]),
                **({"usage": ScopeUsage(**review["scope"]["usage"])} if "usage" in review["scope"] else {}),
            }),
            tuple(_decode_warning(value) for value in review["warnings"]),
            SafeProse(slots["review"]),
        ) if review is not None else None
    )
    if (
        not isinstance(data["explanations"], list)
        or len(data["explanations"]) > 3
        or len(set(data["explanations"])) != len(data["explanations"])
        or any(not _valid_id(fid) for fid in data["explanations"])
    ):
        raise ValueError("explicaciones inválidas")
    explanations = tuple(
        ExplanationView(fid, SafeProse(slots[f"explanation-{fid}"]))
        for fid in data["explanations"]
    )
    if not isinstance(data["notices"], list) or len(data["notices"]) > 3:
        raise ValueError("avisos inválidos")
    if any(
        not isinstance(entry, dict)
        or set(entry) not in (
            {"kind", "finding_ids"}, {"kind", "finding_ids", "comment_id"},
        )
        or not _valid_code(entry["kind"])
        or not isinstance(entry["finding_ids"], list)
        or any(not _valid_id(fid) for fid in entry["finding_ids"])
        or ("comment_id" in entry and (
            type(entry["comment_id"]) is not int or entry["comment_id"] < 0
        ))
        for entry in data["notices"]
    ):
        raise ValueError("avisos mal formados")
    notices = tuple(
        Notice(entry["kind"], tuple(entry["finding_ids"]), entry.get("comment_id"))
        for entry in data["notices"]
    )
    failure = None
    if data["failure"] is not None:
        metadata = data["failure"]
        if (
            not isinstance(metadata, dict)
            or set(metadata) != {"head_sha", "code"}
            or not isinstance(metadata["head_sha"], str)
            or not _valid_sha(metadata["head_sha"])
            or not _valid_code(metadata["code"])
        ):
            raise ValueError("fallo inválido")
        failure = FailureView(metadata["head_sha"], metadata["code"], SafeProse(slots["failure"]))
    historical = SafeProse(slots["history"]) if "history" in slots else None
    return reviewed, review, explanations, notices, failure, historical


def read_document(body):
    original_memory = domain.read_snapshot(body)
    load = domain.memoria_operativa(original_memory)
    if isinstance(load, domain.MemoryBlocked):
        return DocumentBlocked(load.reason, body)
    if not has_document_marker(body):
        reviewed = None
        completion = reviewed_completion(body)
        if completion is not None:
            reviewed = ReviewedHeader(reviewed_sha(body), completion)
        covered = None
        for line in body.splitlines():
            if line.startswith(COVERED_PREFIX):
                sha, entries = libro_de_linea(line)
                if reviewed and sha == reviewed.head_sha and entries is not None:
                    covered = CoverageBook(sha, tuple(entries.items()))
                break
        history_lines = body.split("\n")
        if history_lines[0] == MARKER:
            history_lines.pop(0)
        for prefix in (SHA_PREFIX, COMPLETION_PREFIX, COVERED_PREFIX):
            if history_lines and history_lines[0].startswith(prefix):
                history_lines.pop(0)
        if original_memory.block and original_memory.block in history_lines:
            history_lines.remove(original_memory.block)
        history = "\n".join(history_lines).strip("\n")
        return ReviewDocument(
            load.snapshot, reviewed, covered, None, (), (), None,
            safe_prose(history) if history else None,
        )
    lines = body.split("\n")
    if not lines or lines[0] != MARKER:
        return DocumentBlocked("cabecera desplazada", body)
    blocks = [(i, line) for i, line in enumerate(lines) if line.startswith(domain.FINDINGS_PREFIX)]
    if len(blocks) != 1:
        return DocumentBlocked("bloques de estado repetidos o ausentes", body)
    index, block = blocks[0]
    if not isinstance(domain.read_snapshot(block), domain.Valid):
        return DocumentBlocked("estado inválido", body)
    if index + 1 >= len(lines) or not lines[index + 1].startswith(VIEW_PREFIX) or not lines[index + 1].endswith(SUFFIX):
        return DocumentBlocked("vista fuera de posición", body)
    view_line = lines[index + 1]
    if body.count(VIEW_PREFIX) != 1:
        return DocumentBlocked("vistas duplicadas", body)
    try:
        data = json.loads(view_line[len(VIEW_PREFIX):-len(SUFFIX)], object_pairs_hook=_unique_object)
        if not isinstance(data, dict) or not isinstance(data.get("slots"), list) or any(
            not isinstance(slot, dict)
            or set(slot) != {"id", "sha256"}
            or not isinstance(slot["id"], str)
            or re.fullmatch(r"[a-zA-Z0-9-]+", slot["id"]) is None
            or not isinstance(slot["sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", slot["sha256"]) is None
            for slot in data["slots"]
        ):
            raise ValueError("declaración de slots inválida")
        if data["state_sha256"] != _digest(block):
            raise ValueError("hash de estado incorrecto")
        head = reviewed_completion(body)
        expected = data["reviewed"]
        if (expected is None) != (head is None) or (
            expected is not None and (
                expected["head_sha"] != reviewed_sha(body)
                or expected["completion"] != head
            )
        ):
            raise ValueError("cabecera adulterada")
        covered_lines = [line for line in lines if line.startswith(COVERED_PREFIX)]
        if len(covered_lines) > 1 or data["covered_sha256"] != (
            _digest(covered_lines[0]) if covered_lines else None
        ):
            raise ValueError("libro adulterado")
        covered = None
        if covered_lines:
            sha, entries = libro_de_linea(covered_lines[0])
            if expected is None or sha != expected["head_sha"] or entries is None or lines[index - 1] != covered_lines[0]:
                raise ValueError("libro fuera de posición")
            covered = CoverageBook(sha, tuple(entries.items()))
        pos = 1
        if expected:
            if lines[1] != f"{SHA_PREFIX}{expected['head_sha']} -->" or lines[2] != f"{COMPLETION_PREFIX}{expected['head_sha']}:{expected['completion']} -->":
                raise ValueError("cabecera fuera de posición")
            pos = 3
        if covered:
            pos += 1
        if pos != index:
            raise ValueError("estado fuera de posición")
        markers = list(SLOT_MARKER.finditer(body))
        ids = [entry["id"] for entry in data["slots"]]
        if len(set(ids)) != len(ids) or len(markers) != len(ids) * 2:
            raise ValueError("slots ausentes o duplicados")
        slots = {}
        for j, name in enumerate(ids):
            start, end = markers[2 * j], markers[2 * j + 1]
            if (start.group(1), start.group(2), end.group(1), end.group(2)) != ("slot", name, "end-slot", name):
                raise ValueError("slot desplazado")
            begin = start.end() + 1
            finish = end.start() - 1
            if body[start.end():begin] != "\n" or body[finish:end.start()] != "\n" or finish < begin:
                raise ValueError("slot incompleto")
            content = body[begin:finish]
            if _digest(content) != data["slots"][j]["sha256"]:
                raise ValueError("hash de slot incorrecto")
            slots[name] = content
        remainder = body.replace(view_line, "", 1)
        for marker in markers:
            remainder = remainder.replace(marker.group(), "", 1)
        if has_document_marker(remainder):
            raise ValueError("marcador activo fuera de slot")
        reviewed, review, explanations, notices, failure, historical = _metadata(data, slots)
        if review and (reviewed is None or review.head_sha != reviewed.head_sha):
            raise ValueError("revisión y cabecera no coinciden")
        if set(slots) != (
            ({"review"} if review else set())
            | {f"explanation-{entry.finding_id}" for entry in explanations}
            | ({"failure"} if failure else set())
            | ({"history"} if historical else set())
        ):
            raise ValueError("slots no declarados")
        if any(has_document_marker(content) for content in slots.values()):
            raise ValueError("marcador activo dentro de la prosa")
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        return DocumentBlocked(str(exc), body)
    return ReviewDocument(load.snapshot, reviewed, covered, review, explanations, notices, failure, historical)
