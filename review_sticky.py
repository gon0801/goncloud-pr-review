"""La memoria del comentario fijo: marcadores, libro de cobertura y lectura de la memoria previa y de la cobertura que declara el revisor."""

import json
import re

import review_domain
from review_config import ERROR_KEY
from review_context import LIBRO_BORRADO
from review_domain import (
    LIBRO_MAX_ENTRADAS,
    leer_cierre,
    parse_model_findings,
)


MARKER = "<!-- ai-review:sticky -->"
SHA_PREFIX = "<!-- ai-review:sha="
COMPLETION_PREFIX = "<!-- ai-review:completion="
COVERED_PREFIX = "<!-- ai-review:covered="
LIBRO_MAX_BYTES = 30000


def reviewed_sha(body):
    start = body.find(SHA_PREFIX)
    if start < 0:
        return None
    end = body.find(" -->", start)
    return body[start + len(SHA_PREFIX) : end] if end > 0 else None


def reviewed_completion(body):
    lines = body.splitlines()
    if len(lines) < 3 or lines[0] != MARKER or body.count(COMPLETION_PREFIX) != 1:
        return None
    match = re.fullmatch(
        r"<!-- ai-review:completion=([0-9a-f]{40}):(complete|partial) -->", lines[2]
    )
    if not match or lines[1] != f"{SHA_PREFIX}{match[1]} -->":
        return None
    return match[2]


def libro_a_linea(head_sha, libro):
    """El libro de cobertura como línea oculta del sticky; None si no cabe."""
    if len(libro) > LIBRO_MAX_ENTRADAS:
        return None
    # Las flechas del comentario viajan como secuencias \uXXXX dentro del JSON:
    # json.loads las decodifica al leer y una ruta con `-->` no rompe el comentario.
    cuerpo = review_domain.escape_hidden_json(
        json.dumps(libro, ensure_ascii=True, separators=(",", ":"))
    )
    linea = f"{COVERED_PREFIX}{head_sha}:{cuerpo} -->"
    return linea if len(linea.encode("utf-8")) <= LIBRO_MAX_BYTES else None


def libro_de_linea(linea):
    if not linea.startswith(COVERED_PREFIX):
        return None, None
    resto = linea[len(COVERED_PREFIX) :]
    cabeza, sep, resto = resto.partition(":")
    if not sep or not re.fullmatch(r"[0-9a-f]{40}", cabeza):
        return None, None
    fin = resto.rfind(" -->")
    if fin < 0:
        return None, None
    try:
        libro = json.loads(resto[:fin])
    except json.JSONDecodeError:
        return None, None
    if not isinstance(libro, dict) or len(libro) > LIBRO_MAX_ENTRADAS:
        return None, None
    for ruta, blob in libro.items():
        if not isinstance(ruta, str) or not ruta:
            return None, None
        if not isinstance(blob, str) or not (
            re.fullmatch(r"[0-9a-f]{40}", blob) or blob == LIBRO_BORRADO
        ):
            return None, None
    return cabeza, libro


def libro_de(body):
    """El libro del sticky: la primera línea covered= válida y del head de la
    cabecera; cualquier desvío es (None, None), nunca un libro ajeno."""
    for linea in (body or "").splitlines():
        if linea.startswith(COVERED_PREFIX):
            cabeza, libro = libro_de_linea(linea)
            if libro is not None and cabeza == reviewed_sha(body):
                return cabeza, libro
            return None, None
    return None, None


def modelo_utilizable(modelo):
    """La forma la decide el transporte del dominio, sin descartar el bloque."""
    return modelo is not None and not modelo.get("rechazadas_forma") and not any(
        isinstance(
            review_domain.observation_de_entrada(entrada),
            review_domain.ObservationRejected,
        )
        for entrada in modelo.get("findings", [])
    )


def pasada_acredita(result, model_ok):
    """Una pasada acredita cobertura solo si su resultado es utilizable
    entero: COVERAGE declarada, runtime terminado y hallazgos del modelo
    válidos; si no, no agrega nada al libro."""
    if ERROR_KEY in (result or {}):
        return False
    cobertura = leer_cierre((result or {}).get("result") or "").cobertura
    return (
        cobertura is not None
        and (result or {}).get("subtype") != "error_max_turns"
        and bool(model_ok)
        and modelo_utilizable(
            parse_model_findings(
                (result or {}).get("result") or "", conservar_anclas=True
            )
        )
    )


def libro_fusionado(previo, manifest, result, repo, model_ok):
    """El libro tras una pasada: lo cubierto ahora con su blob en head (o el
    centinela del borrado), más lo cubierto antes que sigue intacto; el resto
    queda fuera."""
    head = manifest["head"]
    # Sin head verificable nada es reclamable: ni blobs (un None de blob_at
    # puede ser un head que no resuelve, no un borrado) ni centinelas.
    if not repo.cat_file_exists(f"{head}^{{commit}}"):
        return {}
    texto = (result or {}).get("result") or ""
    if pasada_acredita(result, model_ok):
        cierre = leer_cierre(texto)
        revisados = manifest.get("reviewed") or []
        if cierre.cobertura == "complete":
            cubiertos = revisados
        elif cierre.cobertura == "partial":
            sin = cierre.sin_revisar
            excluidos = {e["path"] for e in manifest.get("excluded") or []}
            reconocibles = set(revisados) | excluidos
            declaracion_verificable = sin is not None and sin <= reconocibles
            if declaracion_verificable:
                cubiertos = [p for p in revisados if p not in sin]
            else:
                cubiertos = []
        else:
            cubiertos = []
    else:
        cubiertos = []
    cubierto = set(cubiertos)
    fused = {}
    for p, b in (previo or {}).items():
        if p in cubierto:
            continue
        blob = repo.blob_at(head, p)
        if (blob is None and b == LIBRO_BORRADO) or blob == b:
            fused[p] = b
    for p in cubiertos:
        blob = repo.blob_at(head, p)
        fused[p] = blob if blob else LIBRO_BORRADO
    return fused


def sticky_from_comments(comments, login):
    """Latest sticky among fetched comments. A missing user only happens with test
    doubles (the real API always sends user.login); those still count as sticky."""
    found = [
        c
        for c in comments or []
        if MARKER in (c.get("body") or "") and c.get("user", login) == login
    ]
    return found[-1] if found else None


def prev_de_memoria(body):
    """El prev.json que lee prepare, desde el cuerpo del sticky. Lo escriben gate
    (ruta directa) y execute-request (worker): sin él el modelo revisa sin los
    hallazgos previos. También entiende la memoria v2/v3 del coordinador."""
    if body is None:
        return {"sha": None, "state": None, "completion": None}
    load = review_domain.read_snapshot(body)
    return {
        "sha": reviewed_sha(body),
        "state": review_domain.estado_legado_de_memoria(body),
        "completion": reviewed_completion(body),
        "covered": libro_de(body)[1],
        "snapshot": load.block if isinstance(load, review_domain.Valid) else None,
    }


def sha_hexa(valor):
    valor = valor or ""
    return len(valor) == 40 and all(c in "0123456789abcdef" for c in valor.lower())


def cobertura_declarada(texto, modelo):
    """Cobertura respaldada por el bloque del modelo; sin bloque, UNKNOWN."""
    cobertura = leer_cierre(texto).cobertura
    mapeada = {
        "complete": review_domain.COMPLETE_CLAIM,
        "partial": review_domain.PARTIAL,
    }.get(cobertura, review_domain.UNKNOWN)
    return review_domain.UNKNOWN if modelo is None else mapeada


def recortes_del_manifest(manifest):
    """Las entradas de excluded que degradan: presupuesto o encoding."""
    return [
        e
        for e in ((manifest or {}).get("excluded") or [])
        if e.get("reason") in ("budget", "encoding")
    ]


def continuacion_terminal(manifest):
    """Continuación sin nada por leer: solo presupuesto y encoding degradan."""
    manifest = manifest or {}
    return (
        manifest.get("mode") == "continuation"
        and not manifest.get("reviewed")
        and not recortes_del_manifest(manifest)
    )
