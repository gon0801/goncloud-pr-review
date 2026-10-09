#!/usr/bin/env python3
"""Análisis por par de D0 (reps válidas: 1 y 3 de f9, 2 de f10). Parser canónico.

Ver docs/evidence/reviewer/D0-C0.md. Entradas: artefactos completos (con campo `result`) en /tmp/t16-f9/artefactos y /tmp/t16-f10/artefactos (copia volátil; re-descargables por run ID con los ledgers de este directorio, que no retienen el texto), ledgers por fase y SHAs del pairing. La copia versionada de los result.json NO conserva el campo `result`."""

import json
import re
import statistics
import subprocess
import sys
from pathlib import Path

WT = Path("/Users/dn/dev/wt/rc-t16")
sys.path.insert(0, str(WT))
from review_domain import read_snapshot  # noqa: E402

F9 = Path("/tmp/t16-f9")
F10 = Path("/tmp/t16-f10")
f9 = {r["id"]: r for r in map(json.loads, (F9 / "ledger.jsonl").open())}
f10 = {r["id"]: r for r in map(json.loads, (F10 / "ledger.jsonl").open())}
CASOS = [
    "pr13-push-1",
    "pr2-push-1",
    "pr2-push-2",
    "pr2-push-3",
    "pr4-push-1",
    "pr9-push-1",
]
PREV = {
    "pr2-push-1": "afb789720670aafcc09c48c4401dcabee18df556",
    "pr2-push-2": "8842b001ee6fe12ef481c48e11ae2cf016d87cda",
    "pr2-push-3": "48c43a90ae593c12f7777fdb67381f8e1d139cbc",
    "pr13-push-1": "bc01955781232c240227ba8ddbec8f19045d635a",
    "pr4-push-1": "49bfde8b34acf65f4be70ecfa7436a64790cc0e6",
    "pr9-push-1": "9fda345bc4c9720f2cbef5eb391ac6aa020fad95",
}


def corrida(caso, rep, brazo):
    if rep == 2:
        rid = f"{caso}-delta/rep2-rerun" if brazo == "delta" else f"{caso}/rep2-rerun"
        return f10[rid]
    rid = f"{caso}-delta/rep{rep}" if brazo == "delta" else f"{caso}/rep{rep}"
    return f9[rid]


def artefacto(rid):
    raiz = F10 if "/rep2-rerun" in rid else F9
    d = raiz / "artefactos" / rid.replace("/", "__")
    return json.loads((d / "result.json").read_text()), json.loads(
        (d / "manifest.json").read_text()
    )


def hallazgos(texto):
    """Hallazgos del bloque del modelo, con el parser canónico; si el comentario
    vino sin cerrar (sin `-->`), recupera el JSON por conteo de llaves y
    re-parsea canónicamente. Devuelve (lista, recuperado)."""
    load = read_snapshot(texto or "", last=True)
    nombre = type(load).__name__
    if nombre == "Legacy":
        return load.raw["findings"], False
    if nombre == "Valid":
        return [
            {
                "file": f.primary_anchor.path if f.primary_anchor else "?",
                "line": None,
                "severity": f.severity,
                "title": f.title,
                "state": {
                    "StatusOpen": "open",
                    "StatusResolved": "resolved",
                    "StatusDismissed": "dismissed",
                }[type(f.status).__name__],
            }
            for f in load.snapshot.findings
        ], False
    i = (texto or "").find("ai-review:findings=")
    if i < 0:
        return [], False
    j = texto.find("{", i)
    prof, en_cadena, escape = 0, False, False
    for k in range(j, len(texto)):
        ch = texto[k]
        if en_cadena:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                en_cadena = False
            continue
        if ch == '"':
            en_cadena = True
        elif ch == "{":
            prof += 1
        elif ch == "}":
            prof -= 1
            if prof == 0:
                cierre = (
                    texto[:j] + "<!-- ai-review:findings=" + texto[j : k + 1] + " -->"
                )
                rec = read_snapshot(cierre, last=True)
                if type(rec).__name__ == "Legacy":
                    return rec.raw["findings"], True
                return [], False
    return [], False


def normal(t):
    return [w for w in re.sub(r"[^a-z0-9]+", " ", t.casefold()).split() if len(w) > 2]


def solapa(a, b):
    na, nb = set(normal(a)), set(normal(b))
    if not na or not nb:
        return False
    return len(na & nb) / len(na | nb) >= 0.34


def emparejados(hall_c, hall_d):
    """Para cada hallazgo del control, su pareja en el delta (mismo archivo y solape)."""
    usados = set()
    parejas = {}
    for i, fc in enumerate(hall_c):
        for j, fd in enumerate(hall_d):
            if j in usados:
                continue
            if fc["file"] == fd["file"] and solapa(fc["title"], fd["title"]):
                parejas[i] = j
                usados.add(j)
                break
    return parejas


def cambiados(prev, head):
    out = subprocess.run(
        ["git", "diff", "--name-only", prev, head],
        cwd=str(WT),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return {x for x in out.splitlines() if x.strip()}


filas = []
for caso in CASOS:
    for rep in (1, 2, 3):
        ctrl, dlt = corrida(caso, rep, "control"), corrida(caso, rep, "delta")
        (rc_text, rc_man), (rd_text, rd_man) = (
            artefacto(ctrl["id"]),
            artefacto(dlt["id"]),
        )
        hall_c, rec_c = hallazgos(rc_text.get("result"))
        hall_d, rec_d = hallazgos(rd_text.get("result"))
        parejas = emparejados(hall_c, hall_d)
        hi_c = [
            i
            for i, f in enumerate(hall_c)
            if f["severity"].upper() in ("HIGH", "CRITICAL")
        ]
        perdidos = [hall_c[i] for i in hi_c if i not in parejas]
        cambio = cambiados(PREV[caso], rc_man["head"])
        falsos = [
            f for f in hall_d if f["state"] == "resolved" and f["file"] not in cambio
        ]
        hi_d = [f for f in hall_d if f["severity"].upper() in ("HIGH", "CRITICAL")]
        filas.append(
            {
                "par": f"{caso} rep{rep}",
                "rep": rep,
                "control_usd": ctrl["costo"],
                "delta_usd": dlt["costo"],
                "ratio": dlt["costo"] / ctrl["costo"],
                "hc": len(hall_c),
                "hd": len(hall_d),
                "parejas": len(parejas),
                "hic": len(hi_c),
                "hid": len(hi_d),
                "perdidos": len(perdidos),
                "perdidos_detalle": [
                    f"{f['severity']} {f['file']} :: {f['title']}" for f in perdidos
                ],
                "falsos_resueltos": len(falsos),
                "falsos_detalle": [f"{f['file']} :: {f['title']}" for f in falsos],
                "replicacion": (len(parejas) / len(hall_c)) if hall_c else None,
                "nuevos_delta": len(hall_d) - len(parejas),
                "resolved_delta": sum(1 for f in hall_d if f["state"] == "resolved"),
                "sin_bloque": len(hall_d) == 0,
                "recuperado": rec_d,
            }
        )

for f in filas:
    extra = ""
    if f["perdidos"]:
        extra += "  PERDIDO: " + " | ".join(f["perdidos_detalle"])
    if f["falsos_resueltos"]:
        extra += "  FALSO: " + " | ".join(f["falsos_detalle"])
    rep_txt = "None" if f["replicacion"] is None else f"{f['replicacion']:.2f}"
    print(
        f"{f['par']}: ${f['control_usd']:.4f} vs ${f['delta_usd']:.4f} ratio {f['ratio']:.2f} | "
        f"hall {f['hc']}/{f['hd']} parej {f['parejas']} | HiC {f['hic']}/{f['hid']} perd {f['perdidos']} | "
        f"falsos {f['falsos_resueltos']} | repl {rep_txt} | resueltas_delta {f['resolved_delta']} "
        f"{'RECUPERADO' if f['recuperado'] else ''}{extra}"
    )

print()
ratios = [f["ratio"] for f in filas]
ctrl = [f["control_usd"] for f in filas]
dlt = [f["delta_usd"] for f in filas]
difs = [d - c for c, d in zip(ctrl, dlt)]
print(
    f"pares {len(filas)}; mediana control ${statistics.median(ctrl):.4f}; mediana delta ${statistics.median(dlt):.4f}"
)
print(
    f"mediana ratios {statistics.median(ratios):.3f}; media ratios {statistics.fmean(ratios):.3f}"
)
print(f"delta mas barato en {sum(1 for r in ratios if r < 1)} de {len(ratios)} pares")
print(
    f"criterio >=20% menor: {statistics.median(dlt) <= 0.8 * statistics.median(ctrl)} (umbral ${0.8 * statistics.median(ctrl):.4f})"
)
print(
    f"perdidos High/Crit: {sum(f['perdidos'] for f in filas)}; falsos resueltos: {sum(f['falsos_resueltos'] for f in filas)}"
)
print("deltas sin bloque:", [f["par"] for f in filas if f["sin_bloque"]] or "ninguno")
for rep in (1, 2, 3):
    sub = [f for f in filas if f["rep"] == rep]
    print(
        f"rep{rep}: mediana control ${statistics.median([f['control_usd'] for f in sub]):.4f} "
        f"delta ${statistics.median([f['delta_usd'] for f in sub]):.4f} "
        f"mediana ratios {statistics.median([f['ratio'] for f in sub]):.3f}"
    )
with open("/tmp/t16-f11-pares.json", "w") as fh:
    json.dump(filas, fh, indent=1, ensure_ascii=False)
print("detalle en /tmp/t16-f11-pares.json")
