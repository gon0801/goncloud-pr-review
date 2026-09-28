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

Reglas: la identidad de una observación es la clave completa
(caso, producto, configuracion, intento); varias observaciones del mismo caso
se aceptan si la clave difiere y la clave completa repetida se rechaza. La
adjudicación es (caso, hallazgo): un ID de hallazgo no puede repetirse entre
observaciones del mismo caso. Sólo se comparan pares con base y head exactos;
las filas de adjudicación duplicadas se rechazan; unresolved sale del
denominador de precisión y se informa cuántos se excluyeron; el costo ausente
se informa como desconocido (nunca cero) y la recuperación sólo se calcula
cuando existe un conjunto de defectos conocidos adjudicados.
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
    indice_hallazgos = {}
    for obs in observaciones:
        ident = obs.get("caso")
        if ident not in corpus:
            falla(f"observación de caso desconocido: {ident!r}")
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
        clave_obs = (
            ident,
            obs["producto"],
            obs["configuracion"],
            obs["intento"],
        )
        if clave_obs in vistas:
            falla(
                f"observación duplicada para el caso {ident}: ya hay una con la "
                f"misma clave completa (caso, producto, configuracion, intento) "
                f"= ({ident}, {obs['producto']!r}, {obs['configuracion']!r}, "
                f"{obs['intento']!r})"
            )
        hallazgos = {}
        for h in obs["hallazgos"]:
            if h["id"] in hallazgos:
                falla(f"hallazgo duplicado {h['id']!r} en el caso {ident}")
            if (ident, h["id"]) in indice_hallazgos:
                falla(
                    f"hallazgo {h['id']!r} repetido en el caso {ident}: la clave de "
                    "adjudicación es (caso, hallazgo) y ya existe en otra "
                    "observación del mismo caso; usa IDs distintos por producto"
                )
            hallazgos[h["id"]] = h
            indice_hallazgos[(ident, h["id"])] = clave_obs
        vistas[clave_obs] = {"obs": obs, "hallazgos": hallazgos}

    conocidos_por_caso = {c: set(d) for c, d in defectos.items() if c in corpus}
    vistos = set()
    conteo = {"valid": 0, "false_positive": 0, "duplicate": 0, "unresolved": 0}
    detectados = set()
    falsos_resueltos = 0
    casos_con_observaciones = {k[0] for k in vistas}
    for fila in filas:
        ident, hallazgo = fila.get("caso"), fila.get("hallazgo")
        llave = (ident, hallazgo)
        if llave in vistos:
            falla(f"fila de adjudicación duplicada para {ident}/{hallazgo}")
        if ident not in casos_con_observaciones:
            falla(f"adjudicación de caso desconocido: {ident!r}")
        clave_vista = indice_hallazgos.get(llave)
        if clave_vista is None:
            falla(f"adjudicación de hallazgo desconocido: {ident}/{hallazgo}")
        veredicto = fila.get("veredicto")
        if veredicto not in conteo:
            falla(
                f"veredicto inválido {veredicto!r} en {ident}/{hallazgo}; "
                "usa valid, false_positive, duplicate o unresolved"
            )
        vistos.add(llave)
        conteo[veredicto] += 1
        defecto = fila.get("defecto")
        if defecto:
            if defecto not in conocidos_por_caso.get(ident, set()):
                falla(
                    f"defecto {defecto!r} acreditado en {ident} no está entre los "
                    "conocidos de ese caso; los IDs de defecto son por caso"
                )
            if veredicto == "valid":
                detectados.add((ident, defecto))
        if veredicto == "duplicate":
            original = fila.get("duplicado_de")
            if (ident, original) not in indice_hallazgos:
                falla(
                    f"duplicate sin original válido: {ident}/{hallazgo} -> {original!r}"
                )
        h = vistas[clave_vista]["hallazgos"][hallazgo]
        if h.get("resuelto") and fila.get("resuelto_real") is False:
            falsos_resueltos += 1

    validos = conteo["valid"]
    denominador = validos + conteo["false_positive"] + conteo["duplicate"]
    excluidos = conteo["unresolved"]
    precision = round(validos / denominador, 4) if denominador else None

    total_hallazgos = sum(len(v["hallazgos"]) for v in vistas.values())
    sin_adjudicar = total_hallazgos - len(vistos)

    conocidos = {(c, d) for c, ds in conocidos_por_caso.items() for d in ds}
    if conocidos:
        omitidos = sorted(f"{c}/{d}" for c, d in conocidos - detectados)
        recuperacion = round(len(detectados) / len(conocidos), 4)
        defectos_informe = {
            "conjunto_presente": True,
            "conocidos": len(conocidos),
            "detectados": len(detectados),
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

    def identidad(clave):
        return f"{clave[0]}/{clave[1]}/{clave[2]}/intento={clave[3]}"

    duraciones = [
        o["obs"]["duracion_s"]
        for o in vistas.values()
        if o["obs"]["duracion_s"] is not None
    ]
    turnos = [
        o["obs"]["turnos"] for o in vistas.values() if o["obs"]["turnos"] is not None
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
            identidad(k): v["obs"]["cobertura"]
            for k, v in sorted(vistas.items(), key=lambda kv: identidad(kv[0]))
        },
        "tiempo": {
            "duracion_total_s": round(sum(duraciones), 3) if duraciones else None,
            "duracion_desconocidas": len(vistas) - len(duraciones),
            "duracion_promedio_s": round(sum(duraciones) / len(duraciones), 3)
            if duraciones
            else None,
            "turnos_total": sum(turnos) if turnos else None,
            "turnos_desconocidos": len(vistas) - len(turnos),
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
