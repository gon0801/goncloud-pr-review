"""La gramática compartida de los comandos `ai-review:` para ambos modos."""

import re
from dataclasses import dataclass

import review_domain
from review_domain import FINDING_ID_DIGITS


@dataclass(frozen=True)
class Comando:
    accion: str
    ids: frozenset = frozenset()
    todo: bool = False
    extra: bool = False


DISMISS_ALL_WORD = "todo"


DISMISS_TARGETS_RE = re.compile(
    r"(?:\s*(?:[,;]|\b[yeo]\b|F"
    + FINDING_ID_DIGITS
    + r"\b|"
    + DISMISS_ALL_WORD
    + r"\b(?:\s+lo\s+dem[áa]s\b)?))*",
    re.IGNORECASE,
)
DISMISS_FIRST_TOKEN_RE = re.compile(
    r"\s*(?:F" + FINDING_ID_DIGITS + r"\b|" + DISMISS_ALL_WORD + r"\b)",
    re.IGNORECASE,
)


def dismiss_targets(rest):
    """(ids, todo) de lo que sigue a "descartar". Solo el tramo inicial de ids,
    comas, conjunciones y `todo [lo demás]` es el objetivo; el resto es el
    motivo y no cambia el comando (un SHA como c2b1af7 trae "f7"). El primer
    token tiene que ser un id o `todo`. `todo` seguido de una palabra
    ("todo bien") es prosa y nunca descarta todo."""
    if not DISMISS_FIRST_TOKEN_RE.match(rest):
        return set(), False
    end = DISMISS_TARGETS_RE.match(rest).end()
    lead, tail = rest[:end], rest[end:]
    ids = {
        f"F{int(n)}"
        for n in re.findall(rf"\bF({FINDING_ID_DIGITS})", lead, re.IGNORECASE)
    }
    todo = bool(re.search(r"\btodo\b", lead, re.IGNORECASE)) and not re.match(
        r"\s*\w", tail
    )
    return ids, todo


def _sangria(linea):
    """Columnas de sangría al inicio de la línea, con tabuladores de 4."""
    columna = 0
    for caracter in linea:
        if caracter == " ":
            columna += 1
        elif caracter == "\t":
            columna += 4 - columna % 4
        else:
            break
    return columna


def _lineas_utiles(cuerpo):
    cerca = None
    for linea in (cuerpo or "").splitlines():
        m = re.match(r" {0,3}(`{3,}|~{3,})", linea)
        if cerca is None:
            if m:
                cerca = (m.group(1)[0], len(m.group(1)))
                continue
            if linea.lstrip().startswith(">") or _sangria(linea) >= 4:
                continue
            yield linea
        elif (
            m
            and m.group(1)[0] == cerca[0]
            and len(m.group(1)) >= cerca[1]
            and re.fullmatch(r" {0,3}(`{3,}|~{3,})\s*", linea)
        ):
            cerca = None


def leer_comando(cuerpo):
    lineas = list(_lineas_utiles(cuerpo))
    candidatas = [ln for ln in lineas if re.match(r" {0,3}ai-review:", ln, re.IGNORECASE)]
    if not candidatas:
        if any(re.search(r"ai-review:", ln, re.IGNORECASE) for ln in lineas):
            return Comando(review_domain.ACCION_NO_ENTENDIDA)
        return None
    primera, extra = candidatas[0], len(candidatas) > 1
    m = re.match(
        rf" {{0,3}}ai-review:\s*explicar\s+F({FINDING_ID_DIGITS})\b",
        primera,
        re.IGNORECASE,
    )
    if m:
        return Comando("explicar", frozenset({f"F{int(m.group(1))}"}), extra=extra)
    if re.fullmatch(r" {0,3}ai-review:\s*revisar\s*", primera, re.IGNORECASE):
        return Comando("revisar", extra=extra)
    m = re.match(r" {0,3}ai-review:\s*descartar\s+(.+)", primera, re.IGNORECASE)
    if m:
        ids, todo = dismiss_targets(m.group(1))
        if todo:
            return Comando("descartar", frozenset(), True, extra)
        if ids:
            return Comando("descartar", frozenset(ids), False, extra)
    return Comando(review_domain.ACCION_NO_ENTENDIDA, extra=extra)
