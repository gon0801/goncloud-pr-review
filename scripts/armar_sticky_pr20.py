#!/usr/bin/env python3
"""Arma el sticky completo del caso representativo del PR #20 de hermes.

Uso:
  python3 scripts/armar_sticky_pr20.py --salida /tmp/sticky-pr20-armado.md

Lee el sticky real del PR #20 de goncloud-hermes
(tests/fixtures/pr20-sticky-hermes.md) y le aplica la ronda extra de
hallazgos (tests/fixtures/pr20-ronda-extra.json) con la misma aceptación del
dominio que usa el coordinador. Escribe el cuerpo completo que el bot habría
publicado de no desbordar el tope viejo: la prueba de comentario real (T16)
lo publica y lo verifica con scripts/verify_sticky.py. Solo stdlib; no toca
GitHub.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import review  # noqa: E402
import review_domain as domain  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"


def cargar_ronda():
    body = (FIXTURES / "pr20-sticky-hermes.md").read_text(encoding="utf-8")
    ronda = json.loads((FIXTURES / "pr20-ronda-extra.json").read_text(encoding="utf-8"))
    return body, ronda


def aceptar_ronda(body, ronda):
    previo = domain.read_snapshot(body).snapshot
    observaciones = [
        domain.observation_de_entrada(e) for e in ronda["model_block"]["findings"]
    ]
    revision = domain.Revision(
        base_sha=ronda["base_sha"],
        head_sha=ronda["head_sha"],
        policy_digest=previo.revision.policy_digest,
    )
    facts = domain.RepositoryFacts(
        revision=revision,
        changed_paths=tuple(ronda["changed_paths"]),
        delta_calculado=True,
    )
    plan = domain.ReviewPlan(
        revision=revision,
        changed_paths=tuple(ronda["changed_paths"]),
        policy=domain.ReviewPolicy(),
    )
    report = domain.validar_reporte(observaciones, domain.COMPLETE_CLAIM, facts)
    decision = domain.accept_report(previo, plan, report)
    if not isinstance(decision, domain.Replace):
        raise SystemExit(f"armar_sticky: la ronda no se aceptó ({decision})")
    return decision.snapshot


def armar():
    body, ronda = cargar_ronda()
    previos = {f.id for f in domain.read_snapshot(body).snapshot.findings}
    snapshot = aceptar_ronda(body, ronda)
    lineas = body.split("\n")
    resto = "\n".join(lineas[1:]) if lineas and lineas[0] == review.MARKER else body
    resto = review.strip_findings_block(resto, last=False).strip()
    visible = review.revision_visible(
        dict(ronda, review_provider="opencode-go", model_ok=True),
        snapshot,
        previos,
        ronda["head_sha"],
    )
    bloque = domain.encode_snapshot(snapshot).block
    cuerpo = visible(bloque, resto)
    return cuerpo, snapshot


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--salida", default="/tmp/sticky-pr20-armado.md")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cuerpo, snapshot = armar()
    Path(args.salida).write_text(cuerpo, encoding="utf-8")
    abiertos = sum(
        1 for f in snapshot.findings if isinstance(f.status, domain.StatusOpen)
    )
    print(f"armar_sticky: {args.salida} ({len(cuerpo.encode('utf-8'))} bytes)")
    print(
        f"armar_sticky: hallazgos={len(snapshot.findings)} "
        f"(abiertos={abiertos}, cerrados={len(snapshot.findings) - abiertos}) "
        f"completion={snapshot.completion}"
    )
    print(
        "armar_sticky: verificación con "
        f"python3 scripts/verify_sticky.py --body-file {args.salida} "
        f"--expect-sha {snapshot.revision.head_sha}"
    )


if __name__ == "__main__":
    main()
