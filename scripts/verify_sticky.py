#!/usr/bin/env python3
"""Verifica un comentario fijo del revisor contra sus invariantes.

Uso:
  python3 scripts/verify_sticky.py --body-file sticky.md [--expect-sha <40hex>]
  gh api ... --jq .body | python3 scripts/verify_sticky.py [--expect-sha ...]

Chequeos (falla cerrado: lo que no se puede verificar, no pasa):
  1. cabecera: MARKER, sha y completion en orden y bien formados.
  2. bloque: la memoria se lee (ni ausente ni corrupta ni futura).
  3. coherencia: el marcador rinde lo que el snapshot declara
     (marker_of(snapshot.completion) == marcador).
  4. sha esperado: si se pide, coincide con el de la cabecera.

Sale 0 si todo pasa; 1 si algo falla. Solo stdlib.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import review  # noqa: E402
import review_domain as domain  # noqa: E402


def falla(mensajes, texto):
    mensajes.append(texto)
    print(f"verify_sticky: FALLA: {texto}")


def main():
    ap = argparse.ArgumentParser(description="Verifica un sticky del revisor.")
    ap.add_argument("--body-file", help="archivo con el cuerpo del comentario")
    ap.add_argument("body", nargs="?", help="cuerpo directo (o por stdin)")
    ap.add_argument("--expect-sha", default="", help="sha que debe declarar")
    args = ap.parse_args()
    if args.body_file:
        body = Path(args.body_file).read_text(encoding="utf-8")
    elif args.body is not None:
        body = args.body
    elif not sys.stdin.isatty():
        body = sys.stdin.read()
    else:
        ap.error("pasame --body-file, el cuerpo o stdin")
    errores = []

    marcador = review.reviewed_completion(body)
    sha = review.reviewed_sha(body)
    if marcador is None or sha is None:
        falla(errores, "cabecera sin MARKER/sha/completion en orden")
    else:
        print(f"verify_sticky: ok: cabecera sha={sha[:8]} completion={marcador}")

    if not errores:
        carga = domain.read_snapshot(body)
        if isinstance(carga, (domain.Missing, domain.Invalid)):
            falla(errores, f"bloque ilegible: {type(carga).__name__}")
        elif isinstance(carga, domain.Future):
            falla(errores, f"esquema futuro v{carga.version}: no verificable")
        else:
            snap = carga.snapshot
            print(
                f"verify_sticky: ok: bloque schema={snap.schema} "
                f"hallazgos={len(snap.findings)} completion={snap.completion}"
            )
            if domain.marker_of(snap.completion) != marcador:
                falla(
                    errores,
                    f"marcador {marcador} no coincide con snapshot ({snap.completion})",
                )
            else:
                print("verify_sticky: ok: marcador coherente con snapshot")

    if args.expect_sha:
        if sha != args.expect_sha:
            falla(errores, f"sha {sha} != esperado {args.expect_sha}")
        else:
            print("verify_sticky: ok: sha esperado")

    if errores:
        print(f"verify_sticky: {len(errores)} falla(s)")
        raise SystemExit(1)
    print("verify_sticky: OK")


if __name__ == "__main__":
    main()
