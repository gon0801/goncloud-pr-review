"""Particiona la batería de unittest bajo tests/ para CI.

Uso:
  python3 scripts/run_test_shard.py --shard 0/2            # corre el shard 0 de 2
  python3 scripts/run_test_shard.py --shard 1/2 --manifest # lista los IDs sin correr
  python3 scripts/run_test_shard.py --verify-partition 2   # candado de unión

Los casos se descubren bajo el directorio de pruebas, se ordenan por ID y la
posición i de esa lista va al shard `i % N` (numeración de shards desde cero).
"""

import argparse
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def falla(mensaje):
    print(f"run_test_shard: {mensaje}", file=sys.stderr)
    raise SystemExit(2)


def parse_shard(valor):
    m = re.fullmatch(r"(\d+)/(\d+)", valor)
    if not m:
        falla(
            f"--shard espera i/N con enteros (numeración desde cero), recibí {valor!r}"
        )
    i, n = int(m.group(1)), int(m.group(2))
    if n < 1:
        falla(f"--shard: N debe ser >= 1, recibí {n}")
    if i >= n:
        falla(f"--shard: i debe ser < N, recibí {i}/{n}")
    return i, n


def hojas(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from hojas(item)
        else:
            yield item


def descubrir_ids(tests_dir):
    suite = unittest.TestLoader().discover(
        start_dir=str(tests_dir), top_level_dir=str(tests_dir)
    )
    return sorted(t.id() for t in hojas(suite))


def shard_de(ids, i, n):
    return [tid for posicion, tid in enumerate(ids) if posicion % n == i]


def correr_shard(args, i, n):
    ids = descubrir_ids(args.tests_dir)
    mios = shard_de(ids, i, n)
    if not mios:
        falla(f"el shard {i}/{n} quedaría vacío (total {len(ids)} pruebas)")
    if args.manifest:
        print("\n".join(mios))
        return
    suite = unittest.TestLoader().discover(
        start_dir=str(args.tests_dir), top_level_dir=str(args.tests_dir)
    )
    elegidos = [t for t in hojas(suite) if t.id() in set(mios)]
    if len(elegidos) != len(mios):
        falla(
            f"el descubrimiento no reproduce los IDs del manifiesto: "
            f"{len(elegidos)} casos vs {len(mios)} IDs"
        )
    resultado = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(elegidos))
    raise SystemExit(0 if resultado.wasSuccessful() else 1)


def verificar_particion(args):
    n = args.verify_partition
    if n < 1:
        falla(f"--verify-partition: N debe ser >= 1, recibí {n}")
    ids = descubrir_ids(args.tests_dir)
    shards = [shard_de(ids, i, n) for i in range(n)]
    vacios = [i for i, s in enumerate(shards) if not s]
    if vacios:
        falla(f"particiones vacías en {vacios} (total {len(ids)} pruebas)")
    unidos = [tid for s in shards for tid in s]
    if len(unidos) != len(set(unidos)):
        falla("hay IDs duplicados entre particiones")
    if sorted(unidos) != ids:
        falla("la unión de particiones no coincide con el descubrimiento completo")
    for i, s in enumerate(shards):
        print(f"shard {i}/{n}: {len(s)}")
    print(f"total: {len(ids)}")
    print("verify: OK")


def main():
    parser = argparse.ArgumentParser(
        description="Corre o verifica una partición de la batería de unittest bajo tests/."
    )
    parser.add_argument(
        "--shard",
        metavar="i/N",
        help="corre la partición i de N (numeración desde cero)",
    )
    parser.add_argument(
        "--manifest",
        action="store_true",
        help="con --shard: lista los IDs del shard sin correr las pruebas",
    )
    parser.add_argument(
        "--verify-partition",
        type=int,
        metavar="N",
        help="verifica que N particiones cubren la batería completa sin duplicados",
    )
    parser.add_argument(
        "--tests-dir",
        type=Path,
        default=ROOT / "tests",
        help="directorio de pruebas (default: tests/ del repo)",
    )
    args = parser.parse_args()

    modos = [args.shard is not None, args.verify_partition is not None]
    if sum(modos) != 1:
        falla("elegí exactamente un modo: --shard i/N o --verify-partition N")

    if args.shard is not None:
        i, n = parse_shard(args.shard)
        correr_shard(args, i, n)
    else:
        verificar_particion(args)


if __name__ == "__main__":
    main()
