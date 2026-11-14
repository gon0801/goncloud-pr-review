"""La gramática del comando `ai-review: descartar` que comparten los dos modos."""

import re

from review_domain import FINDING_ID_DIGITS


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
