"""Construye los insumos de adjudicación ciega de E1 (offline y determinístico).

Lee corpus.json + salidas/ (revisor retenido y capturas CodeRabbit del SHA
exacto) y escribe observaciones.json, adjudicacion-ciega.json y
ciego-correspondencia.json. Es constructor, no adjudicador: no clasifica.
Nunca imprime el contenido de la correspondencia: sólo conteos y el hash y
tamaño del archivo.
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


def main():
    parser = argparse.ArgumentParser(
        description="Construye hoja ciega, observaciones y correspondencia (offline)."
    )
    parser.add_argument(
        "--raiz",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "evaluation" / "reviewer",
    )
    args = parser.parse_args()
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
