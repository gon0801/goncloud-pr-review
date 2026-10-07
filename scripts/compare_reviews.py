"""Compara revisiones capturadas sobre los mismos commits. Offline: no llama
modelos ni publica comentarios; trabaja solo sobre salidas ya adjudicadas.

Uso:
  python3 scripts/compare_reviews.py --corpus corpus.json \
      --observations observaciones.json --judgments adjudicaciones.json \
      --output informe.json [--pairing pairing.json]

Entradas (JSON):
- corpus:    {"casos": [{caso, repo, base, head, producto, configuracion, intento}]}
- observaciones: {"observaciones": [{caso, repo, base, head, producto, configuracion,
    intento, resultado, cobertura, duracion_s, turnos, costo_usd (null = desconocido),
    hallazgos: [{id, titulo, ruta, resuelto}]}]}
- judgments: {"adjudicaciones": [{caso, hallazgo, veredicto (valid | false_positive |
    duplicate | unresolved), defecto?, duplicado_de?, resuelto_real?}],
    "defectos": {caso: [ids de defectos conocidos]}}  (opcional)
- pairing (opcional, activa el modo v2): {"experimentos": [{experimento, pares:
    [{caso, repo, base, head, tarea, sha_anterior, repeticion, control: {producto,
    configuracion, intento}, variante: {producto, configuracion, intento}}]}]}

Sin --pairing rige el modo histórico: la identidad de una observación es la
clave completa (caso, producto, configuracion, intento); varias observaciones
del mismo caso se aceptan si la clave difiere y la clave completa repetida se
rechaza. La adjudicación es (caso, hallazgo): un ID de hallazgo no puede
repetirse entre observaciones del mismo caso. Sólo se comparan pares con base
y head exactos; las filas de adjudicación duplicadas se rechazan; unresolved
sale del denominador de precisión y se informa cuántos se excluyeron; el costo
ausente se informa como desconocido (nunca cero) y la recuperación sólo se
calcula cuando existe un conjunto de defectos conocidos adjudicados.

Con --pairing rige el modo v2 sobre las claves CaseKey = (caso, repo, base,
head, tarea, sha_anterior), ObservationKey = (CaseKey, producto,
configuracion, repeticion, intento), FindingKey = (ObservationKey, hallazgo)
y PairKey = (CaseKey, experimento, repeticion). El corpus declara el CaseKey
por caso y cada observación debe repetirlo igual. La adjudicación v2 usa la
ObservationKey completa; una fila histórica (sólo caso y hallazgo) sólo se
admite cuando resuelve a una única observación del caso. El informe no tiene
precisión global: cada grupo (producto, configuracion) informa hallazgos,
precisión, defectos, intentos (los fallidos se conservan), solicitudes con
reintentos y costos. Los pares se fijan antes de evaluar resultados; un par
con contraparte ausente o con campos del caso que difieren de los declarados
se rechaza y sus observaciones quedan en sin_pareja con la causa.
"""

import argparse
import json
import sys
from pathlib import Path

CLAVES_CASO = ("caso", "repo", "base", "head", "tarea", "sha_anterior")
CLAVE_OBSERVACION = CLAVES_CASO + (
    "producto",
    "configuracion",
    "repeticion",
    "intento",
)
CLAVES_OBS_V2 = CLAVE_OBSERVACION + (
    "resultado",
    "cobertura",
    "duracion_s",
    "turnos",
    "costo_usd",
    "hallazgos",
)
CLAVES_FILA = (
    "repo",
    "base",
    "head",
    "tarea",
    "sha_anterior",
    "producto",
    "configuracion",
    "repeticion",
    "intento",
)
CLAVES_LADO = ("producto", "configuracion", "intento")
CLAVES_PAR = CLAVES_CASO + ("repeticion", "control", "variante")
VEREDICTOS = ("valid", "false_positive", "duplicate", "unresolved")


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
    return datos


def compare_reviews(corpus, observations, judgments, pairing):
    defectos = judgments.get("defectos") or {}
    if pairing is None:
        return informe_historico(
            corpus["casos"],
            observations["observaciones"],
            judgments["adjudicaciones"],
            defectos,
        )
    return informe_v2(
        corpus["casos"],
        observations["observaciones"],
        judgments["adjudicaciones"],
        defectos,
        pairing,
    )


def informe_historico(casos, observaciones, filas, defectos):
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

    def orden_clave(clave):
        return (clave[0], clave[1], clave[2], str(clave[3]))

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
        "cobertura_declarada": [
            {
                "caso": k[0],
                "producto": k[1],
                "configuracion": k[2],
                "intento": k[3],
                "cobertura": v["obs"]["cobertura"],
            }
            for k, v in sorted(vistas.items(), key=lambda kv: orden_clave(kv[0]))
        ],
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
    return informe


def precision_observacion(registro):
    conteo = registro["conteo"]
    validos = conteo["valid"]
    denominador = validos + conteo["false_positive"] + conteo["duplicate"]
    return {
        "precision": round(validos / denominador, 4) if denominador else None,
        "validos": validos,
        "denominador": denominador,
    }


def lado_resuelto(corpus, vistas, par, rol):
    caso = corpus.get(par["caso"])
    if caso is None:
        return None
    lado = par[rol]
    clave = tuple(caso[c] for c in CLAVES_CASO) + (
        lado["producto"],
        lado["configuracion"],
        par["repeticion"],
        lado["intento"],
    )
    return vistas.get(clave)


def informe_grupo(producto, configuracion, registros, conocidos_por_caso):
    registros = sorted(
        registros,
        key=lambda r: (
            r["caso"],
            r["producto"],
            r["configuracion"],
            r["repeticion"],
            r["intento"],
        ),
    )
    conteo = {v: 0 for v in VEREDICTOS}
    total_hallazgos = 0
    adjudicados = 0
    detectados = set()
    for registro in registros:
        for veredicto, n in registro["conteo"].items():
            conteo[veredicto] += n
        total_hallazgos += len(registro["hallazgos"])
        adjudicados += len(registro["filas"])
        detectados |= registro["detectados"]
    validos = conteo["valid"]
    denominador = validos + conteo["false_positive"] + conteo["duplicate"]
    conocidos = {
        (c, d)
        for c in {r["caso"] for r in registros}
        if c in conocidos_por_caso
        for d in conocidos_por_caso[c]
    }
    if conocidos:
        defectos_grupo = {
            "conjunto_presente": True,
            "conocidos": len(conocidos),
            "detectados": len(detectados),
            "omitidos": sorted(f"{c}/{d}" for c, d in conocidos - detectados),
            "recuperacion": round(len(detectados) / len(conocidos), 4),
        }
    else:
        defectos_grupo = {
            "conjunto_presente": False,
            "conocidos": 0,
            "detectados": 0,
            "omitidos": "desconocido",
            "recuperacion": "desconocido",
        }
    solicitudes = {}
    for registro in registros:
        solicitudes.setdefault((registro["caso"], registro["repeticion"]), []).append(
            registro
        )
    por_solicitud = []
    for (caso, repeticion), grupo in sorted(solicitudes.items()):
        duraciones = [
            r["datos"]["duracion_s"]
            for r in grupo
            if r["datos"]["duracion_s"] is not None
        ]
        por_solicitud.append(
            {
                "caso": caso,
                "repeticion": repeticion,
                "intentos": len(grupo),
                "fallidos": sum(
                    1 for r in grupo if r["datos"]["resultado"] != "success"
                ),
                "duracion_s": round(sum(duraciones), 3) if duraciones else None,
                "intentos_detalle": [
                    {
                        "intento": r["intento"],
                        "resultado": r["datos"]["resultado"],
                        "duracion_s": r["datos"]["duracion_s"],
                        "costo_usd": r["datos"]["costo_usd"],
                    }
                    for r in grupo
                ],
            }
        )
    exitosos = sum(1 for r in registros if r["datos"]["resultado"] == "success")
    fallidos = len(registros) - exitosos
    costos = [
        r["datos"]["costo_usd"]
        for r in registros
        if r["datos"]["costo_usd"] is not None
    ]
    return {
        "producto": producto,
        "configuracion": configuracion,
        "observaciones": len(registros),
        "solicitudes_total": len(solicitudes),
        "intentos": {
            "total": len(registros),
            "exitosos": exitosos,
            "fallidos": fallidos,
            "tasa_fallos": round(fallidos / len(registros), 4) if registros else None,
        },
        "hallazgos": {
            "total": total_hallazgos,
            "validos": validos,
            "falsos_positivos": conteo["false_positive"],
            "duplicados": conteo["duplicate"],
            "sin_resolver": conteo["unresolved"],
            "sin_adjudicar": total_hallazgos - adjudicados,
        },
        "precision": {
            "valor": round(validos / denominador, 4) if denominador else None,
            "validos": validos,
            "denominador": denominador,
            "excluidos_sin_resolver": conteo["unresolved"],
        },
        "defectos": defectos_grupo,
        "solicitudes": {"total": len(solicitudes), "por_solicitud": por_solicitud},
        "costo": {
            "usd_conocido": round(sum(costos), 6) if costos else None,
            "desconocidos": len(registros) - len(costos),
        },
    }


def informe_v2(casos, observaciones, filas, defectos, pairing):
    corpus = {}
    for caso in casos:
        ident = caso.get("caso")
        if not ident or ident in corpus:
            falla(f"caso ausente o duplicado en el corpus: {ident!r}")
        for clave in CLAVES_CASO:
            if clave not in caso:
                falla(f"caso {ident!r} del corpus sin la clave requerida {clave!r}")
        corpus[ident] = caso

    vistas = {}
    for obs in observaciones:
        ident = obs.get("caso")
        if ident not in corpus:
            falla(f"observación de caso desconocido: {ident!r}")
        caso = corpus[ident]
        for clave in CLAVES_OBS_V2:
            if clave not in obs:
                falla(f"observación del caso {ident} sin la clave requerida {clave!r}")
        difieren = [c for c in CLAVES_CASO[1:] if obs[c] != caso[c]]
        if difieren:
            falla(
                f"observación del caso {ident} difiere del corpus en: "
                f"{', '.join(difieren)}"
            )
        clave_obs = tuple(obs[c] for c in CLAVE_OBSERVACION)
        if clave_obs in vistas:
            falla(
                f"observación duplicada para el caso {ident}: ya hay una con la "
                f"misma ObservationKey {clave_obs}"
            )
        hallazgos = {}
        for h in obs["hallazgos"]:
            if h["id"] in hallazgos:
                falla(f"hallazgo duplicado {h['id']!r} en la observación {clave_obs}")
            hallazgos[h["id"]] = h
        vistas[clave_obs] = {
            "clave": clave_obs,
            "caso": ident,
            "producto": obs["producto"],
            "configuracion": obs["configuracion"],
            "repeticion": obs["repeticion"],
            "intento": obs["intento"],
            "datos": obs,
            "hallazgos": hallazgos,
            "conteo": {v: 0 for v in VEREDICTOS},
            "filas": {},
            "detectados": set(),
        }

    conocidos_por_caso = {c: set(d) for c, d in defectos.items() if c in corpus}
    legado = {}
    for registro in vistas.values():
        for hallazgo in registro["hallazgos"]:
            legado.setdefault((registro["caso"], hallazgo), []).append(
                registro["clave"]
            )

    vistos = set()
    falsos_resueltos = 0
    for fila in filas:
        ident, hallazgo = fila.get("caso"), fila.get("hallazgo")
        if any(clave in fila for clave in CLAVES_FILA):
            for clave in CLAVES_FILA:
                if clave not in fila:
                    falla(
                        f"adjudicación de {ident}/{hallazgo} sin la clave requerida "
                        f"{clave!r}"
                    )
            clave_obs = tuple(fila[c] for c in CLAVE_OBSERVACION)
            if clave_obs not in vistas:
                falla(f"adjudicación de observación desconocida: {clave_obs}")
        else:
            candidatas = legado.get((ident, hallazgo), [])
            if len(candidatas) > 1:
                falla(
                    f"adjudicación histórica ambigua: {ident}/{hallazgo} resuelve a "
                    f"{len(candidatas)} observaciones del caso"
                )
            if not candidatas:
                falla(f"adjudicación de hallazgo desconocido: {ident}/{hallazgo}")
            clave_obs = candidatas[0]
        registro = vistas[clave_obs]
        if hallazgo not in registro["hallazgos"]:
            falla(
                f"adjudicación de hallazgo desconocido en la observación "
                f"{clave_obs}: {hallazgo!r}"
            )
        if (clave_obs, hallazgo) in vistos:
            falla(f"fila de adjudicación duplicada para {ident}/{hallazgo}")
        vistos.add((clave_obs, hallazgo))
        veredicto = fila.get("veredicto")
        if veredicto not in VEREDICTOS:
            falla(
                f"veredicto inválido {veredicto!r} en {ident}/{hallazgo}; "
                "usa valid, false_positive, duplicate o unresolved"
            )
        registro["conteo"][veredicto] += 1
        registro["filas"][hallazgo] = fila
        defecto = fila.get("defecto")
        if defecto:
            if defecto not in conocidos_por_caso.get(ident, set()):
                falla(
                    f"defecto {defecto!r} acreditado en {ident} no está entre los "
                    "conocidos de ese caso; los IDs de defecto son por caso"
                )
            if veredicto == "valid":
                registro["detectados"].add((ident, defecto))
        if veredicto == "duplicate":
            original = fila.get("duplicado_de")
            if original not in registro["hallazgos"]:
                falla(
                    f"duplicate sin original válido: {ident}/{hallazgo} -> {original!r}"
                )
        h = registro["hallazgos"][hallazgo]
        if h.get("resuelto") and fila.get("resuelto_real") is False:
            falsos_resueltos += 1

    experimentos = []
    pares_vistos = set()
    for exp in pairing["experimentos"]:
        nombre = exp.get("experimento")
        if not nombre:
            falla(f"experimento sin nombre en el pairing: {exp!r}")
        pares = exp.get("pares") or []
        if not isinstance(pares, list):
            falla(f"el experimento {nombre!r} debe declarar una lista 'pares'")
        for par in pares:
            for clave in CLAVES_PAR:
                if clave not in par:
                    falla(
                        f"par del experimento {nombre} sin la clave requerida {clave!r}"
                    )
            for rol in ("control", "variante"):
                for clave in CLAVES_LADO:
                    if clave not in par[rol]:
                        falla(
                            f"lado {rol} del par {par.get('caso')!r} en {nombre} "
                            f"sin la clave requerida {clave!r}"
                        )
            llave = (tuple(par[c] for c in CLAVES_CASO), nombre, par["repeticion"])
            if llave in pares_vistos:
                falla(
                    f"par duplicado en el experimento {nombre}: caso "
                    f"{par['caso']!r}, repetición {par['repeticion']!r}"
                )
            pares_vistos.add(llave)
        experimentos.append((nombre, pares))

    bloques = []
    emparejadas = set()
    rechazadas = {}
    for nombre, pares in sorted(experimentos, key=lambda e: e[0]):
        evaluados = 0
        rechazos = []
        cohorte = {
            "comparables": 0,
            "control_mejor": 0,
            "variante_mejor": 0,
            "empate": 0,
            "sin_precision_comparable": 0,
        }
        detalle = []
        for par in sorted(pares, key=lambda p: (p["caso"], p["repeticion"])):
            lados = {
                rol: lado_resuelto(corpus, vistas, par, rol)
                for rol in ("control", "variante")
            }
            ausentes = [rol for rol, r in lados.items() if r is None]
            if ausentes:
                motivo = f"contraparte ausente: {', '.join(ausentes)}"
            else:
                caso = corpus[par["caso"]]
                difieren = [c for c in CLAVES_CASO[1:] if par[c] != caso[c]]
                motivo = (
                    f"campos que difieren del caso: {', '.join(difieren)}"
                    if difieren
                    else None
                )
            if motivo is not None:
                rechazos.append(
                    {
                        "caso": par["caso"],
                        "repeticion": par["repeticion"],
                        "motivo": motivo,
                    }
                )
                for registro in lados.values():
                    if registro is not None:
                        rechazadas.setdefault(registro["clave"], motivo)
                continue
            evaluados += 1
            lados_informe = {}
            for rol, registro in lados.items():
                emparejadas.add(registro["clave"])
                lados_informe[rol] = precision_observacion(registro)
            detalle.append(
                {
                    "caso": par["caso"],
                    "repeticion": par["repeticion"],
                    "control": lados_informe["control"],
                    "variante": lados_informe["variante"],
                }
            )
            control = lados_informe["control"]["precision"]
            variante = lados_informe["variante"]["precision"]
            if control is None or variante is None:
                cohorte["sin_precision_comparable"] += 1
            else:
                cohorte["comparables"] += 1
                if control > variante:
                    cohorte["control_mejor"] += 1
                elif variante > control:
                    cohorte["variante_mejor"] += 1
                else:
                    cohorte["empate"] += 1
        bloques.append(
            {
                "experimento": nombre,
                "pares_evaluados": evaluados,
                "pares_rechazados": rechazos,
                "cohorte": cohorte,
                "pares_detalle": detalle,
            }
        )

    sin_pareja = []
    registros_ordenados = sorted(
        vistas.values(),
        key=lambda r: (
            r["caso"],
            r["producto"],
            r["configuracion"],
            r["repeticion"],
            r["intento"],
        ),
    )
    for registro in registros_ordenados:
        if registro["clave"] in emparejadas:
            continue
        if registro["clave"] in rechazadas:
            motivo = f"par rechazado: {rechazadas[registro['clave']]}"
        else:
            motivo = "sin par declarado en pairing.json"
        sin_pareja.append(
            {
                "caso": registro["caso"],
                "producto": registro["producto"],
                "configuracion": registro["configuracion"],
                "repeticion": registro["repeticion"],
                "intento": registro["intento"],
                "motivo": motivo,
            }
        )

    grupos = {}
    for registro in vistas.values():
        grupos.setdefault((registro["producto"], registro["configuracion"]), []).append(
            registro
        )
    productos = [
        informe_grupo(producto, configuracion, registros, conocidos_por_caso)
        for (producto, configuracion), registros in sorted(grupos.items())
    ]

    total_hallazgos = sum(len(r["hallazgos"]) for r in vistas.values())
    adjudicados = sum(len(r["filas"]) for r in vistas.values())
    conteo_global = {
        v: sum(r["conteo"][v] for r in vistas.values()) for v in VEREDICTOS
    }
    return {
        "version": 2,
        "casos": len(corpus),
        "observaciones": len(vistas),
        "hallazgos": {
            "total": total_hallazgos,
            "validos": conteo_global["valid"],
            "falsos_positivos": conteo_global["false_positive"],
            "duplicados": conteo_global["duplicate"],
            "sin_resolver": conteo_global["unresolved"],
            "sin_adjudicar": total_hallazgos - adjudicados,
        },
        "productos": productos,
        "pares": bloques,
        "sin_pareja": sin_pareja,
        "falsos_resueltos": falsos_resueltos,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Compara revisiones capturadas (offline, sin modelos)."
    )
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--judgments", type=Path, required=True)
    parser.add_argument("--pairing", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    corpus = cargar(args.corpus, "casos")
    observations = cargar(args.observations, "observaciones")
    judgments = cargar(args.judgments, "adjudicaciones")
    pairing = cargar(args.pairing, "experimentos") if args.pairing else None

    informe = compare_reviews(corpus, observations, judgments, pairing)
    args.output.write_text(json.dumps(informe, indent=2, ensure_ascii=False) + "\n")
    print(f"compare_reviews: informe escrito en {args.output}")


if __name__ == "__main__":
    main()
