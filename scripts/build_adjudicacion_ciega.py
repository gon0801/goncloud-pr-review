"""Construye los insumos de adjudicación ciega de E1 (offline y determinístico).

Lee corpus.json + salidas/ (revisor retenido y capturas CodeRabbit del SHA
exacto) y escribe observaciones.json, adjudicacion-ciega.json y
ciego-correspondencia.json. Es constructor, no adjudicador: no clasifica.
Nunca imprime el contenido de la correspondencia: sólo conteos y el hash y
tamaño del archivo.

Modo versionado (--versionado --raiz-v2 RUTA): lee observaciones.json,
partition.json y pairing.json (formato v2) de esa raíz y escribe ahí
adjudicacion-ciega.json y ciego-correspondencia.json; sin el flag corre el
modo histórico sin cambios.
"""

import argparse
import hashlib
import json
import random
import re
import sys
from pathlib import Path

SEMILLA = 20260927
RE_PIE_HTML = re.compile(r"<!--.*?-->", re.S)
RE_DETAILS = re.compile(r"<details>.*?</details>", re.S)
RE_ADDRESS = re.compile(r"✅ Addressed in commit [0-9a-f]+")
RE_SEVERIDAD = re.compile(r"\b(Minor|Major|Critical|Trivial|Nitpick)\b")
RE_BOLD = re.compile(r"^\*\*(.+?)\*\*\s*$", re.M)
RE_MARCA_SEVERIDAD_V2 = re.compile(
    r"(?:^|(?<=\s))"
    r"[-•*#*_~]*\s*"
    r"[^\x00-\x7F]\s*[*_~]*"
    r"(?:Minor|Major|Critical|Trivial|Nitpick|Low|Medium|High)\b"
    r"[*_~]*(?:\s*[·|]\s*)?",
    re.IGNORECASE,
)
ENFASIS_V2 = " *_`~"

CLAVES_CASO_V2 = ("caso", "repo", "base", "head", "tarea", "sha_anterior")
CLAVES_OBSERVACION_V2 = CLAVES_CASO_V2 + (
    "producto",
    "configuracion",
    "repeticion",
    "intento",
    "resultado",
    "cobertura",
    "duracion_s",
    "turnos",
    "costo_usd",
    "hallazgos",
)
CLAVES_HALLAZGO_V2 = ("id", "titulo", "ruta", "resuelto")
SEVERIDAD_V2 = {
    "trivial": "baja",
    "nitpick": "baja",
    "minor": "baja",
    "low": "baja",
    "major": "media",
    "medium": "media",
    "critical": "alta",
    "high": "alta",
}
GRUPOS_V2 = ("ajuste", "reservada")


def falla(mensaje):
    print(f"build_adjudicacion_ciega: {mensaje}", file=sys.stderr)
    raise SystemExit(2)


def leer_json(ruta):
    try:
        return json.loads(ruta.read_text())
    except FileNotFoundError:
        falla(f"no existe {ruta}")
    except json.JSONDecodeError as exc:
        falla(f"{ruta} no es JSON válido: {exc}")


def viñetas_prosa(texto, caso):
    marcador = texto.find("ai-review:findings=")
    if marcador < 0:
        falla(f"{caso}: sin marcador de hallazgos")
    prosa, actual, viñetas = texto[:marcador], None, []
    for linea in prosa.splitlines():
        if linea.startswith("- "):
            if actual:
                viñetas.append(" ".join(actual))
            actual = [linea]
        elif actual is not None and linea.strip():
            actual.append(linea)
    if actual:
        viñetas.append(" ".join(actual))
    return viñetas


def observacion_revisor(caso, ccorpus, entrada, raiz):
    base_dir = raiz / "salidas" / "revisor" / caso / "intento-1"
    result = leer_json(base_dir / "result.json")
    manifest = leer_json(base_dir / "manifest.json")
    if (manifest["base"], manifest["head"]) != (ccorpus["base"], ccorpus["head"]):
        falla(f"{caso}: el manifest retenido no coincide con el corpus")
    if result["subtype"] != "success" or result["is_error"]:
        falla(f"{caso}: la salida retenida no es una salida útil")
    texto = result["result"]
    marcador = re.search(r"ai-review:findings=(\{.*?\}) -->", texto, re.S)
    if not marcador:
        falla(f"{caso}: sin marcador de hallazgos")
    findings = json.loads(marcador.group(1))["findings"]
    viñetas = viñetas_prosa(texto, caso)
    usadas, hallazgos, fallbacks = set(), [], []
    for i, f in enumerate(findings, start=1):
        clave = f"{f['file']}:{f['line']}"
        elegida = None
        for j, v in enumerate(viñetas):
            if j not in usadas and clave in v and f["title"] in v:
                elegida = j
                break
        # si la prosa no trae viñeta individual emparejable, el detalle es el
        # título canónico del marcador; la prosa íntegra queda en la salida
        # retenida, fuera de la hoja
        if elegida is None:
            fallbacks.append(f"{clave} «{f['title']}»")
            detalle = f["title"]
        else:
            usadas.add(elegida)
            detalle = viñetas[elegida]
        hallazgos.append(
            {
                "id": f"{caso}-R{i:02d}",
                "titulo": f["title"],
                "ruta": f["file"],
                "linea": f["line"],
                "severidad": f.get("severity", "no declarada"),
                "resuelto": f.get("state") == "resolved",
                "detalle": detalle,
            }
        )
    cobertura = re.search(r"COVERAGE:\s*(\S+)", texto)
    retanda = entrada["procedencia"].startswith("run ")
    observacion = {
        "caso": caso,
        "repo": ccorpus["repo"],
        "base": ccorpus["base"],
        "head": ccorpus["head"],
        "producto": "revisor",
        "configuracion": (
            "deepseek-v4.1-flash vía deepseek directo (retanda E1aP; "
            "ATTEMPTS=2, presupuesto 1320 s)"
            if retanda
            else "deepseek-v4.1-flash vía opencode-go (tanda original; "
            "ATTEMPTS=2, presupuesto 1320 s)"
        ),
        "intento": 1,
        "resultado": result["subtype"],
        "cobertura": cobertura.group(1) if cobertura else "no declarada",
        "duracion_s": round(result["duration_ms"] / 1000, 3),
        "turnos": result["num_turns"],
        "costo_usd": result.get("total_cost_usd"),
        "hallazgos": hallazgos,
    }
    return observacion, fallbacks


def observacion_coderabbit(caso, ccorpus, captura):
    hallazgos = []
    orden = sorted(captura["comentarios"], key=lambda x: x["id"])
    for i, k in enumerate(orden, start=1):
        cuerpo = k["body"]
        resuelto = bool(RE_ADDRESS.search(cuerpo))
        limpio = RE_PIE_HTML.sub("", cuerpo)

        def quitar_bloque(m):
            bloque = m.group(0)
            return "" if "coderabbit" in bloque.lower() or "🤖" in bloque else bloque

        limpio = RE_DETAILS.sub(quitar_bloque, limpio)
        limpio = RE_ADDRESS.sub("", limpio)
        limpio = re.sub(r"\n{3,}", "\n\n", limpio).strip()
        if "coderabbit" in limpio.lower():
            falla(f"{caso}: residual de procedencia en el cuerpo {k['id']}")
        primera = next((x for x in cuerpo.splitlines() if x.strip()), "")
        sev = RE_SEVERIDAD.search(primera)
        negritas = RE_BOLD.findall(limpio)
        hallazgos.append(
            {
                "id": f"{caso}-C{i:02d}",
                "titulo": negritas[0].strip() if negritas else "sin título declarado",
                "ruta": k["path"],
                "linea": k.get("line") or k.get("original_line"),
                "severidad": sev.group(1) if sev else "no declarada",
                "resuelto": resuelto,
                "detalle": limpio,
            }
        )
    return {
        "caso": caso,
        "repo": ccorpus["repo"],
        "base": ccorpus["base"],
        "head": ccorpus["head"],
        "producto": "coderabbit",
        "configuracion": "comentarios existentes en el SHA exacto; sin revisión disparada",
        "intento": 1,
        "resultado": "comentarios existentes",
        "cobertura": "no declarada (comentarios existentes)",
        "duracion_s": None,
        "turnos": None,
        "costo_usd": None,
        "hallazgos": hallazgos,
        "sha_captura": captura["sha"],
    }


def es_linea_badges_v2(linea):
    if "|" not in linea:
        return False
    segmentos = [s.strip(ENFASIS_V2) for s in linea.split("|")]
    visibles = [s for s in segmentos if s]
    return bool(visibles) and all(not s[0].isalnum() for s in visibles)


def limpiar_evidencia_v2(texto):
    limpio = RE_PIE_HTML.sub("", texto)
    limpio = RE_DETAILS.sub("", limpio)
    limpio = RE_ADDRESS.sub("", limpio)
    limpio = limpio.replace("🤖", "")
    limpio = "\n".join(
        linea for linea in limpio.splitlines() if not es_linea_badges_v2(linea)
    )
    limpio = RE_MARCA_SEVERIDAD_V2.sub("", limpio)
    return re.sub(r"\n{3,}", "\n\n", limpio).strip()


def clave_finding_v2(o, h):
    valores = (
        o["caso"],
        o["repo"],
        o["base"],
        o["head"],
        o["tarea"],
        o["sha_anterior"],
        o["producto"],
        o["configuracion"],
        o["repeticion"],
        o["intento"],
        h["id"],
    )
    return tuple((v is None, "" if v is None else v) for v in valores)


def severidad_normalizada_v2(declarada):
    return SEVERIDAD_V2.get(str(declarada).strip().lower(), "no declarada")


def inventario_identificadores_v2(observaciones):
    identificadores = set()
    for o in observaciones:
        if o["producto"]:
            identificadores.add(o["producto"])
        if len(o["configuracion"]) >= 8:
            identificadores.add(o["configuracion"])
    return sorted(identificadores, key=lambda s: (-len(s), s))


def redactar_identificadores_v2(texto, identificadores):
    for identificador in identificadores:
        texto = re.sub(
            re.escape(identificador),
            "[redactado]",
            texto,
            flags=re.IGNORECASE,
        )
    return texto


def fila_hoja_v2(o, h, identificadores):
    linea = h.get("linea")
    detalle = h.get("detalle")
    diagnostico = redactar_identificadores_v2(h["titulo"], identificadores)
    evidencia = (
        redactar_identificadores_v2(limpiar_evidencia_v2(detalle), identificadores)
        if detalle is not None
        else ""
    )
    return {
        "caso": o["caso"],
        "head": o["head"],
        "diagnostico": diagnostico,
        "ubicacion": f"{h['ruta']}:{linea}" if linea is not None else h["ruta"],
        "evidencia": evidencia or diagnostico,
        "severidad": severidad_normalizada_v2(h.get("severidad")),
    }


def fila_correspondencia_v2(o, h, particion):
    declarada = h.get("severidad")
    return {
        "caso": o["caso"],
        "repo": o["repo"],
        "base": o["base"],
        "head": o["head"],
        "tarea": o["tarea"],
        "sha_anterior": o["sha_anterior"],
        "producto": o["producto"],
        "configuracion": o["configuracion"],
        "repeticion": o["repeticion"],
        "intento": o["intento"],
        "hallazgo_id": h["id"],
        "severidad_original": declarada if declarada is not None else "no declarada",
        "particion": particion[o["caso"]],
    }


def validar_observaciones_v2(datos, ruta):
    if not isinstance(datos, dict) or not isinstance(datos.get("observaciones"), list):
        falla(f"{ruta} no trae la lista 'observaciones'")
    for i, o in enumerate(datos["observaciones"], start=1):
        if not isinstance(o, dict):
            falla(f"observación {i}: no es un objeto")
        faltan = [c for c in CLAVES_OBSERVACION_V2 if c not in o]
        if faltan:
            falla(
                f"observación {i} ({o.get('caso', 'sin caso')}): "
                f"faltan las claves {faltan}"
            )
        if not isinstance(o["hallazgos"], list):
            falla(f"observación {i} ({o['caso']}): hallazgos no es una lista")
        for j, h in enumerate(o["hallazgos"], start=1):
            if not isinstance(h, dict):
                falla(f"observación {i} ({o['caso']}), hallazgo {j}: no es un objeto")
            faltan_h = [c for c in CLAVES_HALLAZGO_V2 if c not in h]
            if faltan_h:
                falla(
                    f"observación {i} ({o['caso']}), hallazgo {j}: "
                    f"faltan las claves {faltan_h}"
                )
    return datos["observaciones"]


def validar_particion_v2(datos, casos, ruta):
    if not isinstance(datos, dict) or not isinstance(datos.get("particion"), dict):
        falla(f"{ruta} no trae el objeto 'particion'")
    particion = datos["particion"]
    if set(particion) != casos:
        falla(
            f"la partición no coincide con los casos de las observaciones; "
            f"faltan: {sorted(casos - set(particion))}, "
            f"sobran: {sorted(set(particion) - casos)}"
        )
    for caso, grupo in particion.items():
        if grupo not in GRUPOS_V2:
            falla(f"{caso}: grupo de partición inválido: {grupo!r}")
    return particion


def validar_adjudicacion_v2(datos, ruta):
    if not isinstance(datos, dict) or not isinstance(datos.get("adjudicacion"), dict):
        falla(f"{ruta} no trae el objeto 'adjudicacion'")
    adj = datos["adjudicacion"]
    if adj.get("normalizacion") != "1":
        falla(
            f"normalización de adjudicación {adj.get('normalizacion')!r} no soportada; "
            "este builder sólo aplica la 1"
        )
    if not isinstance(adj.get("jueces"), list):
        falla("adjudicacion.jueces debe ser una lista")
    for juez in adj["jueces"]:
        if (
            not isinstance(juez, dict)
            or "id" not in juez
            or juez.get("tipo") not in {"humano", "ia"}
        ):
            falla(f"adjudicacion.jueces: {juez!r} sin id o con tipo inválido")
    desempates = adj.get("desempates")
    if (
        not isinstance(desempates, dict)
        or "version" not in desempates
        or "regla" not in desempates
    ):
        falla("adjudicacion.desempates debe ser un objeto con version y regla")
    return adj


def main_v2(raiz):
    ruta_observaciones = raiz / "observaciones.json"
    ruta_particion = raiz / "partition.json"
    ruta_pairing = raiz / "pairing.json"
    observaciones = validar_observaciones_v2(
        leer_json(ruta_observaciones), ruta_observaciones
    )
    casos = {o["caso"] for o in observaciones}
    particion = validar_particion_v2(leer_json(ruta_particion), casos, ruta_particion)
    adj = validar_adjudicacion_v2(leer_json(ruta_pairing), ruta_pairing)

    identificadores = inventario_identificadores_v2(observaciones)
    pares = []
    for o in observaciones:
        for h in o["hallazgos"]:
            pares.append(
                (
                    clave_finding_v2(o, h),
                    fila_hoja_v2(o, h, identificadores),
                    fila_correspondencia_v2(o, h, particion),
                )
            )
    pares.sort(key=lambda par: par[0])
    random.Random(SEMILLA).shuffle(pares)
    hoja, filas_correspondencia = [], []
    for n, (_, fila, fuente) in enumerate(pares, start=1):
        blind_id = f"H-{n:03d}"
        hoja.append({"blind_id": blind_id, **fila})
        filas_correspondencia.append({"blind_id": blind_id, **fuente})

    texto_hoja = (
        json.dumps(
            {
                "meta": {
                    "version": 2,
                    "que_es": "hoja de adjudicación ciega v2: una fila por hallazgo "
                    "normalizado, sin campos que identifiquen el origen de cada fila",
                    "instruccion": "clasifica cada hallazgo como valid, false_positive, "
                    "duplicate o unresolved (duplicate nombra duplicado_de); no intentes "
                    "identificar el origen de ninguna fila",
                    "normalizacion": adj["normalizacion"],
                    "jueces": adj["jueces"],
                    "desempates": adj["desempates"],
                    "adjudicacion_ia": any(
                        j.get("tipo") == "ia" for j in adj["jueces"]
                    ),
                    "semilla": SEMILLA,
                    "orden": "aleatorio reproducible con random.Random(semilla) sobre la "
                    "FindingKey ordenada; regenerable con "
                    "scripts/build_adjudicacion_ciega.py --versionado",
                },
                "hallazgos": hoja,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )
    (raiz / "adjudicacion-ciega.json").write_text(texto_hoja)

    texto_correspondencia = (
        json.dumps(
            {
                "meta": {
                    "que_es": "correspondencia hoja↔fuente de la adjudicación ciega v2",
                    "advertencia": "ningún participante de la adjudicación debe abrir este "
                    "archivo; sólo lo audita el operador al cierre",
                    "semilla": SEMILLA,
                },
                "filas": filas_correspondencia,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )
    (raiz / "ciego-correspondencia.json").write_text(texto_correspondencia)

    por_grupo = {}
    for grupo in particion.values():
        por_grupo[grupo] = por_grupo.get(grupo, 0) + 1
    distribucion = ", ".join(f"{g}={por_grupo[g]}" for g in sorted(por_grupo))
    print(f"casos: {len(casos)}")
    print(f"observaciones: {len(observaciones)}")
    print(f"partición: {distribucion}")
    print(
        f"hoja: {len(hoja)} filas con semilla {SEMILLA} -> "
        f"{raiz / 'adjudicacion-ciega.json'}"
    )
    print(
        f"correspondencia: filas={len(filas_correspondencia)} "
        f"bytes={len(texto_correspondencia.encode())} "
        f"sha256={hashlib.sha256(texto_correspondencia.encode()).hexdigest()} "
        "(contenido no impreso)"
    )


def main():
    parser = argparse.ArgumentParser(
        description="Construye hoja ciega, observaciones y correspondencia (offline)."
    )
    parser.add_argument(
        "--raiz",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "evaluation" / "reviewer",
    )
    parser.add_argument(
        "--versionado",
        action="store_true",
        help="corre el modo versionado v2 sobre --raiz-v2",
    )
    parser.add_argument(
        "--raiz-v2",
        type=Path,
        default=Path(__file__).resolve().parent.parent
        / "evaluation"
        / "reviewer"
        / "v2",
        help="raíz de las entradas y salidas del modo versionado",
    )
    args = parser.parse_args()
    if args.versionado:
        main_v2(args.raiz_v2)
        return
    raiz = args.raiz

    corpus = {c["caso"]: c for c in leer_json(raiz / "corpus.json")["casos"]}
    indice = {
        s["caso"]: s for s in leer_json(raiz / "salidas" / "indice.json")["salidas"]
    }
    if set(corpus) != set(indice) or len(corpus) != 20:
        falla("el índice de salidas y el corpus no cubren exactamente los 20 casos")

    obs, fallbacks_revisor = [], []
    for caso in sorted(corpus):
        observacion, fallbacks = observacion_revisor(
            caso, corpus[caso], indice[caso], raiz
        )
        obs.append(observacion)
        fallbacks_revisor.extend((caso, f) for f in fallbacks)
    comparables, pendientes = [], []
    for caso in sorted(corpus):
        captura = leer_json(
            raiz / "salidas" / "coderabbit" / f"{caso}-{corpus[caso]['head'][:8]}.json"
        )
        if captura["sha"] != corpus[caso]["head"]:
            falla(f"{caso}: la captura CodeRabbit no es del SHA exacto")
        if captura["revisiones"] or captura["comentarios"]:
            comparables.append(caso)
            obs.append(observacion_coderabbit(caso, corpus[caso], captura))
        else:
            pendientes.append(
                {
                    "caso": caso,
                    "producto": "coderabbit",
                    "base": corpus[caso]["base"],
                    "head": corpus[caso]["head"],
                    "motivo": "sin revisión ni comentarios existentes de CodeRabbit "
                    "en el head exacto del corpus",
                }
            )

    emparejadas = []
    contextos = {}
    for o in obs:
        c = corpus[o["caso"]]
        contextos[o["caso"]] = (
            f"PR #{c['pr']} «{c['titulo']}» · categoría {c['categoria']} · "
            f"diff completo del caso en el repositorio entre {c['base']} y {c['head']}"
        )
        for h in o["hallazgos"]:
            emparejadas.append(
                (
                    {
                        "caso": o["caso"],
                        "producto": o["producto"],
                        "hallazgo_id": h["id"],
                        "procedencia": (
                            indice[o["caso"]]["procedencia"]
                            if o["producto"] == "revisor"
                            else "comentarios existentes en el SHA exacto"
                        ),
                    },
                    {
                        "caso": o["caso"],
                        "head": o["head"],
                        "severidad": h["severidad"],
                        "titulo": h["titulo"],
                        "ruta": h["ruta"],
                        "linea": h["linea"],
                        "detalle": h["detalle"],
                        "contexto": contextos[o["caso"]],
                    },
                )
            )

    random.Random(SEMILLA).shuffle(emparejadas)
    hoja, filas_correspondencia = [], []
    for n, (fuente, fila) in enumerate(emparejadas, start=1):
        blind_id = f"H-{n:03d}"
        hoja.append({"blind_id": blind_id, **fila})
        filas_correspondencia.append({"blind_id": blind_id, **fuente})

    texto_hoja = (
        json.dumps(
            {
                "meta": {
                    "que_es": "hoja de adjudicación ciega: una fila por hallazgo, sin "
                    "campos ni etiquetas que identifiquen el origen de cada fila",
                    "instruccion": "clasifica cada hallazgo como valid, false_positive, "
                    "duplicate o unresolved (duplicate nombra duplicado_de); no intentes "
                    "identificar el origen de ninguna fila",
                    "campos": "blind_id, caso, head, severidad (la que declaró la fuente), "
                    "título, ruta/línea, detalle íntegro y contexto del caso; el diff "
                    "completo se puede leer del repositorio entre base y head",
                    "semilla": SEMILLA,
                    "orden": "aleatorio reproducible con random.Random(semilla) sobre el "
                    "orden (caso, hallazgo); regenerable con "
                    "scripts/build_adjudicacion_ciega.py",
                },
                "hallazgos": hoja,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )
    (raiz / "adjudicacion-ciega.json").write_text(texto_hoja)

    texto_correspondencia = (
        json.dumps(
            {
                "meta": {
                    "que_es": "correspondencia hoja↔fuente de la adjudicación ciega",
                    "advertencia": "ningún participante de la adjudicación debe abrir este "
                    "archivo; sólo lo audita el operador al cierre",
                    "semilla": SEMILLA,
                },
                "filas": filas_correspondencia,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )
    ruta_correspondencia = raiz / "ciego-correspondencia.json"
    ruta_correspondencia.write_text(texto_correspondencia)

    texto_observaciones = (
        json.dumps(
            {
                "meta": {
                    "que_es": "observaciones normalizadas para scripts/compare_reviews.py",
                    "clave_observacion": "(caso, producto, configuracion, intento)",
                    "convencion": "costo_usd, duracion_s y turnos en null = desconocido "
                    "(nunca cero); resuelto sólo se marca si la propia salida lo declara",
                    "pendiente_e0": "E0 hoy rechaza una segunda observación del mismo caso; "
                    "alinearla con la clave completa es el pendiente anotado en E1, no se "
                    "cambió E0 en este bloque",
                    "redaccion_ciega": "en los detalles de CodeRabbit se quitaron "
                    "únicamente metadatos de la herramienta (comentarios HTML, bloque "
                    "Prompt to fix, línea Addressed -> campo resuelto); el resto queda "
                    "íntegro",
                },
                "observaciones": obs,
                "pendientes_coderabbit": pendientes,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )
    (raiz / "observaciones.json").write_text(texto_observaciones)

    por_producto = {}
    for o in obs:
        conteo = por_producto.setdefault(
            o["producto"], {"observaciones": 0, "hallazgos": 0}
        )
        conteo["observaciones"] += 1
        conteo["hallazgos"] += len(o["hallazgos"])
    for producto, conteo in sorted(por_producto.items()):
        print(
            f"producto {producto}: {conteo['observaciones']} observaciones, "
            f"{conteo['hallazgos']} hallazgos"
        )
    print(
        f"pares coderabbit: {len(comparables)} comparables por SHA exacto, "
        f"{len(pendientes)} pendientes"
    )
    print(
        f"fallbacks de detalle (título canónico del marcador): "
        f"{len(fallbacks_revisor)} -> "
        + "; ".join(f"{caso}: {fuente}" for caso, fuente in fallbacks_revisor)
    )
    print(
        f"hoja: {len(hoja)} filas con semilla {SEMILLA} -> "
        f"{raiz / 'adjudicacion-ciega.json'}"
    )
    print(
        f"correspondencia: filas={len(filas_correspondencia)} "
        f"bytes={len(texto_correspondencia.encode())} "
        f"sha256={hashlib.sha256(texto_correspondencia.encode()).hexdigest()} "
        "(contenido no impreso)"
    )


if __name__ == "__main__":
    main()
