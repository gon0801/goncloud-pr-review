"""El texto del comentario: veredicto, secciones, alcance, avisos y banners, y el borrado de llaves antes de publicar."""

import html
import re

import review_domain
from review_config import PROVIDERS
from review_context import trim_utf8
from review_domain import (
    DISMISSED,
    OPEN,
    RESOLVED,
    SEVERITIES,
    split_coverage,
    strip_model_findings_block,
)
from review_sticky import (
    COMPLETION_PREFIX,
    COVERED_PREFIX,
    MARKER,
    SHA_PREFIX,
    libro_a_linea,
)


COMMENT_LIMIT = 50000
GITHUB_COMMENT_MAX = 65000


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


def scope_lines(result, manifest, provider):
    scope = [f"- Revisados: {len(manifest['reviewed'])} archivo(s)"]
    if manifest["excluded"]:
        shown = manifest["excluded"][:40]
        scope.append(f"- Excluidos: {len(manifest['excluded'])}")
        # La ruta sale del PR: tampoco puede abrir un comentario HTML.
        scope += [
            f"  - `{neutralizar_marcadores(e['path'][:200])}` ({e['reason']})"
            for e in shown
        ]
        if len(manifest["excluded"]) > len(shown):
            scope.append(f"  - … y {len(manifest['excluded']) - len(shown)} más")
    usage = (result or {}).get("usage") or {}
    if usage:
        cost = estimate_cost(provider["prices"], usage)
        cap = manifest.get("max_turns")
        turns = (
            f"{(result or {}).get('num_turns', '?')}/{cap}"
            if cap
            else f"{(result or {}).get('num_turns', '?')}"
        )
        scope.append(
            f"- Turnos: {turns} · tokens entrada "
            f"{usage.get('input_tokens', 0) + usage.get('cache_creation_input_tokens', 0):,}"
            f" (+{usage.get('cache_read_input_tokens', 0):,} en caché) · salida {usage.get('output_tokens', 0):,}"
            + (f" · costo aprox ${cost:.3f}" if cost is not None else "")
        )
    return scope


def _marcador_cobertura(warnings, findings):
    derivada = findings.get("completion") if findings else None
    if derivada is not None:
        return review_domain.marker_of(derivada)
    return "partial" if warnings else "complete"


AVISO_DE_PRESUPUESTO = "\n\n_(Revisión recortada al presupuesto de capacidad.)_"


def prosa_en_presupuesto(armar, prosa, budget):
    """La prosa recortada para que armar(prosa), el comentario entero, quepa en
    el presupuesto por caracteres y por bytes; None si ni el cuerpo sin prosa
    cabe (el presupuesto se rechaza y mandan los topes actuales). armar une la
    prosa como un elemento más, así que el cuerpo mide fijo + prosa."""
    fijo = armar("")
    chars = budget.comment_max_chars - len(fijo)
    octetos = budget.comment_max_bytes - len(fijo.encode("utf-8"))
    if chars < 0 or octetos < 0:
        return None
    if len(prosa) <= chars and len(prosa.encode("utf-8")) <= octetos:
        return prosa
    aviso = AVISO_DE_PRESUPUESTO
    if len(aviso) > chars or len(aviso.encode("utf-8")) > octetos:
        aviso = ""  # margen menor que el aviso: se recorta sin él
    recorte = prosa[: chars - len(aviso)]
    return trim_utf8(recorte, octetos - len(aviso.encode("utf-8")), "") + aviso


def _cuerpo_de_aviso_de_capacidad(result, manifest, provider, review, aviso):
    """El cuerpo del comentario cuando el fijo no cabe: aviso visible, sin
    bloque de estado y sin marcar sha ni cobertura (el camino del desborde
    de memoria, también para un comentario sin espacio). Nunca corta a
    ciegas: un marcador oculto partido rompería el estado del próximo push."""

    def recorte():
        return (
            review[:disponible]
            + "\n\n_(Revisión recortada por el límite de tamaño de comentarios de GitHub.)_"
        )

    partes = [
        MARKER,
        aviso,
        "",
        DETALLE_DEL_REVISOR,
        "",
        "",
        "",
        ALCANCE_INICIO,
        "",
        *scope_lines(result, manifest, provider),
        "",
        "</details>",
    ]
    overhead = len("\n".join(partes)) + 100  # margen para la nota de recorte
    disponible = max(0, GITHUB_COMMENT_MAX - overhead)
    partes[5] = (recorte() if len(review) > disponible else review) or (
        "_El revisor no devolvió texto._"
    )
    return "\n".join(partes)


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


AVISO_SIN_ESPACIO = aviso_md(
    "Comentario sin espacio para el estado",
    "el cuerpo fijo del sticky no cabe en el presupuesto del comentario (con y "
    "sin libro de cobertura); esta revisión no se marca como revisada ni "
    "confirma cobertura.",
    "CAUTION",
)


def compose(result, manifest, *, sha, provider, findings=None, budget=None, libro=None):
    provider = PROVIDERS[provider]
    text = (result or {}).get("result") or ""
    review, coverage, detail = split_coverage(text)
    budget_cut = [e for e in manifest["excluded"] if e["reason"] == "budget"]

    warnings = []
    reviewed_any = bool(manifest["reviewed"])
    if (result or {}).get("subtype") == "error_max_turns":
        warnings.append("el revisor se quedó sin turnos antes de terminar")
    if coverage is None and reviewed_any:
        warnings.append("el revisor no declaró su cobertura")
    elif coverage == "partial":
        # El aviso dice solo lo que sabe el programa. El motivo que dio el modelo
        # es texto suyo y va en su sección, tras su prosa.
        warnings.append("el revisor no alcanzó a revisar todo")
        if detail:
            review = (
                f"{review}\n\n" if review else ""
            ) + f"Cobertura declarada por el revisor: parcial | {detail}"
    if budget_cut:
        warnings.append(
            f"{len(budget_cut)} archivo(s) quedaron fuera por tamaño del diff"
        )
    encoding_omitted = [e for e in manifest["excluded"] if e["reason"] == "encoding"]
    if encoding_omitted:
        warnings.append(
            f"{len(encoding_omitted)} archivo(s) no se pudieron leer como UTF-8"
        )
    if findings is not None and not findings.get("model_ok", True) and reviewed_any:
        warnings.append(
            "el revisor no entregó su bloque de hallazgos utilizable; "
            "esta pasada no acredita cobertura y conserva los hallazgos anteriores"
        )

    if findings is not None and findings.get("merged"):
        return compose_with_findings(
            result,
            manifest,
            sha=sha,
            provider=provider,
            findings=findings,
            review=review,
            warnings=warnings,
            budget=budget,
            libro=libro,
        )

    if findings is not None:
        review = strip_model_findings_block(review)
    review = neutralizar_marcadores(prosa_del_modelo(review))
    parts = [
        MARKER,
        f"{SHA_PREFIX}{sha} -->",
        f"{COMPLETION_PREFIX}{sha}:{_marcador_cobertura(warnings, findings)} -->",
    ]
    linea_libro = libro_a_linea(sha, libro) if libro is not None else None
    if linea_libro is not None:
        parts.insert(3, linea_libro)
    if findings is not None:
        parts.append(findings["block"])
    parts += [f"### Revisión automática · {provider['label']} · {sha[:7]}", ""]
    if warnings:
        parts += [
            *aviso_md(
                "Revisión incompleta", "; ".join(warnings) + ".", "WARNING"
            ).split("\n"),
            "",
        ]
    # El texto del modelo va en una sola sección, rotulada, también aquí.
    parts += [DETALLE_DEL_REVISOR, ""]
    scope = [
        ALCANCE_INICIO,
        "",
        *scope_lines(result, manifest, provider),
        "",
        "</details>",
    ]
    if not manifest["reviewed"]:
        review = (
            review
            or "No hay archivos revisables en este PR (todo quedó excluido por filtro)."
        )
    review = review or "_El revisor no devolvió texto._"

    def armar(prosa):
        return "\n".join(parts + [prosa, ""] + scope)

    if budget is not None:
        prosa = prosa_en_presupuesto(armar, review, budget)
        if prosa is not None:
            return armar(prosa)[:GITHUB_COMMENT_MAX]
        if linea_libro is not None:
            # El estado manda sobre el libro: la línea es prescindible (sin
            # ella la siguiente pasada revisa full, que es seguro) y el corte
            # a ciegas podría partir el bloque de estado.
            def armar_sin_libro(prosa):
                cuerpo = [p for p in parts if p is not linea_libro]
                return "\n".join(cuerpo + [prosa, ""] + scope)

            prosa = prosa_en_presupuesto(armar_sin_libro, review, budget)
            if prosa is not None:
                return armar_sin_libro(prosa)[:GITHUB_COMMENT_MAX]
        return _cuerpo_de_aviso_de_capacidad(
            result, manifest, provider, review, AVISO_SIN_ESPACIO
        )
    if len(review) > COMMENT_LIMIT:
        review = (
            review[:COMMENT_LIMIT]
            + "\n\n_(Revisión recortada por el límite de tamaño de comentarios de GitHub.)_"
        )
    return armar(review)[:GITHUB_COMMENT_MAX]


def compose_with_findings(
    result,
    manifest,
    *,
    sha,
    provider,
    findings,
    review,
    warnings,
    budget=None,
    libro=None,
):
    merged, new_ids, block = (
        findings["merged"],
        findings.get("new_ids", []),
        findings["block"],
    )
    review = neutralizar_marcadores(
        prosa_del_modelo(strip_model_verdict(strip_model_findings_block(review)))
    )
    no_acreditados = tuple(findings.get("no_acreditados", ()))
    sections = sections_for(merged, new_ids, no_acreditados)
    if isinstance(block, review_domain.CapacityExceeded):
        # Memoria íntegra que no cabe: no se publica bloque ni se marca el
        # commit como revisado; sólo el aviso visible, dentro del tope.
        aviso = aviso_md(
            "Memoria conservada sin actualizar",
            f"el estado ({block.needed} bytes) no cabe en el bloque de "
            f"{block.limit} bytes; esta revisión no se marca como revisada ni "
            "confirma cobertura.",
            "CAUTION",
        )
        return _cuerpo_de_aviso_de_capacidad(result, manifest, provider, review, aviso)
    title = f"### Revisión automática · {provider['label']} · {sha[:7]}"
    if manifest.get("mode") == "incremental" and manifest.get("prev_sha"):
        title += f" · incremental desde {manifest['prev_sha'][:7]}"
    parts = [
        MARKER,
        f"{SHA_PREFIX}{sha} -->",
        f"{COMPLETION_PREFIX}{sha}:{_marcador_cobertura(warnings, findings)} -->",
        block,
        title,
        "",
    ]
    linea_libro = libro_a_linea(sha, libro) if libro is not None else None
    if linea_libro is not None:
        parts.insert(3, linea_libro)
    if warnings:
        parts += [
            *aviso_md(
                "Revisión incompleta", "; ".join(warnings) + ".", "WARNING"
            ).split("\n"),
            "",
        ]
    parts += [verdict_for(merged), ""]
    parts += sections
    if not review:
        if not manifest["reviewed"]:
            review = "No hubo archivos revisables en este push; los hallazgos anteriores se conservan."
        elif findings.get("model_ok", True):
            review = "Sin hallazgos nuevos en este push."
        else:
            review = "_El revisor no devolvió texto._"
    parts += ["", "## Detalle del revisor", ""]
    # Esta sección es del modelo y el programa no escribe en ella: si su prosa
    # dice "resuelto" de un hallazgo que el veredicto deja abierto, la
    # explicación está en la línea del hallazgo, en "Siguen abiertos".
    i_prosa = len(parts)
    parts += [review, ""]
    scope = scope_lines(result, manifest, provider)
    if manifest.get("mode") == "incremental" and manifest.get("prev_sha"):
        cambiados = manifest.get("changed_files")
        delta = (
            "delta no verificable"
            if cambiados is None
            else f"{len(cambiados)} archivo(s) cambiaron"
        )
        scope.insert(
            0, f"- Modo: incremental desde {manifest['prev_sha'][:7]} ({delta})"
        )
    parts += [
        ALCANCE_INICIO,
        "",
        *scope,
        "",
        "</details>",
    ]

    def armar(prosa):
        return "\n".join(parts[:i_prosa] + [prosa] + parts[i_prosa + 1 :])

    # El checkpoint confirmado jamás se recorta: el estado manda sobre el
    # libro (la línea es prescindible: sin ella la siguiente pasada revisa
    # full), y si el fijo no cabe ni sin libro manda el aviso de capacidad,
    # nunca el corte a ciegas que podría partir el bloque de estado.
    if budget is not None:
        prosa = prosa_en_presupuesto(armar, review, budget)
        if prosa is not None:
            return armar(prosa)[:GITHUB_COMMENT_MAX]
        if linea_libro is not None:

            def armar_sin_libro(prosa):
                cuerpo = [p for p in parts if p is not linea_libro]
                # sin libro la prosa vive en i_prosa - 1: ocupa ESE hueco y la
                # cola sigue en i_prosa, o el fijo mediría la prosa completa.
                return "\n".join(cuerpo[: i_prosa - 1] + [prosa] + cuerpo[i_prosa:])

            prosa = prosa_en_presupuesto(armar_sin_libro, review, budget)
            if prosa is not None:
                return armar_sin_libro(prosa)[:GITHUB_COMMENT_MAX]
        return _cuerpo_de_aviso_de_capacidad(
            result, manifest, provider, review, AVISO_SIN_ESPACIO
        )
    budget_prosa = max(0, COMMENT_LIMIT - len(block) - len("\n".join(sections)))
    if len(review) > budget_prosa:
        review = (
            review[:budget_prosa]
            + "\n\n_(Revisión recortada por el límite de tamaño de comentarios de GitHub.)_"
        )
    return armar(review)[:GITHUB_COMMENT_MAX]


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


def strip_caution_banner(text):
    """Drop a previously-inserted caution banner so a new one replaces it instead of stacking."""
    lines = text.split("\n")
    # El bloque de memoria va entre la cabecera y el aviso: al quitarlo queda
    # una línea vacía antes del aviso.
    inicio = next((k for k, linea in enumerate(lines) if linea), len(lines))
    if inicio == len(lines) or lines[inicio] != CAUTION_MARK:
        return text
    i = inicio + 1
    while i < len(lines) and lines[i].startswith(">"):
        i += 1
    while i < len(lines) and lines[i] == "":
        i += 1
    return "\n".join(lines[i:])


def insert_caution_banner(sticky_body, banner):
    lines = sticky_body.split("\n")
    idx = 0
    if idx < len(lines) and lines[idx] == MARKER:
        idx += 1
    if idx < len(lines) and lines[idx].startswith(SHA_PREFIX):
        idx += 1
    if idx < len(lines) and lines[idx].startswith(COMPLETION_PREFIX):
        idx += 1
    if idx < len(lines) and lines[idx].startswith(COVERED_PREFIX):
        idx += 1
    prefix = lines[:idx]
    rest = strip_caution_banner("\n".join(lines[idx:]))
    head = "\n".join(prefix) + "\n" if prefix else ""
    return head + banner + "\n\n" + rest


def cuerpo_publicable(cuerpo, bloque):
    """Una sola fuente de verdad para "no cabe": un cuerpo solo es
    publicable si read_snapshot lo lee (Valid o Legacy: el formato legado es
    memoria legítima) y trae EXACTAMENTE el bloque codificado. El aviso de
    capacidad (sin bloque) o un marcador partido no se escriben nunca:
    borrarían o romperían la memoria del sticky."""
    carga = review_domain.read_snapshot(cuerpo or "")
    if isinstance(carga, (review_domain.Valid, review_domain.Legacy)):
        return carga.block == bloque
    return False


DETALLE_DEL_REVISOR = "## Detalle del revisor"
ALCANCE_INICIO = "<details><summary>Alcance de la revisión</summary>"


_ENTIDAD_RE = re.compile(r"&(?!amp;)(?=#?\w+;)")


def prosa_del_modelo(texto):
    """Texto del modelo, listo para ir en la única sección que lo lleva ("Detalle
    del revisor" y las explicaciones). La autoridad la da el lugar, no el texto:
    los avisos del programa solo salen de secciones que arma el programa
    (aviso_md, sections_for, estado_visible) y nunca contienen texto del modelo,
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
    return re.sub(r"<(/?details)", r"&lt;\1", texto, flags=re.I)
