"""El texto del comentario: veredicto, secciones, alcance, avisos y banners, y el borrado de llaves antes de publicar."""

import html
import re
from typing import TYPE_CHECKING

import review_domain as domain
from review_config import PROVIDERS

if TYPE_CHECKING:
    from review_document import ReviewDocument

from review_domain import (
    DISMISSED,
    OPEN,
    RESOLVED,
    SEVERITIES,
)

SEVERITY_EMOJI = {"Critical": "🔴", "High": "🟠", "Medium": "🟡", "Low": "⚪"}


def redact(text, secrets):
    for secret in secrets:
        if secret and len(secret) >= 8:
            text = text.replace(secret, "[REDACTED]")
    return text


def plural(count, singular):
    return f"{count} {singular}" + ("" if count == 1 else "s")


def verdict_for(merged):
    open_findings = [f for f in merged if f["state"] == OPEN]
    resolved = sum(1 for f in merged if f["state"] == RESOLVED)
    dismissed = sum(1 for f in merged if f["state"] == DISMISSED)
    if not open_findings:
        head = "sin problemas abiertos"
    else:
        counts = [
            (s, sum(1 for f in open_findings if f["severity"] == s)) for s in SEVERITIES
        ]
        head = ", ".join(f"{n} {s}" for s, n in counts if n) + (
            " abierto" if len(open_findings) == 1 else " abiertos"
        )
    tail = ", ".join(
        [plural(resolved, "resuelto")] * bool(resolved)
        + [plural(dismissed, "descartado")] * bool(dismissed)
    )
    return f"**Veredicto:** {head}" + (f" ({tail})." if tail else ".")


def finding_line(finding):
    where = (
        finding["file"]
        if not finding["line"]
        else f"{finding['file']}:{finding['line']}"
    )
    # La ruta de un ancla legada sale del modelo: no puede imitar un marcador.
    where = neutralizar_marcadores(where.replace("`", "'"))
    title = html.escape(finding["title"], quote=False)
    return (
        f"- {SEVERITY_EMOJI[finding['severity']]} {finding['severity']} · `{where}` · "
        f"{title} · {finding['id']}"
    )


MOTIVO_SIN_CAMBIO_PERTINENTE = (
    "el modelo lo da por resuelto, pero no cambió ningún archivo pertinente"
)


def motivo_line(finding_id, no_acreditados):
    """Una línea bajo el hallazgo abierto que el modelo dio por resuelto sin que
    el programa lo acepte. Sangrada: no termina en ` · Fn` como una de hallazgo."""
    if finding_id not in no_acreditados:
        return []
    return [f"  - ↳ {MOTIVO_SIN_CAMBIO_PERTINENTE}"]


def sections_for(merged, new_ids, no_acreditados=()):
    new = {i for i in new_ids}
    fresh = [f for f in merged if f["id"] in new and f["state"] == OPEN]
    still = [f for f in merged if f["id"] not in new and f["state"] == OPEN]
    done = [f for f in merged if f["state"] == RESOLVED]
    dropped = [f for f in merged if f["state"] == DISMISSED]
    parts = ["## Nuevos en este push", ""]
    parts += [finding_line(f) for f in fresh] or ["Ninguno."]
    parts += ["", "## Siguen abiertos", ""]
    parts += [
        line
        for f in still
        for line in [finding_line(f), *motivo_line(f["id"], no_acreditados)]
    ] or ["Ninguno."]
    if done:
        parts += ["", f"<details><summary>Resueltos ({len(done)})</summary>", ""]
        parts += [finding_line(f) for f in done]
        parts += ["", "</details>"]
    if dropped:
        parts += ["", f"<details><summary>Descartados ({len(dropped)})</summary>", ""]
        parts += [finding_line(f) for f in dropped]
        parts += ["", "</details>"]
    return parts


VERDICT_RE = re.compile(r"^\s*\*\*Veredicto:\*\*")


def strip_model_verdict(text):
    """Drop every verdict line the model wrote (the publisher writes the only verdict),
    except inside code fences, where it is quoted content."""
    lines = (text or "").split("\n")
    fenced, opened = set(), None
    for i, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            if opened is None:
                opened = i
            else:
                fenced.update(range(opened, i + 1))
                opened = None
    # An unclosed fence counts as plain text: it can't hide a verdict to strip.
    return "\n".join(
        line
        for i, line in enumerate(lines)
        if i in fenced or not VERDICT_RE.match(line)
    ).strip()


def estimate_cost(prices, usage):
    if not prices or not usage:
        return None
    miss = usage.get("input_tokens", 0) + usage.get("cache_creation_input_tokens", 0)
    hit = usage.get("cache_read_input_tokens", 0)
    out = usage.get("output_tokens", 0)
    return (miss * prices[0] + hit * prices[1] + out * prices[2]) / 1_000_000


# Avisos que escribe el programa en el comentario. La autoridad la da el lugar:
# solo salen de secciones que arma el programa y su texto lo sabe el programa;
# nunca llevan texto del modelo, que va únicamente en su sección ("Detalle del
# revisor" y las explicaciones). Todos salen de aviso_md.
AVISOS_DEL_PROGRAMA = (
    "Revisión incompleta",
    "Memoria conservada sin actualizar",
    "Comentario sin espacio para el estado",
    "No se pudo revisar el commit",
    "Comando no entendido",
    "Comando no aplicado",
    "Revisión en curso",
    "Sin revisión publicada",
)


def aviso_md(etiqueta, texto, nivel=None):
    if not any(etiqueta.startswith(e) for e in AVISOS_DEL_PROGRAMA):
        raise ValueError(f"aviso sin registrar en AVISOS_DEL_PROGRAMA: {etiqueta!r}")
    cabeza = f"> [!{nivel}]\n" if nivel else ""
    return f"{cabeza}> **{etiqueta}:** {texto}"


CAUTION_MARK = "> [!CAUTION]"


def caution_banner(reason, head, has_previous):
    banner = aviso_md(
        f"No se pudo revisar el commit {head[:7]}",
        f"{una_linea_literal(reason)}.",
        "CAUTION",
    )
    if has_previous:
        banner += (
            " Lo de abajo es de la revisión anterior. Se reintenta con el próximo "
            'push o con "Re-run jobs".'
        )
    return banner


ALCANCE_INICIO = "<details><summary>Alcance de la revisión</summary>"


_ENTIDAD_RE = re.compile(r"&(?!amp;|lt;)(?=#?\w+;)")


def prosa_del_modelo(texto):
    """Texto del modelo, listo para ir en la única sección que lo lleva ("Detalle
    del revisor" y las explicaciones). La autoridad la da el lugar, no el texto:
    los avisos del programa solo salen de secciones que arma el programa
    (aviso_md, sections_for y el documento tipado) y nunca contienen texto del modelo,
    así que lo que parezca un aviso dentro de la sección del modelo se lee como
    del modelo. Aquí solo hace falta el escapado básico para que no rompa el resto
    del comentario: el `&` de una referencia de carácter se escapa para que se vea
    literal (`<!--` y `<details` los neutraliza neutralizar_marcadores)."""
    return _ENTIDAD_RE.sub("&amp;", texto)


def una_linea_literal(texto):
    """Causa externa (no del modelo, p. ej. el error del worker) que el programa
    interpola en un aviso suyo: una sola línea, con HTML y markdown escapados para
    que se vea literal y no forme otra línea o etiqueta dentro del aviso."""
    texto = " ".join(str(texto).split())
    texto = html.escape(texto, quote=False)
    return re.sub(r"([\\`*_~\[\]])", r"\\\1", texto)


def neutralizar_marcadores(texto):
    """Prosa del modelo (sale del contenido del PR) dentro del comentario: no
    puede imitar los marcadores ai-review:* ni dejar un comentario HTML abierto
    (todo <!-- se escapa), ni abrir o cerrar un <details>. Idempotente."""
    texto = texto.replace("<!--", "&lt;!--")
    return re.sub(r"<(/?details)", r"&lt;\1", texto, flags=re.IGNORECASE)


NOTICE_TEXT = {
    "incomplete": "El revisor no alcanzó a revisar todo.",
    "capacity-exceeded": "desborde de memoria: se conserva el checkpoint previo.",
    "max-turns": "el revisor se quedó sin turnos antes de terminar",
    "missing-coverage": "el revisor no declaró su cobertura",
    "partial-coverage": "el revisor no alcanzó a revisar todo",
    "invalid-findings": (
        "el revisor no entregó su bloque de hallazgos utilizable; "
        "esta pasada no acredita cobertura y conserva los hallazgos anteriores"
    ),
    "prose-truncated": "El detalle se recortó para conservar el estado de la revisión.",
    "history-truncated": "La revisión anterior se recortó para conservar el estado.",
    "book-omitted": "El libro de cobertura no cupo en el comentario.",
    "explanation-truncated": "Una explicación se recortó para conservar el estado.",
    "failure-truncated": "El detalle del fallo se recortó para conservar el estado.",
}


def _warning_text(warning):
    if warning.code == "budget-exclusions":
        return f"{warning.count} archivo(s) quedaron fuera por tamaño del diff"
    if warning.code == "encoding-exclusions":
        return f"{warning.count} archivo(s) no se pudieron leer como UTF-8"
    return NOTICE_TEXT.get(warning.code, warning.code)


def notice_text(notice):
    cid = notice.comment_id
    if cid is None:
        if notice.kind == "capacity-exceeded":
            return f"> [!CAUTION]\n> {NOTICE_TEXT[notice.kind]}"
        return f"> [!NOTE]\n> {NOTICE_TEXT.get(notice.kind, notice.kind)}"
    if notice.kind == "no-entendido":
        return (
            f"{aviso_md('Comando no entendido', '')}el comentario {cid} menciona `ai-review:` pero no es un "
            "comando que entienda; cada comando va al inicio de una línea: "
            "`ai-review: descartar F3`, `ai-review: descartar F1, F2`, "
            "`ai-review: descartar todo`, `ai-review: explicar F3` o `ai-review: revisar`."
        )
    if notice.kind == "extra":
        return (
            f"{aviso_md('Comando no aplicado', '')}el comentario {cid} trae más de un "
            "comando; solo se lee el primero. Escribe cada comando en su propio "
            "comentario."
        )
    if notice.kind == "todo-tardio":
        return (
            f"{aviso_md('Comando no aplicado', '')}`ai-review: descartar todo` del "
            f"comentario {cid} es de antes de la última revisión con hallazgos nuevos y "
            "no se aplicó; si también quieres descartarlos, comenta de nuevo "
            "`ai-review: descartar todo`."
        )
    if notice.kind == "inexistente" and notice.finding_ids:
        ids = notice.finding_ids
        nombrados = (
            f"el hallazgo {ids[0]}, que no existe"
            if len(ids) == 1
            else f"los hallazgos {', '.join(ids)}, que no existen"
        )
        return (
            f"{aviso_md('Comando no aplicado', '')}el comentario {cid} nombra "
            f"{nombrados}; no se aplicó nada."
        )
    return f"> [!NOTE]\n> {notice.kind}"


def render_document(document: "ReviewDocument", memory: domain.Snapshot,
                    slots: list[tuple[str, str]]) -> list[str]:
    """Formato visible; el codec proporciona cada slot con o sin delimitadores."""
    review = document.review
    lines = []
    if review:
        merged = domain.hallazgos_legacy(memory)
        title = f"### Revisión automática · {PROVIDERS.get(review.provider, {'label': review.provider})['label']} · {review.head_sha[:7]}"
        if review.scope.mode == "incremental" and review.scope.prev_sha:
            title += f" · incremental desde {review.scope.prev_sha[:7]}"
        lines.extend([
            title,
            "",
        ])
        if review.warnings:
            lines.extend(
                aviso_md(
                    "Revisión incompleta",
                    "; ".join(_warning_text(warning) for warning in review.warnings) + ".",
                    "WARNING",
                ).split("\n")
            )
            lines.append("")
        lines.extend([
            verdict_for(merged),
            "",
            *sections_for(merged, review.new_ids, domain.resoluciones_sin_acreditar(memory)),
            "",
            "## Detalle del revisor",
            "",
        ])
    elif document.historical and memory.findings:
        merged = domain.hallazgos_legacy(memory)
        lines.extend([
            verdict_for(merged),
            "",
            *sections_for(merged, (), domain.resoluciones_sin_acreditar(memory)),
            "",
        ])
    for name, text in slots:
        if name == "history":
            lines.append("#### Revisión anterior importada")
        elif name.startswith("explanation-"):
            fid = name.removeprefix("explanation-")
            title = next((f.title for f in memory.findings if f.id == fid), "")
            label = f"## Explicación de {fid}"
            lines.extend(["", label + (f" · {html.escape(title, quote=False)}" if title else ""), ""])
        elif name == "failure":
            lines.extend([
                "> [!CAUTION]",
                f"#### No se pudo revisar el commit {document.failure.head_sha[:7]} · Revisión sin terminar",
            ])
        lines.append(text)
    if review:
        scope = review.scope
        lines.extend([ALCANCE_INICIO, ""])
        if scope.mode == "incremental" and scope.prev_sha:
            delta = (
                "delta no verificable" if scope.changed_count is None
                else f"{scope.changed_count} archivo(s) cambiaron"
            )
            lines.append(f"- Modo: incremental desde {scope.prev_sha[:7]} ({delta})")
        lines.append(f"- Revisados: {scope.reviewed_count} archivo(s)")
        if scope.excluded_count:
            lines.append(f"- Excluidos: {scope.excluded_count}")
            lines.extend(f"  - {entry}" for entry in scope.exclusions)
            if scope.excluded_count > len(scope.exclusions):
                lines.append(f"  - … y {scope.excluded_count - len(scope.exclusions)} más")
        if scope.usage:
            usage = scope.usage
            turns = str(usage.turns) if usage.turns is not None else "?"
            if usage.max_turns:
                turns += f"/{usage.max_turns}"
            lines.append(
                f"- Turnos: {turns} · tokens entrada {usage.input_tokens:,} "
                f"(+{usage.cache_tokens:,} en caché) · salida {usage.output_tokens:,}"
                + (f" · costo aprox ${usage.cost_usd:.3f}" if usage.cost_usd is not None else "")
            )
        lines.extend(["", "</details>"])
    pending = next(
        (
            request for request in reversed(memory.pending_requests)
            if request.kind == "review" and request.state == domain.PENDIENTE
        ),
        None,
    )
    if pending is not None:
        head = pending.target.get("head_sha") or ""
        target = f"el commit `{head[:7]}`" if (isinstance(head, str) and re.fullmatch(r"[0-9a-f]{40}", head)) else "el PR"
        lines.append(
            f"{aviso_md('Revisión en curso', '')}el revisor está revisando {target}; "
            "este comentario se actualiza cuando termine."
        )
    elif not (review or document.historical or document.failure):
        lines.append(
            f"{aviso_md('Sin revisión publicada', '')}este PR no tiene una revisión pendiente ni "
            "publicada; la pide `ai-review: revisar`, o un push cuando el PR está "
            "abierto y listo para revisión."
        )
    for entry in document.notices:
        lines.append(notice_text(entry))
    return lines
