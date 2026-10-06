"""Dominio de la memoria del revisor: lectura compatible, migración, tipos
y escritura. Puro: sin red, sin modelo, sin Git.

Esquema legado (bloque sin `schema`): findings [{id, file, files, line,
severity, title, state}], next y seen. Lectura tolerante (las entradas malas
se descartan y los campos se normalizan, como siempre) y escritura legada por
defecto para estados legados.

Esquema 2: Snapshot con anclas, evidencia, cursor de comandos y solicitudes
pendientes. La lectura es estricta en la frontera (ids duplicados, tipos
inválidos o referencias inconsistentes -> Invalid) y la escritura no tiene
camino con pérdida: si el estado no cabe en el tope de 8000 bytes UTF-8 se
devuelve CapacityExceeded y el llamador conserva el último estado válido.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field, replace

FINDINGS_PREFIX = "<!-- ai-review:findings="
FINDINGS_SUFFIX = " -->"
FINDINGS_MAX_BYTES = 8000
FINDINGS_MAX_COUNT = 60
FINDINGS_TITLE_MAX = 160
FINDING_FILES_MAX = 5
SEVERITIES = ("Critical", "High", "Medium", "Low")
OPEN, RESOLVED, DISMISSED = "open", "resolved", "dismissed"
FINDING_ID_RE = re.compile(r"^F(\d+)$")
COMPLETE_CLAIM, PARTIAL, UNKNOWN = "complete_claim", "partial", "unknown"
_HEX40 = re.compile(r"^[0-9a-f]{40}$")


# Tipos del diseño (descriptivo en docs/reviewer-improvements-design.md).


@dataclass
class Missing:
    """No hay bloque de memoria."""

    block: str = ""


@dataclass
class Invalid:
    """Hay bloque pero no se puede leer con confianza."""

    reason: str
    block: str = ""


@dataclass
class Future:
    """Hay bloque de una versión más nueva que ésta."""

    version: int
    block: str = ""


@dataclass
class CapacityExceeded:
    """El estado v2/v3 no cabe en el presupuesto; no se recorta nada."""

    limit: int
    needed: int


@dataclass
class Revision:
    base_sha: str | None = None
    head_sha: str | None = None
    policy_digest: str | None = None


@dataclass
class StatusOpen:
    pass


@dataclass
class StatusResolved:
    at_sha: str | None = None


@dataclass
class StatusDismissed:
    command_id: int | None = None


@dataclass
class AnchorLegacy:
    path: str
    line: int | None = None


@dataclass
class AnchorLocated:
    path: str
    blob_sha: str
    range: tuple
    excerpt_digest: str
    symbol_hint: str | None = None


@dataclass
class EvidenceSource:
    anchor: object


@dataclass
class EvidenceCheck:
    check_id: str
    head_sha: str
    producer: str
    conclusion: str
    url: str


@dataclass
class EvidenceUnverified:
    text: str


@dataclass
class Finding:
    id: str | None
    title: str
    severity: str
    status: object
    primary_anchor: object
    related_anchors: list = field(default_factory=list)
    cause_hint: str | None = None
    evidence: list = field(default_factory=list)


@dataclass
class PendingRequest:
    id: str
    kind: str
    finding_id: str | None = None


@dataclass
class WorkRequest:
    """Solicitud pendiente con id numérico monotónico."""

    id: int
    kind: str
    finding_id: str | None = None
    legacy_id: str | None = None
    origin: str = ""
    basis_generation: int = 0
    state: str = "pending"


@dataclass
class RunKey:
    request_id: int
    run_id: int
    attempt: int


@dataclass
class Receipt:
    command_id: int
    effect: str


@dataclass
class StorageBudget:
    max_bytes: int = FINDINGS_MAX_BYTES


@dataclass
class EncodedCheckpoint:
    block: str
    schema: int
    bytes: int


@dataclass
class ReviewTarget:
    revision: Revision | None = None
    basis_generation: int = 0


@dataclass
class Snapshot:
    schema: int
    generation: int
    revision: Revision | None
    next_id: int
    completion: str
    findings: list
    command_cursor: int
    pending_requests: list = field(default_factory=list)
    request_count: int = 0
    receipts: tuple = field(default_factory=tuple)


@dataclass
class CompleteClaim:
    obligations: tuple = field(default_factory=tuple)


@dataclass
class Partial:
    missing: tuple = field(default_factory=tuple)
    reason: str = ""


@dataclass
class UnknownCoverage:
    reason: str = ""


@dataclass
class Valid:
    snapshot: Snapshot
    block: str = ""


@dataclass
class Legacy:
    snapshot: Snapshot
    raw: dict
    block: str = ""


@dataclass
class Replace:
    snapshot: Snapshot


@dataclass
class Keep:
    reason: str


@dataclass
class RequestWork:
    request: PendingRequest


# Funciones del estado legado, extraídas de review.py sin cambio de conducta.


def one_line(text, limit):
    collapsed = " ".join(str(text or "").split())
    if len(collapsed) > limit:
        collapsed = collapsed[:limit].rstrip()
    return collapsed.replace("--!>", "--!\u203a").replace("-->", "--\u203a")


def normalize_severity(value):
    for severity in SEVERITIES:
        if str(value or "").strip().lower() == severity.lower():
            return severity
    return "Medium"


def finding_number(fid):
    match = FINDING_ID_RE.match(str(fid or ""))
    return int(match.group(1)) if match else None


def sanitize_finding(entry, *, allow_dismissed):
    """Validate one raw finding dict. Returns a clean dict, or None to drop it."""
    if not isinstance(entry, dict):
        return None
    path = one_line(entry.get("file"), 200)
    title = one_line(entry.get("title"), FINDINGS_TITLE_MAX)
    if not path or not title:
        return None
    try:
        line = int(entry.get("line") or 0)
    except (TypeError, ValueError):
        line = 0
    state = str(entry.get("state") or OPEN).strip().lower()
    if state not in (OPEN, RESOLVED, DISMISSED) or (
        state == DISMISSED and not allow_dismissed
    ):
        state = OPEN
    fid = str(entry.get("id") or "").strip().upper()
    raw_files = entry.get("files") if isinstance(entry.get("files"), list) else []
    files = unique_paths([path] + [one_line(item, 200) for item in raw_files])
    return {
        "id": fid if finding_number(fid) else None,
        "file": path,
        "files": files,
        "line": max(line, 0),
        "severity": normalize_severity(entry.get("severity")),
        "title": title,
        "state": state,
    }


def unique_paths(paths):
    """Primary path first, then related ones, no blanks or repeats, capped."""
    out = []
    for path in paths:
        if path and path not in out:
            out.append(path)
    return out[:FINDING_FILES_MAX]


def derive_next(findings):
    numbers = [finding_number(f["id"]) for f in findings if f.get("id")]
    return (max(numbers) + 1) if numbers else 1


def find_findings_block(text, *, last=False):
    """Locate a findings block: (start, end, data) or None.

    Each ` -->` after the prefix is tried until the JSON parses, so a `-->` inside a
    title can't cut the block short. The sticky's own block is the FIRST one (right
    under the markers); the model's is the LAST one (right before COVERAGE), so a block
    the model quotes from the PR earlier in its text never wins.
    """
    text = text or ""
    starts, pos = [], text.find(FINDINGS_PREFIX)
    while pos >= 0:
        starts.append(pos)
        pos = text.find(FINDINGS_PREFIX, pos + 1)
    for start in reversed(starts) if last else starts:
        body = start + len(FINDINGS_PREFIX)
        end = text.find(FINDINGS_SUFFIX, body)
        while end >= 0:
            try:
                data = json.loads(text[body:end])
            except ValueError:
                end = text.find(FINDINGS_SUFFIX, end + 1)
                continue
            if isinstance(data, dict) and isinstance(data.get("findings"), list):
                return start, end + len(FINDINGS_SUFFIX), data
            break
    return None


def legacy_raw_of(data):
    """Sanitized legacy dict ({findings, next, seen}) from a parsed block."""
    findings = [
        f
        for f in (sanitize_finding(e, allow_dismissed=True) for e in data["findings"])
        if f
    ]
    claimed = data.get("next")
    minimum = derive_next(findings)
    seen = data.get("seen")
    return {
        "findings": findings,
        "next": claimed if isinstance(claimed, int) and claimed >= minimum else minimum,
        "seen": seen if isinstance(seen, int) and seen > 0 else 0,
    }


def serialize_findings(state, *, max_bytes=None):
    """Canonical hidden block: íntegro o CapacityExceeded (M1).

    La memoria es esencial: nunca se recortan títulos, rutas, ids, descartes
    ni el cursor. Si el estado íntegro no cabe en el presupuesto (bytes
    UTF-8), se devuelve CapacityExceeded y el publicador aplica Keep:
    conserva el bloque anterior sin avanzar el commit.
    """
    limite = FINDINGS_MAX_BYTES if max_bytes is None else max_bytes
    findings = sorted(state["findings"], key=lambda f: finding_number(f["id"]) or 0)
    entries = []
    for f in findings:
        entry = {
            "id": f["id"],
            "file": one_line(f["file"], 200),
            "line": f["line"],
            "severity": f["severity"],
            "title": one_line(f["title"], FINDINGS_TITLE_MAX),
            "state": f["state"],
        }
        related = [one_line(path, 200) for path in f.get("files", [])[1:]]
        if related:
            entry["files"] = related
        entries.append(entry)
    blob = json.dumps(
        {
            "findings": entries,
            "next": state["next"],
            **({"seen": state["seen"]} if state.get("seen") else {}),
        },
        separators=(",", ":"),
        ensure_ascii=False,
    )
    bloque = FINDINGS_PREFIX + blob + FINDINGS_SUFFIX
    needed = len(bloque.encode("utf-8"))
    if needed > limite:
        return CapacityExceeded(limit=limite, needed=needed)
    return bloque


def same_issue(a, b):
    return a["file"] == b["file"] and a["title"].casefold() == b["title"].casefold()


def parse_model_findings(text):
    """Parse the block the model emitted. The model may never dismiss; only users do."""
    load = read_snapshot(text, last=True)
    if not isinstance(load, Legacy):
        return None
    state = load.raw
    for finding in state["findings"]:
        if finding["state"] == DISMISSED:
            finding["state"] = OPEN
    return state


def strip_findings_block(text, *, last=False):
    found = find_findings_block(text, last=last)
    if found is None:
        return text or ""
    start, end, _ = found
    return (text[:start] + text[end:]).strip()


# Lectura compatible (legado + esquema 2) y migración.


def _migrate_legacy(raw):
    """Legacy dict -> Snapshot sin inventar anclas ni evidencia.

    Ubicaciones legadas (path/line), estados tal cual (descartes conservados),
    cursor `seen` -> command_cursor y contador `next` -> next_id. Sin causas,
    sin evidencia, sin revisión ni solicitudes (eso no existía en legado).
    """
    findings = []
    for f in raw["findings"]:
        status = {
            OPEN: StatusOpen(),
            RESOLVED: StatusResolved(at_sha=None),
            DISMISSED: StatusDismissed(command_id=None),
        }[f["state"]]
        findings.append(
            Finding(
                id=f.get("id"),
                title=f["title"],
                severity=f["severity"],
                status=status,
                primary_anchor=AnchorLegacy(path=f["file"], line=f["line"]),
                related_anchors=[
                    AnchorLegacy(path=p, line=None) for p in f.get("files", [])[1:]
                ],
                cause_hint=None,
                evidence=[],
            )
        )
    return Snapshot(
        schema=1,
        generation=0,
        revision=None,
        next_id=raw["next"],
        completion=UNKNOWN,
        findings=findings,
        command_cursor=raw["seen"],
        pending_requests=[],
    )


def _hex40(value):
    return isinstance(value, str) and bool(_HEX40.match(value))


def _anchor_of(data):
    if not isinstance(data, dict):
        raise _SchemaError("ancla no es un objeto")
    kind = data.get("kind")
    if kind == "legacy":
        path = data.get("path")
        line = data.get("line")
        if not isinstance(path, str) or not path:
            raise _SchemaError("ancla legada sin path")
        if line is not None and (
            not isinstance(line, int) or isinstance(line, bool) or line < 0
        ):
            raise _SchemaError("ancla legada con line inválido")
        return AnchorLegacy(path=path, line=line)
    if kind == "located":
        rango = data.get("range")
        if (
            not isinstance(data.get("path"), str)
            or not data["path"]
            or not _hex40(data.get("blob_sha"))
            or not isinstance(rango, list)
            or len(rango) != 2
            or not all(isinstance(x, int) and not isinstance(x, bool) for x in rango)
            or not isinstance(data.get("excerpt_digest"), str)
        ):
            raise _SchemaError("ancla localizada incompleta")
        hint = data.get("symbol_hint")
        if hint is not None and not isinstance(hint, str):
            raise _SchemaError("symbol_hint inválido")
        return AnchorLocated(
            path=data["path"],
            blob_sha=data["blob_sha"],
            range=(rango[0], rango[1]),
            excerpt_digest=data["excerpt_digest"],
            symbol_hint=hint,
        )
    raise _SchemaError(f"ancla de kind desconocido: {kind!r}")


def _evidence_of(data):
    if not isinstance(data, dict):
        raise _SchemaError("evidencia no es un objeto")
    kind = data.get("kind")
    if kind == "source":
        return EvidenceSource(anchor=_anchor_of(data.get("anchor")))
    if kind == "check":
        if (
            not isinstance(data.get("check_id"), str)
            or not _hex40(data.get("head_sha"))
            or not isinstance(data.get("producer"), str)
            or not isinstance(data.get("conclusion"), str)
            or not isinstance(data.get("url"), str)
        ):
            raise _SchemaError("check de evidencia incompleto")
        return EvidenceCheck(
            check_id=data["check_id"],
            head_sha=data["head_sha"],
            producer=data["producer"],
            conclusion=data["conclusion"],
            url=data["url"],
        )
    if kind == "unverified":
        if not isinstance(data.get("text"), str):
            raise _SchemaError("claim sin texto")
        return EvidenceUnverified(text=data["text"])
    raise _SchemaError(f"evidencia de kind desconocido: {kind!r}")


def _status_of(data):
    if not isinstance(data, dict):
        raise _SchemaError("estado no es un objeto")
    kind = data.get("kind")
    if kind == "open":
        return StatusOpen()
    if kind == "resolved":
        at_sha = data.get("at_sha")
        if at_sha is not None and not _hex40(at_sha):
            raise _SchemaError("resolved con at_sha inválido")
        return StatusResolved(at_sha=at_sha)
    if kind == "dismissed":
        command_id = data.get("command_id")
        if command_id is not None and (
            not isinstance(command_id, int) or isinstance(command_id, bool)
        ):
            raise _SchemaError("dismissed con command_id inválido")
        return StatusDismissed(command_id=command_id)
    raise _SchemaError(f"estado de kind desconocido: {kind!r}")


def _finding_of(data):
    if not isinstance(data, dict):
        raise _SchemaError("hallazgo no es un objeto")
    fid = data.get("id")
    if not isinstance(fid, str) or not FINDING_ID_RE.match(fid):
        raise _SchemaError(f"id de hallazgo inválido: {fid!r}")
    if (
        not isinstance(data.get("title"), str)
        or not data["title"]
        or data.get("severity") not in SEVERITIES
    ):
        raise _SchemaError(f"hallazgo {fid} con título o severidad inválidos")
    if "related_anchors" in data and not isinstance(data["related_anchors"], list):
        raise _SchemaError("related_anchors debe ser una lista")
    if "evidence" in data and not isinstance(data["evidence"], list):
        raise _SchemaError("evidence debe ser una lista")
    relacionadas = data.get("related_anchors") or []
    evidencia = data.get("evidence") or []
    causa = data.get("cause_hint")
    if causa is not None and not isinstance(causa, str):
        raise _SchemaError(f"cause_hint inválido: {causa!r}")
    return Finding(
        id=fid,
        title=data["title"],
        severity=data["severity"],
        status=_status_of(data.get("status")),
        primary_anchor=_anchor_of(data.get("primary_anchor")),
        related_anchors=[_anchor_of(a) for a in relacionadas],
        cause_hint=causa,
        evidence=[_evidence_of(e) for e in evidencia],
    )


class _SchemaError(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _base_snapshot(data, schema):
    generation = data.get("generation")
    next_id = data.get("next_id")
    cursor = data.get("command_cursor")
    if (
        not isinstance(generation, int)
        or isinstance(generation, bool)
        or generation < 0
        or not isinstance(next_id, int)
        or isinstance(next_id, bool)
        or next_id < 1
        or not isinstance(cursor, int)
        or isinstance(cursor, bool)
        or cursor < 0
    ):
        raise _SchemaError(f"contadores de schema {schema} inválidos")
    if data.get("completion") not in (COMPLETE_CLAIM, PARTIAL, UNKNOWN):
        raise _SchemaError("completion inválida")
    revision = None
    if data.get("revision") is not None:
        rev = data["revision"]
        if (
            not isinstance(rev, dict)
            or not _hex40(rev.get("base_sha"))
            or not _hex40(rev.get("head_sha"))
            or not isinstance(rev.get("policy_digest"), str)
        ):
            raise _SchemaError("revisión inválida")
        revision = Revision(
            base_sha=rev["base_sha"],
            head_sha=rev["head_sha"],
            policy_digest=rev["policy_digest"],
        )
    findings, vistos = [], set()
    for entry in data.get("findings", []):
        hallazgo = _finding_of(entry)
        if hallazgo.id in vistos:
            raise _SchemaError(f"id de hallazgo duplicado: {hallazgo.id}")
        vistos.add(hallazgo.id)
        findings.append(hallazgo)
    maximo = max((finding_number(f.id) or 0) for f in findings) if findings else 0
    if next_id <= maximo:
        raise _SchemaError(
            f"next_id ({next_id}) no supera al id máximo existente (F{maximo})"
        )
    for f in findings:
        if isinstance(f.status, StatusDismissed) and (
            f.status.command_id is not None and f.status.command_id > cursor
        ):
            raise _SchemaError(
                f"command_id {f.status.command_id} supera command_cursor ({cursor})"
            )
    return generation, revision, next_id, data["completion"], findings, cursor


def _v2_snapshot(data):
    (
        generation,
        revision,
        next_id,
        completion,
        findings,
        cursor,
    ) = _base_snapshot(data, 2)
    if "pending_requests" in data and not isinstance(data["pending_requests"], list):
        raise _SchemaError("pending_requests debe ser una lista")
    ids = {f.id for f in findings}
    solicitudes, textos = [], set()
    for req in data.get("pending_requests") or []:
        if (
            not isinstance(req, dict)
            or not isinstance(req.get("id"), str)
            or not req["id"]
            or not isinstance(req.get("kind"), str)
        ):
            raise _SchemaError("solicitud pendiente inválida")
        if req["id"] in textos:
            raise _SchemaError(f"id de solicitud duplicado: {req['id']!r}")
        textos.add(req["id"])
        finding_id = req.get("finding_id")
        if finding_id is not None and finding_id not in ids:
            raise _SchemaError(
                f"la solicitud {req['id']!r} apunta al hallazgo inexistente {finding_id!r}"
            )
        solicitudes.append(
            PendingRequest(id=req["id"], kind=req["kind"], finding_id=finding_id)
        )
    return Snapshot(
        schema=2,
        generation=generation,
        revision=revision,
        next_id=next_id,
        completion=completion,
        findings=findings,
        command_cursor=cursor,
        pending_requests=solicitudes,
    )


def _v3_snapshot(data):
    (
        generation,
        revision,
        next_id,
        completion,
        findings,
        cursor,
    ) = _base_snapshot(data, 3)
    if "pending_requests" in data and not isinstance(data["pending_requests"], list):
        raise _SchemaError("pending_requests debe ser una lista")
    if "receipts" in data and not isinstance(data["receipts"], list):
        raise _SchemaError("receipts debe ser una lista")
    request_count = data.get("request_count", 0)
    if (
        not isinstance(request_count, int)
        or isinstance(request_count, bool)
        or request_count < 0
    ):
        raise _SchemaError("request_count inválido")
    ids = {f.id for f in findings}
    solicitudes, numericos, legados = [], set(), set()
    for req in data.get("pending_requests") or []:
        rid = req.get("id") if isinstance(req, dict) else None
        legacy_id = req.get("legacy_id") if isinstance(req, dict) else None
        if (
            not isinstance(req, dict)
            or not isinstance(rid, int)
            or isinstance(rid, bool)
            or rid < 1
            or rid in numericos
            or rid > request_count
            or not isinstance(req.get("kind"), str)
            or not req["kind"]
        ):
            raise _SchemaError("solicitud pendiente inválida")
        if legacy_id is not None and (
            not isinstance(legacy_id, str) or not legacy_id or legacy_id in legados
        ):
            raise _SchemaError(
                f"legacy_id de solicitud inválido o duplicado: {legacy_id!r}"
            )
        legados.add(legacy_id)
        numericos.add(rid)
        finding_id = req.get("finding_id")
        if finding_id is not None and finding_id not in ids:
            raise _SchemaError(
                f"la solicitud {rid} apunta al hallazgo inexistente {finding_id!r}"
            )
        solicitudes.append(
            WorkRequest(
                id=rid,
                kind=req["kind"],
                finding_id=finding_id,
                legacy_id=legacy_id,
                origin=req.get("origin", ""),
                basis_generation=req.get("basis_generation", 0),
                state=req.get("state", "pending"),
            )
        )
    recibos, comandos = [], set()
    for recibo in data.get("receipts") or []:
        cid = recibo.get("command_id") if isinstance(recibo, dict) else None
        efecto = recibo.get("effect") if isinstance(recibo, dict) else None
        if (
            not isinstance(recibo, dict)
            or not isinstance(cid, int)
            or isinstance(cid, bool)
            or cid < 1
            or cid in comandos
            or cid > cursor
            or not isinstance(efecto, str)
            or not efecto
        ):
            raise _SchemaError("recibo inválido")
        comandos.add(cid)
        recibos.append(Receipt(command_id=cid, effect=efecto))
    return Snapshot(
        schema=3,
        generation=generation,
        revision=revision,
        next_id=next_id,
        completion=completion,
        findings=findings,
        command_cursor=cursor,
        pending_requests=solicitudes,
        request_count=request_count,
        receipts=tuple(recibos),
    )


def read_snapshot(body, *, last=False):
    """Lee el bloque de memoria y clasifica el resultado.

    Missing (no hay bloque), Invalid (bloque ilegible o que viola la frontera
    del esquema), Future (versión más nueva), Valid (schema 2 o 3) o Legacy
    (formato legado, migrado a Snapshot sin inventar nada; `raw` conserva el
    estado legado saneado para el adaptador).
    """
    found = find_findings_block(body, last=last)
    if found is None:
        if FINDINGS_PREFIX in (body or ""):
            return Invalid("bloque de hallazgos corrupto: JSON o cierre ausentes")
        return Missing()
    start, end, data = found
    found_block = body[start:end]
    data = found[2]
    if "schema" in data:
        schema = data["schema"]
        if schema == 2:
            try:
                return Valid(_v2_snapshot(data), block=found_block)
            except _SchemaError as exc:
                return Invalid(exc.reason, block=found_block)
            except (TypeError, ValueError, AttributeError, KeyError) as exc:
                return Invalid(f"bloque v2 mal formado: {exc}", block=found_block)
        if schema == 3:
            try:
                return Valid(_v3_snapshot(data), block=found_block)
            except _SchemaError as exc:
                return Invalid(exc.reason, block=found_block)
            except (TypeError, ValueError, AttributeError, KeyError) as exc:
                return Invalid(f"bloque v3 mal formado: {exc}", block=found_block)
        if isinstance(schema, int) and not isinstance(schema, bool):
            return Future(schema, block=found_block)
        return Invalid(f"schema desconocido: {schema!r}")
    raw = legacy_raw_of(data)
    return Legacy(snapshot=_migrate_legacy(raw), raw=raw, block=found_block)


def snapshot_a_v3(snapshot):
    """Migra un Snapshot legado/v2 a schema 3.

    Las solicitudes pendientes reciben IDs numéricos monotónicos (1..n) y
    conservan su identificador anterior en legacy_id: la correspondencia viaja
    persistida en cada solicitud, sin compactar pendientes. Es idempotente.
    """
    if snapshot.schema == 3:
        return snapshot
    solicitudes = []
    for i, req in enumerate(snapshot.pending_requests, start=1):
        legacy = req.id if isinstance(req, PendingRequest) else req.legacy_id
        solicitudes.append(
            WorkRequest(
                id=i, kind=req.kind, finding_id=req.finding_id, legacy_id=legacy
            )
        )
    return replace(
        snapshot,
        schema=3,
        request_count=len(solicitudes),
        pending_requests=solicitudes,
        receipts=snapshot.receipts,
    )


def normalize_policy(boot):
    """Normaliza la política confiable una vez.

    Identidad externa current/anchors; el valor interno antiguo `titles` se
    adapta aquí, en la frontera de compatibilidad. Valida modos, versión de
    schema, reglas, exclusiones y presupuestos.
    """
    identity = boot.get("finding_identity", "titles")
    if identity == "titles":
        identity = "current"
    if identity not in ("current", "anchors"):
        raise ValueError(f"finding_identity inválida: {identity!r}")
    mode = boot.get("diff_mode", "full")
    if mode not in ("full", "incremental"):
        raise ValueError(f"diff_mode inválido: {mode!r}")
    version = boot.get("schema_version", 2)
    if version not in (2, 3):
        raise ValueError(f"schema_version inválida: {version!r}")
    max_count = boot.get("findings_max_count", FINDINGS_MAX_COUNT)
    max_bytes = boot.get("findings_max_bytes", FINDINGS_MAX_BYTES)
    if not isinstance(max_count, int) or isinstance(max_count, bool) or max_count < 1:
        raise ValueError(f"findings_max_count inválido: {max_count!r}")
    if (
        not isinstance(max_bytes, int)
        or isinstance(max_bytes, bool)
        or max_bytes < 1000
    ):
        raise ValueError(f"findings_max_bytes inválido: {max_bytes!r}")
    patterns = boot.get("exclude_patterns") or ()
    if isinstance(patterns, str) or not all(isinstance(x, str) for x in patterns):
        raise ValueError("exclude_patterns debe ser una lista de patrones")
    return ReviewPolicy(
        finding_identity=identity,
        findings_max_count=max_count,
        findings_max_bytes=max_bytes,
        rules_digest=str(boot.get("rules_digest", "")),
        exclude_patterns=tuple(patterns),
        allow_inline_comments=bool(boot.get("allow_inline_comments", False)),
        schema_version=version,
        diff_mode=mode,
    )


def _legacy_raw_of_snapshot(snapshot):
    findings = []
    for f in snapshot.findings:
        if isinstance(f.status, StatusOpen):
            state = OPEN
        elif isinstance(f.status, StatusResolved):
            state = RESOLVED
        else:
            state = DISMISSED
        anchor = f.primary_anchor
        path = anchor.path if isinstance(anchor, (AnchorLegacy, AnchorLocated)) else ""
        line = anchor.line if isinstance(anchor, AnchorLegacy) else 0
        entry = {
            "id": f.id,
            "file": path,
            "line": line if isinstance(line, int) else 0,
            "severity": f.severity,
            "title": f.title,
            "state": state,
        }
        related = [a.path for a in f.related_anchors]
        if related:
            entry["files"] = [path] + related
        findings.append(entry)
    raw = {"findings": findings, "next": snapshot.next_id}
    if snapshot.command_cursor:
        raw["seen"] = snapshot.command_cursor
    return raw


def _neutralizar(texto):
    """Los textos nunca cierran el bloque: -->/--!> pasan a --›/--!›."""
    if texto is None:
        return None
    return str(texto).replace("--!>", "--!\u203a").replace("-->", "--\u203a")


def _revision_json(snapshot):
    if snapshot.revision is None:
        return None
    return {
        "base_sha": snapshot.revision.base_sha,
        "head_sha": snapshot.revision.head_sha,
        "policy_digest": snapshot.revision.policy_digest,
    }


def _findings_json(snapshot, *, neutralizar=True):
    def texto(valor):
        return _neutralizar(valor) if neutralizar else valor

    def anchor_json(a):

        if isinstance(a, AnchorLegacy):
            return {"kind": "legacy", "path": texto(a.path), "line": a.line}
        return {
            "kind": "located",
            "path": texto(a.path),
            "blob_sha": a.blob_sha,
            "range": list(a.range),
            "excerpt_digest": texto(a.excerpt_digest),
            "symbol_hint": texto(a.symbol_hint),
        }

    def evidence_json(e):
        if isinstance(e, EvidenceSource):
            return {"kind": "source", "anchor": anchor_json(e.anchor)}
        if isinstance(e, EvidenceCheck):
            return {
                "kind": "check",
                "check_id": texto(e.check_id),
                "head_sha": e.head_sha,
                "producer": texto(e.producer),
                "conclusion": texto(e.conclusion),
                "url": texto(e.url),
            }
        return {"kind": "unverified", "text": texto(e.text)}

    def status_json(status):
        if isinstance(status, StatusOpen):
            return {"kind": "open"}
        if isinstance(status, StatusResolved):
            return {"kind": "resolved", "at_sha": status.at_sha}
        return {"kind": "dismissed", "command_id": status.command_id}

    return [
        {
            "id": f.id,
            "title": _neutralizar(f.title) if neutralizar else f.title,
            "severity": f.severity,
            "status": status_json(f.status),
            "primary_anchor": anchor_json(f.primary_anchor),
            "related_anchors": [anchor_json(a) for a in f.related_anchors],
            "cause_hint": _neutralizar(f.cause_hint) if neutralizar else f.cause_hint,
            "evidence": [evidence_json(e) for e in f.evidence],
        }
        for f in snapshot.findings
    ]


def encode_snapshot(snapshot, budget=None):
    """Serializa el estado: legado en formato legado, v2 sin pérdida, v3 en
    checkpoint codificado con escapes JSON reversibles.

    Devuelve el bloque (str) para legado/v2, un EncodedCheckpoint para v3, o
    CapacityExceeded cuando el estado no cabe en el presupuesto (nunca se
    recorta: el llamador conserva el último estado válido).
    """
    limite = budget.max_bytes if budget is not None else FINDINGS_MAX_BYTES
    if snapshot.schema == 1:
        return serialize_findings(_legacy_raw_of_snapshot(snapshot), max_bytes=limite)
    if snapshot.schema == 2:
        payload = {
            "schema": 2,
            "generation": snapshot.generation,
            "revision": _revision_json(snapshot),
            "next_id": snapshot.next_id,
            "completion": snapshot.completion,
            "command_cursor": snapshot.command_cursor,
            "pending_requests": [
                {"id": r.id, "kind": r.kind, "finding_id": r.finding_id}
                for r in snapshot.pending_requests
            ],
            "findings": _findings_json(snapshot),
        }
        blob = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        bloque = FINDINGS_PREFIX + blob + FINDINGS_SUFFIX
        load = read_snapshot(bloque)
        if not isinstance(load, Valid):
            raise ValueError(f"estado v2 ilegal: {load.reason}")
        needed = len(bloque.encode("utf-8"))
        if needed > limite:
            return CapacityExceeded(limit=limite, needed=needed)
        return bloque
    if snapshot.schema == 3:
        payload = {
            "schema": 3,
            "generation": snapshot.generation,
            "revision": _revision_json(snapshot),
            "next_id": snapshot.next_id,
            "completion": snapshot.completion,
            "command_cursor": snapshot.command_cursor,
            "request_count": snapshot.request_count,
            "pending_requests": [
                {
                    "id": r.id,
                    "legacy_id": r.legacy_id,
                    "kind": r.kind,
                    "finding_id": r.finding_id,
                    "origin": r.origin,
                    "basis_generation": r.basis_generation,
                    "state": r.state,
                }
                for r in snapshot.pending_requests
            ],
            "receipts": [
                {"command_id": r.command_id, "effect": r.effect}
                for r in snapshot.receipts
            ],
            "findings": _findings_json(snapshot, neutralizar=False),
        }
        blob = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        blob = blob.replace("-->", "--\\u003e").replace("--!>", "--!\\u003e")
        bloque = FINDINGS_PREFIX + blob + FINDINGS_SUFFIX
        load = read_snapshot(bloque)
        if not isinstance(load, Valid) or load.snapshot.schema != 3:
            raise ValueError(f"estado v3 ilegal: {getattr(load, 'reason', load)}")
        needed = len(bloque.encode("utf-8"))
        if needed > limite:
            return CapacityExceeded(limit=limite, needed=needed)
        return EncodedCheckpoint(block=bloque, schema=3, bytes=needed)
    raise ValueError(f"schema desconocido: {snapshot.schema!r}")


# ---- F0: identidad, evidencia y resolución ----


@dataclass
class Observation:
    """Lo que el modelo reporta por hallazgo, antes de validar."""

    title: str
    severity: str
    primary_anchor: object
    related_anchors: list = field(default_factory=list)
    cause_hint: str | None = None
    evidence: list = field(default_factory=list)
    claim: str = OPEN


@dataclass
class MatchExisting:
    id: str


@dataclass
class MatchNew:
    pass


@dataclass
class MatchAmbiguous:
    ids: tuple


@dataclass
class RepositoryFacts:
    """Hechos del repositorio que el adaptador verifica con Git.

    El dominio no ejecuta comandos: recibe la revisión, el delta real,
    correspondencias de renombres confirmadas (pares ruta_nueva -> ruta_vieja)
    y los blobs ya leídos (por (ruta, blob_sha)) para validar las citas.
    """

    revision: Revision | None = None
    changed_paths: tuple = ()
    reverted_paths: tuple = ()
    renames: tuple = ()
    blobs: dict = field(default_factory=dict)


@dataclass
class ReviewPlan:
    revision: Revision | None = None
    basis_generation: int = 0
    mode: str = "full"
    previous_sha: str | None = None
    changed_paths: tuple = ()
    obligations: tuple = ()
    context_refs: tuple = ()
    exclusions: tuple = ()
    delivered: tuple = ()


@dataclass
class ReviewPolicy:
    """Filtros, presupuestos, digest de reglas y controles de activación."""

    finding_identity: str = "titles"
    findings_max_count: int = FINDINGS_MAX_COUNT
    findings_max_bytes: int = FINDINGS_MAX_BYTES
    rules_digest: str = ""
    exclude_patterns: tuple = ()
    allow_inline_comments: bool = False
    schema_version: int = 2
    diff_mode: str = "full"


@dataclass
class ValidatedReport:
    """El reporte del modelo con las citas ya validadas contra los blobs.

    `observations` trae la evidencia filtrada: las citas verificadas quedan
    como `EvidenceSource`; lo no comprobable viaja etiquetado aparte como
    `EvidenceUnverified`; las citas rechazadas se listan en `rechazadas`.
    """

    facts: RepositoryFacts
    observations: list
    claimed_coverage: str = UNKNOWN
    rechazadas: tuple = ()
    coverage: object = None


def _ruta_primaria(algo):
    anchor = getattr(algo, "primary_anchor", None)
    return anchor.path if anchor is not None else ""


def validar_reporte(observaciones, cobertura, facts):
    """Valida ruta, blob, rango y digest del extracto contra los blobs que el
    adaptador verificó. Una cita que no verifica se rechaza (y no queda como
    evidencia); las afirmaciones sin evidencia comprobable viajan etiquetadas
    aparte como EvidenceUnverified. La ubicación verificada prueba que la cita
    existe, nunca que el diagnóstico sea correcto.
    """
    observadas, rechazadas = [], []
    for i, obs in enumerate(observaciones):
        primaria, motivo_primaria = _ancla_verificada(obs.primary_anchor, facts)
        if obs.primary_anchor is not None and not isinstance(
            obs.primary_anchor, AnchorLocated
        ):
            primaria = obs.primary_anchor  # ancla legada: no sirve como identidad
        elif motivo_primaria:
            rechazadas.append((i, f"ancla primaria: {motivo_primaria}"))
            primaria = AnchorLegacy(path=obs.primary_anchor.path, line=None)
        relacionadas = []
        for a in obs.related_anchors:
            verificada, motivo = _ancla_verificada(a, facts)
            if verificada is None:
                if motivo:
                    rechazadas.append((i, f"ancla relacionada: {motivo}"))
                relacionadas.append(a)
            else:
                relacionadas.append(verificada)
        evidencia = []
        for e in obs.evidence:
            if isinstance(e, EvidenceSource) and isinstance(e.anchor, AnchorLocated):
                verificada, motivo = _ancla_verificada(e.anchor, facts)
                if verificada is None:
                    rechazadas.append((i, motivo or "ancla no verificada"))
                    continue
                evidencia.append(e)
            else:
                evidencia.append(e)
        observadas.append(
            replace(
                obs,
                primary_anchor=primaria,
                related_anchors=tuple(relacionadas),
                evidence=tuple(evidencia),
            )
        )
    return ValidatedReport(
        facts=facts,
        observations=observadas,
        claimed_coverage=cobertura,
        rechazadas=tuple(rechazadas),
    )


def _ancla_verificada(anchor, facts):
    """El AnchorLocated verificado contra los blobs, o (None, motivo)."""
    if not isinstance(anchor, AnchorLocated):
        return None, None
    lineas = facts.blobs.get((anchor.path, anchor.blob_sha))
    if lineas is None:
        return None, "blob no verificado por el adaptador"
    a, b = anchor.range
    if not (
        isinstance(a, int)
        and isinstance(b, int)
        and not isinstance(a, bool)
        and not isinstance(b, bool)
        and 1 <= a <= b <= len(lineas)
    ):
        return None, "rango fuera del blob"
    extracto = "\n".join(lineas[a - 1 : b])
    if hashlib.sha256(extracto.encode("utf-8")).hexdigest() != anchor.excerpt_digest:
        return None, "el digest del extracto no coincide"
    return anchor, None


def _rutas_posibles(observation, facts):
    ruta = _ruta_primaria(observation)
    rutas = {ruta}
    for nueva, vieja in facts.renames:
        if ruta == nueva:
            rutas.add(vieja)
    return rutas


def match_finding(previous, observation, facts):
    """Coincidencia determinista (F0, acumulada r3-r7).

    Los candidatos de la misma ruta (o de la renombrada confirmada) se
    separan en P_loc (AnchorLocated verificable contra los blobs), P_loc
    viejos (Located cuyo blob ya no está en facts.blobs: identidad por
    digest guardado, F0 r6) y P_leg (AnchorLegacy, o ancla que no verifica).

    Con ancla verificada en la observación:
    1) Fuerte: mismo excerpt_digest y extracto que aparece UNA sola vez en
       el blob -> Existing; varios con el mismo digest -> Ambiguous.
    2) Plausibles en P_loc: mismo digest con extracto repetido, o un rango
       que se solape.
    3) Contra P_leg y contra los P_loc viejos: exactamente un candidato con
       el mismo título (incluidos los descartados) y ningún otro plausible
       -> Existing. También es plausible un P_leg cuya línea legada cae
       dentro del rango observado.
    Cualquier plausible -> Ambiguous sin fusionar. Si no hay ninguno -> New.

    Sin ancla verificada en la observación: lógica de título + ruta (con los
    descartados identificables por título y no adoptables por ruta sola).
    """
    rutas = _rutas_posibles(observation, facts)
    titulo = observation.title.casefold()
    previos_todos = [f for f in previous if _ruta_primaria(f) in rutas]

    p_loc, p_loc_viejos, p_leg = [], [], []
    for f in previos_todos:
        ancla, _ = _ancla_verificada(f.primary_anchor, facts)
        if ancla is not None:
            p_loc.append((f, ancla))
        elif isinstance(f.primary_anchor, AnchorLocated):
            # blob del push anterior: no verificable ahora, pero el digest
            # guardado sigue siendo identidad contra el blob HEAD (F0 r6)
            p_loc_viejos.append(f)
        else:
            p_leg.append(f)

    ancla_obs, _ = _ancla_verificada(observation.primary_anchor, facts)
    if ancla_obs is not None:
        # f) el digest igual cuenta como identidad fuerte sólo si el extracto
        # aparece UNA vez en el blob; repetido es sólo plausible.
        lineas_obs = facts.blobs[(ancla_obs.path, ancla_obs.blob_sha)]
        ini, fin_r = ancla_obs.range
        extracto_obs = "\n".join(lineas_obs[ini - 1 : fin_r])
        largo = fin_r - ini + 1
        apariciones = sum(
            1
            for i in range(len(lineas_obs) - largo + 1)
            if "\n".join(lineas_obs[i : i + largo]) == extracto_obs
        )

        mismo_digest = [
            f for f, ancla in p_loc if ancla.excerpt_digest == ancla_obs.excerpt_digest
        ]
        # r6: el digest GUARDADO de un previo cuyo blob ya no está en
        # facts.blobs también es identidad (el extracto sobrevive la edición).
        mismo_digest_guardado = [
            f
            for f in p_loc_viejos
            if isinstance(f.primary_anchor, AnchorLocated)
            and f.primary_anchor.excerpt_digest == ancla_obs.excerpt_digest
        ]
        fuertes = mismo_digest + mismo_digest_guardado
        if fuertes and apariciones == 1:
            if len(fuertes) == 1:
                return MatchExisting(id=fuertes[0].id)
            return MatchAmbiguous(ids=tuple(sorted(f.id for f in fuertes)))

        plausibles = {f.id for f in fuertes} if apariciones > 1 else set()
        for f, ancla in p_loc:
            if ancla.excerpt_digest == ancla_obs.excerpt_digest:
                continue
            a1, b1 = ancla.range
            a2, b2 = ancla_obs.range
            if a1 <= b2 and a2 <= b1:
                plausibles.add(f.id)

        # r7 VUELVE: los p_loc_viejos participan como P_leg en la regla de
        # título (un candidato único con el mismo título -> Existing aunque
        # el blob haya cambiado; varios -> Ambiguous sin fusionar).
        candidatos_titulo = [
            f for f in p_leg + p_loc_viejos if f.title.casefold() == titulo
        ]
        en_rango = [
            f
            for f in p_leg
            if (
                (linea := getattr(f.primary_anchor, "line", None)) is not None
                and not isinstance(linea, bool)
                and ini <= linea <= fin_r
            )
        ]
        if len(candidatos_titulo) == 1:
            candidata = candidatos_titulo[0].id
            otros_en_rango = [f.id for f in en_rango if f.id != candidata]
            if not plausibles and not otros_en_rango:
                return MatchExisting(id=candidata)
        plausibles.update(f.id for f in candidatos_titulo)
        plausibles.update(f.id for f in en_rango)
        if plausibles:
            return MatchAmbiguous(ids=tuple(sorted(plausibles)))
        return MatchNew()

    # sin ancla verificada en la observación: título + ruta (compatibilidad)
    fuertes = [f for f in previos_todos if f.title.casefold() == titulo]
    if len(fuertes) == 1:
        return MatchExisting(id=fuertes[0].id)
    if len(fuertes) > 1:
        return MatchAmbiguous(ids=tuple(sorted(f.id for f in fuertes)))
    previos = [f for f in previos_todos if not isinstance(f.status, StatusDismissed)]
    if len(previos) == 1:
        return MatchExisting(id=previos[0].id)
    if len(previos) > 1:
        causa = (observation.cause_hint or "").casefold()
        con_causa = (
            [f for f in previos if (f.cause_hint or "").casefold() == causa]
            if causa
            else []
        )
        if len(con_causa) == 1:
            return MatchExisting(id=con_causa[0].id)
        return MatchAmbiguous(ids=tuple(sorted(f.id for f in previos)))
    return MatchNew()


def observation_de_entrada(entry):
    """Entrada del bloque del modelo -> Observation (camino anchors de F0).

    Transporte puro: la cita/ancla se valida después (validar_reporte) y la
    severidad se normaliza al aceptar. Sin ancla declarada, la ubicación es
    la legada (ruta y línea del propio hallazgo).
    """
    ancla = entry.get("anchor")
    if isinstance(ancla, dict) and ancla.get("blob_sha"):
        rango = ancla.get("range") or [0, 0]
        primaria = AnchorLocated(
            path=str(ancla.get("path") or ""),
            blob_sha=str(ancla.get("blob_sha") or ""),
            range=(int(rango[0]), int(rango[1])),
            excerpt_digest=str(ancla.get("excerpt_digest") or ""),
            symbol_hint=ancla.get("symbol_hint"),
        )
    elif isinstance(ancla, dict):
        primaria = AnchorLegacy(
            path=str(ancla.get("path") or ""), line=ancla.get("line")
        )
    else:
        primaria = AnchorLegacy(
            path=str(entry.get("file") or ""), line=entry.get("line")
        )
    relacionadas = []
    for a in entry.get("related_anchors", []) or []:
        if isinstance(a, dict) and a.get("blob_sha"):
            r = a.get("range") or [0, 0]
            relacionadas.append(
                AnchorLocated(
                    path=str(a.get("path") or ""),
                    blob_sha=str(a.get("blob_sha") or ""),
                    range=(int(r[0]), int(r[1])),
                    excerpt_digest=str(a.get("excerpt_digest") or ""),
                )
            )
        elif isinstance(a, dict):
            relacionadas.append(
                AnchorLegacy(path=str(a.get("path") or ""), line=a.get("line"))
            )
    evidencia = [
        EvidenceUnverified(text=str(ev.get("text") or ""))
        for ev in entry.get("evidence", []) or []
        if isinstance(ev, dict) and ev.get("kind") == "unverified"
    ]
    claim = RESOLVED if entry.get("claim") == "resolved" else OPEN
    return Observation(
        title=str(entry.get("title") or ""),
        severity=str(entry.get("severity") or ""),
        primary_anchor=primaria,
        related_anchors=tuple(relacionadas),
        cause_hint=entry.get("cause_hint"),
        evidence=tuple(evidencia),
        claim=claim,
    )


def _severidad_de(valor):
    """Severidad canónica de la observación + marca si hubo que degradarla."""
    texto = str(valor or "").strip()
    for severidad in SEVERITIES:
        if texto.lower() == severidad.lower():
            return severidad, None
    if not texto:
        return "Medium", None
    return "Medium", f"severity {texto!r} no reconocida; degradada a Medium"


def _cambio_pertinente(finding, cambiadas):
    rutas = {_ruta_primaria(finding)} | {a.path for a in finding.related_anchors}
    return bool(rutas & set(cambiadas))


def accept_report(current, plan, report):
    """Acepta el reporte validado y devuelve la Transition del estado.

    Requiere estado v2 o v3 (schema 2/3): en legado devuelve Keep. El programa asigna
    los ids; título y causa son mutables. Resolver exige cambio pertinente o
    reversión exacta; la evaluación del modelo queda etiquetada como
    UnverifiedClaim. Un match Ambiguous entra como hallazgo separado con la
    indicación de posible duplicado, sin fusionar. Los descartes nunca
    reaparecen.
    """
    if current.schema not in (2, 3):
        return Keep(reason="el reconocimiento de identidad requiere estado v2 o v3")
    facts = report.facts
    revertidas = set(facts.reverted_paths)
    cambiadas = set(plan.changed_paths) | revertidas
    findings = list(current.findings)
    por_id = {f.id: f for f in findings if f.id}
    tocados, next_id = set(), current.next_id
    for obs in report.observations:
        severidad, marca_severidad = _severidad_de(obs.severity)
        match = match_finding(findings, obs, facts)
        if isinstance(match, MatchExisting):
            fid = match.id
            previo = por_id.get(fid)
            if previo is None:
                continue
            if isinstance(previo.status, StatusDismissed):
                continue  # un descarte confirmado no reaparece
            severidad, marca_severidad = _severidad_de(obs.severity)
            if fid in tocados:
                # R-A: la primera mención gana el id; la segunda entra separada
                # con marca de posible duplicado, sin perder evidencia ni ancla.
                causa = f"posible duplicado de {fid}"
                if obs.cause_hint:
                    causa = f"{causa}; {obs.cause_hint}"
                evidencia_dup = tuple(obs.evidence)
                if marca_severidad:
                    evidencia_dup = evidencia_dup + (
                        EvidenceUnverified(text=marca_severidad),
                    )
                findings.append(
                    Finding(
                        id=f"F{next_id}",
                        title=obs.title,
                        severity=severidad,
                        status=StatusOpen(),
                        primary_anchor=obs.primary_anchor,
                        related_anchors=tuple(obs.related_anchors),
                        cause_hint=causa,
                        evidence=evidencia_dup,
                    )
                )
                next_id += 1
                continue
            tocados.add(fid)
            evidencia = tuple(obs.evidence)
            if marca_severidad:
                evidencia = evidencia + (EvidenceUnverified(text=marca_severidad),)
            reverting = _cambio_pertinente(previo, revertidas)
            if reverting:
                estado = StatusResolved(
                    at_sha=plan.revision.head_sha if plan.revision else None
                )
            elif (
                isinstance(previo.status, StatusResolved)
                and obs.claim == OPEN
                and _cambio_pertinente(previo, cambiadas)
            ):
                estado = StatusOpen()  # regresión con cambio pertinente: se reabre
            elif isinstance(previo.status, StatusResolved):
                estado = (
                    previo.status
                )  # la resolución se conserva sin cambio que la rompa
            elif obs.claim == RESOLVED and not _cambio_pertinente(previo, cambiadas):
                evidencia = evidencia + (
                    EvidenceUnverified(
                        text="el modelo lo evalúa resuelto; sin cambio pertinente registrado"
                    ),
                )
                estado = StatusOpen()
            elif obs.claim == RESOLVED:
                estado = StatusResolved(
                    at_sha=plan.revision.head_sha if plan.revision else None
                )
            else:
                estado = StatusOpen()
            ancla = (
                obs.primary_anchor
                if isinstance(obs.primary_anchor, AnchorLocated)
                else previo.primary_anchor
            )
            relacionadas = tuple(obs.related_anchors) or previo.related_anchors
            findings[findings.index(previo)] = replace(
                previo,
                title=obs.title,
                severity=severidad,
                cause_hint=obs.cause_hint,
                primary_anchor=ancla,
                related_anchors=relacionadas,
                evidence=evidencia,
                status=estado,
            )
            continue
        if isinstance(match, MatchAmbiguous):
            causa = "posible duplicado de " + ", ".join(match.ids)
            obs = replace(
                obs,
                cause_hint=(f"{obs.cause_hint}; {causa}" if obs.cause_hint else causa),
            )
        else:
            obs = replace(obs)
        nueva = Finding(
            id=f"F{next_id}",
            title=obs.title,
            severity=severidad,
            status=StatusOpen(),
            primary_anchor=obs.primary_anchor,
            related_anchors=tuple(obs.related_anchors),
            cause_hint=obs.cause_hint,
            evidence=tuple(obs.evidence),
        )
        next_id += 1
        findings.append(nueva)
    for i, f in enumerate(findings):
        if f.id in tocados or isinstance(f.status, StatusDismissed):
            continue
        if _ruta_primaria(f) in revertidas and not isinstance(f.status, StatusResolved):
            findings[i] = replace(
                f,
                status=StatusResolved(
                    at_sha=plan.revision.head_sha if plan.revision else None
                ),
            )
    findings.sort(key=lambda f: finding_number(f.id) or 0)
    cobertura = report.claimed_coverage
    if cobertura in ("complete", COMPLETE_CLAIM):
        completion = COMPLETE_CLAIM
    elif cobertura in ("partial", PARTIAL):
        completion = PARTIAL
    else:
        completion = UNKNOWN
    return Replace(
        snapshot=replace(
            current,
            generation=current.generation + 1,
            revision=plan.revision or current.revision,
            next_id=next_id,
            completion=completion,
            findings=findings,
        )
    )
