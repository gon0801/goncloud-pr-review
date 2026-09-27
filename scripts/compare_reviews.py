"""Compara revisiones capturadas sobre los mismos commits. Offline: no llama
modelos ni publica comentarios; trabaja solo sobre salidas ya adjudicadas.

Uso:
  python3 scripts/compare_reviews.py --corpus corpus.json \
      --observations observaciones.json --judgments adjudicaciones.json \
      --output informe.json

Entradas (JSON):
- corpus:    {"casos": [{caso, repo, base, head, producto, configuracion, intento}]}
- observaciones: {"observaciones": [{caso, repo, base, head, producto, configuracion,
    intento, resultado, cobertura, duracion_s, turnos, costo_usd (null = desconocido),
    hallazgos: [{id, titulo, ruta, resuelto}]}]}
- judgments: {"adjudicaciones": [{caso, hallazgo, veredicto (valid | false_positive |
    duplicate | unresolved), defecto?, duplicado_de?, resuelto_real?}],
    "defectos": {caso: [ids de defectos conocidos]}}  (opcional)

Reglas: sólo se comparan pares con base y head exactos; las filas de
adjudicación duplicadas se rechazan; unresolved sale del denominador de
precisión y se informa cuántos se excluyeron; el costo ausente se informa
como desconocido (nunca cero) y la recuperación sólo se calcula cuando existe
un conjunto de defectos conocidos adjudicados.
"""

import argparse
import json
import sys
from pathlib import Path


def falla(mensaje):
    print(f"compare_reviews: {mensaje}", file=sys.stderr)
    raise SystemExit(2)


def cargar(ruta, clave):
    try:
        datos = json.loads(ruta.read_text())
    except FileNotFoundError:
        falla(f"no existe el archivo {ruta}")
    except json.JSONDecodeError as exc:
        falla(f"{ruta} no es JSON válido: {exc}")
    if clave not in datos or not isinstance(datos[clave], list):
        falla(f"{ruta} debe contener una lista '{clave}'")
    return datos[clave]


def main():
    parser = argparse.ArgumentParser(
        description="Compara revisiones capturadas (offline, sin modelos)."
    )
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--judgments", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    casos = cargar(args.corpus, "casos")
    observaciones = cargar(args.observations, "observaciones")
    filas = cargar(args.judgments, "adjudicaciones")
    try:
        defectos = json.loads(args.judgments.read_text()).get("defectos") or {}
    except json.JSONDecodeError:
        falla(f"{args.judgments} no es JSON válido")

    corpus = {}
    for caso in casos:
        ident = caso.get("caso")
        if not ident or ident in corpus:
            falla(f"caso ausente o duplicado en el corpus: {ident!r}")
        corpus[ident] = caso

    vistas = {}
    for obs in observaciones:
        ident = obs.get("caso")
        if ident not in corpus:
            falla(f"observación de caso desconocido: {ident!r}")
        if ident in vistas:
            falla(f"observación duplicada para el caso {ident}")
        caso = corpus[ident]
        if (obs.get("base"), obs.get("head")) != (caso["base"], caso["head"]):
            falla(
                f"par de distinto SHA para el caso {ident}: "
                f"corpus ({caso['base'][:12]}, {caso['head'][:12]}) vs "
                f"observación ({str(obs.get('base'))[:12]}, {str(obs.get('head'))[:12]}); "
                "sólo se comparan salidas del mismo SHA"
            )
        for clave in (
            "repo",
            "producto",
            "configuracion",
            "intento",
            "resultado",
            "cobertura",
            "duracion_s",
            "turnos",
            "costo_usd",
            "hallazgos",
        ):
            if clave not in obs:
                falla(f"observación del caso {ident} sin la clave requerida {clave!r}")
        hallazgos = {}
        for h in obs["hallazgos"]:
            if h["id"] in hallazgos:
                falla(f"hallazgo duplicado {h['id']!r} en el caso {ident}")
            hallazgos[h["id"]] = h
        vistas[ident] = {"obs": obs, "hallazgos": hallazgos}

    vistos = set()
    conteo = {"valid": 0, "false_positive": 0, "duplicate": 0, "unresolved": 0}
    detectados = set()
    falsos_resueltos = 0
    for fila in filas:
        ident, hallazgo = fila.get("caso"), fila.get("hallazgo")
        llave = (ident, hallazgo)
        if llave in vistos:
            falla(f"fila de adjudicación duplicada para {ident}/{hallazgo}")
        if ident not in vistas:
            falla(f"adjudicación de caso desconocido: {ident!r}")
        if hallazgo not in vistas[ident]["hallazgos"]:
            falla(f"adjudicación de hallazgo desconocido: {ident}/{hallazgo}")
        veredicto = fila.get("veredicto")
        if veredicto not in conteo:
            falla(
                f"veredicto inválido {veredicto!r} en {ident}/{hallazgo}; "
                "usa valid, false_positive, duplicate o unresolved"
            )
        vistos.add(llave)
        conteo[veredicto] += 1
        if veredicto == "valid" and fila.get("defecto"):
            detectados.add(fila["defecto"])
        if veredicto == "duplicate":
            original = fila.get("duplicado_de")
            if original not in vistas[ident]["hallazgos"]:
                falla(
                    f"duplicate sin original válido: {ident}/{hallazgo} -> {original!r}"
                )
        h = vistas[ident]["hallazgos"][hallazgo]
        if h.get("resuelto") and fila.get("resuelto_real") is False:
            falsos_resueltos += 1

    validos = conteo["valid"]
    denominador = validos + conteo["false_positive"] + conteo["duplicate"]
    excluidos = conteo["unresolved"]
    precision = round(validos / denominador, 4) if denominador else None

    total_hallazgos = sum(len(v["hallazgos"]) for v in vistas.values())
    sin_adjudicar = total_hallazgos - len(vistos)

    conjunto = {c: set(d) for c, d in defectos.items() if c in vistas}
    if conjunto:
        conocidos = set().union(*conjunto.values())
        omitidos = sorted(conocidos - detectados)
        recuperacion = round(len(detectados & conocidos) / len(conocidos), 4)
        defectos_informe = {
            "conjunto_presente": True,
            "conocidos": len(conocidos),
            "detectados": len(detectados & conocidos),
            "omitidos": omitidos,
            "recuperacion": recuperacion,
        }
    else:
        defectos_informe = {
            "conjunto_presente": False,
            "conocidos": 0,
            "detectados": 0,
            "omitidos": "desconocido",
            "recuperacion": "desconocido",
        }

    costos = [
        o["obs"]["costo_usd"]
        for o in vistas.values()
        if o["obs"]["costo_usd"] is not None
    ]
    informe = {
        "casos": len(corpus),
        "observaciones": len(vistas),
        "hallazgos": {
            "total": total_hallazgos,
            "validos": validos,
            "falsos_positivos": conteo["false_positive"],
            "duplicados": conteo["duplicate"],
            "sin_resolver": excluidos,
            "sin_adjudicar": sin_adjudicar,
        },
        "precision": {
            "valor": precision,
            "validos": validos,
            "denominador": denominador,
            "excluidos_sin_resolver": excluidos,
        },
        "defectos_conocidos": defectos_informe,
        "falsos_resueltos": falsos_resueltos,
        "cobertura_declarada": {
            i: v["obs"]["cobertura"] for i, v in sorted(vistas.items())
        },
        "tiempo": {
            "duracion_total_s": round(
                sum(o["obs"]["duracion_s"] for o in vistas.values()), 3
            ),
            "duracion_promedio_s": round(
                sum(o["obs"]["duracion_s"] for o in vistas.values()) / len(vistas), 3
            )
            if vistas
            else None,
            "turnos_total": sum(o["obs"]["turnos"] for o in vistas.values()),
        },
        "costo": {
            "usd_conocido": round(sum(costos), 6) if costos else None,
            "desconocidos": sum(
                1 for o in vistas.values() if o["obs"]["costo_usd"] is None
            ),
        },
    }
    args.output.write_text(json.dumps(informe, indent=2, ensure_ascii=False) + "\n")
    print(f"compare_reviews: informe escrito en {args.output}")


if __name__ == "__main__":
    main()
