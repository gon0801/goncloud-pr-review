"""El texto del comentario: veredicto, secciones, alcance, avisos y banners, y el borrado de llaves antes de publicar."""

import html
import re

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
