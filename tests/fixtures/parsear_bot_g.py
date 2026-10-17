"""Copia versionada del parser del sticky que usa merge.py del repo
agent-work (G) para decidir el merge. Verifica que el sticky del revisor
sigue leíble para el único consumidor externo del bloque.

Origen: agent-work-host/scripts/agent-work/merge.py @ a7256b597a75adbd65f7b
5456734cd821bade88b (último commit que lo tocó: 560497f2).
sha256 del archivo original: 6cde6c1e302ce2809ddffc9df17f6c912d4f96c13cf9f
64ed14b04b8640cc38a.

Si el original cambia, regenerar esta copia y anotar el nuevo origen y
digest. Solo la pieza que G usa del sticky: las dos regex, los dos tipos y
parsear_bot; verbatim (nombres y lógica), sin la maquinaria de compuertas.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

RE_COMPLETION = re.compile(
    r"<!-- ai-review:completion=([0-9a-f]{40}):(complete|partial) -->"
)
RE_FINDINGS = re.compile(r"<!-- ai-review:findings=(\{.*?\}) -->", re.DOTALL)


@dataclass(frozen=True)
class HallazgoBot:
    fid: str
    severidad: str
    estado: str


@dataclass(frozen=True)
class MarcaBot:
    sha: str | None
    estado: str | None
    hallazgos: tuple[HallazgoBot, ...]
    bloque_valido: bool


def parsear_bot(cuerpo: str) -> MarcaBot:
    completion = RE_COMPLETION.search(cuerpo)
    sha, estado = (
        (completion.group(1), completion.group(2)) if completion else (None, None)
    )
    hallazgos: list[HallazgoBot] = []
    bloque_valido = False
    bloque = RE_FINDINGS.search(cuerpo)
    if bloque:
        try:
            datos = json.loads(bloque.group(1))
        except ValueError:
            datos = None
        if (
            isinstance(datos, dict)
            and datos.get("schema") == 3
            and isinstance(datos.get("findings"), list)
        ):
            parsed = []
            completo = True
            for hallazgo in datos["findings"]:
                try:
                    parsed.append(
                        HallazgoBot(
                            str(hallazgo["id"]),
                            str(hallazgo["severity"]),
                            str(hallazgo["status"]["kind"]),
                        )
                    )
                except (KeyError, TypeError):
                    completo = False
                    break
            if completo:
                hallazgos = parsed
                bloque_valido = True
    return MarcaBot(sha, estado, tuple(hallazgos), bloque_valido)
