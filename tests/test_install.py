import base64
import hashlib
import json
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FAKE_GH = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import base64, hashlib, json, os, sys

    estado_ruta = os.environ["GH_STATE"]
    registro = os.environ["GH_LOG"]
    estado = json.load(open(estado_ruta))

    def guardar():
        json.dump(estado, open(estado_ruta, "w"), indent=1)

    def registrar(llamada):
        with open(registro, "a") as fh:
            fh.write(json.dumps(llamada) + "\\n")

    def fallar(codigo, mensaje):
        print(mensaje, file=sys.stderr)
        sys.exit(codigo)

    def emitir(objeto):
        valor = jq(objeto, expr)
        if isinstance(valor, list):
            print(chr(10).join(str(elemento) for elemento in valor))
            return
        print(valor if isinstance(valor, str) else json.dumps(valor))

    def jq(dato, expr):
        if expr == ".":
            return dato
        if expr.startswith(".[]."):
            return [fila.get(expr[4:]) for fila in dato]
        actual = dato
        for campo in expr.strip(".").split("."):
            actual = actual.get(campo) if isinstance(actual, dict) else None
        return actual

    def sha_de(texto):
        return hashlib.sha1(texto.encode()).hexdigest()

    args = sys.argv[1:]
    registrar(args)
    expr = "."
    if "-X" in args:
        metodo = args[args.index("-X") + 1]
        args = [a for a in args if a != "-X" and a != metodo]
    banderas = {}
    entrada = None
    i = 0
    while i < len(args):
        if args[i] in ("-f", "-F"):
            clave, _, valor = args[i + 1].partition("=")
            banderas.setdefault(clave, []).append(valor)
            i += 2
        elif args[i] == "--input":
            i += 1
            if args[i] == "-":
                entrada = sys.stdin.read()
            else:
                entrada = open(args[i]).read()
            i += 1
        elif args[i] == "--jq":
            expr = args[i + 1]
            i += 2
        elif args[i] == "-R":
            i += 2
        else:
            i += 1

    if args and args[0] == "pr":
        repo = args[args.index("-R") + 1]
        r = estado["repos"][repo]
        if args[1] == "list":
            abiertos = [
                p for p in r["prs"] if p["head"] == args[args.index("--head") + 1]
            ]
            emitir([{'number': p['number']} for p in abiertos])
            guardar()
            sys.exit(0)
        if args[1] == "create":
            numero = 1 + max((p["number"] for p in r["prs"]), default=0)
            r["prs"].append(
                {
                    "number": numero,
                    "head": args[args.index("--head") + 1],
                    "base": args[args.index("--base") + 1],
                    "title": args[args.index("--title") + 1],
                    "body": args[args.index("--body") + 1],
                }
            )
            guardar()
            print(f"https://github.com/{repo}/pull/{numero}")
            sys.exit(0)
        fallar(1, f"pr {args[1]} no simulado")

    ruta = args[1] if args and args[0] == "api" else ""
    partes = ruta.split("?")[0].strip("/").split("/")
    if len(partes) >= 1 and partes[0] != "repos":
        fallar(1, f"ruta no simulada: {ruta}")
    repo = "/".join(partes[1:3])
    r = estado["repos"].get(repo)
    if r is None:
        fallar(1, f"repo desconocido: {repo}")
    resto = partes[3:]

    if not resto:
        emitir({'default_branch': r['default']})
        guardar()
        sys.exit(0)

    if resto[:2] == ["git", "ref"]:
        rama = "/".join(resto[3:])
        punta = r["branches"].get(rama)
        if punta is None:
            fallar(1, "No such ref")
        emitir({'object': {'sha': punta}})
        guardar()
        sys.exit(0)

    if resto[:2] == ["git", "refs"] and len(resto) == 2:
        rama = banderas["ref"][0].split("refs/heads/")[1]
        if rama in r["branches"]:
            fallar(1, "la referencia ya existe")
        r["branches"][rama] = banderas["sha"][0]
        guardar()
        sys.exit(0)

    if resto[:2] == ["git", "refs"] and resto[2] == "heads":
        rama = "/".join(resto[3:])
        if banderas.get("force"):
            registrar(["force-prohibido"])
            fallar(1, "cannot force update")
        if estado.get("razas", {}).get(repo, 0) > 0:
            estado["razas"][repo] -= 1
            punta_actual = r["branches"][rama]
            ajeno = "cambio ajeno durante la instalacion"
            blob_ajeno = base64.b64encode(ajeno.encode()).decode()
            sha_ajeno = sha_de(blob_ajeno)
            r["blobs"][sha_ajeno] = blob_ajeno
            arbol_base = dict(r["trees"][r["commits"][punta_actual]["tree"]])
            arbol_base["README.md"] = sha_ajeno
            arbol_sha = "t" + sha_de(json.dumps(arbol_base, sort_keys=True))
            r["trees"][arbol_sha] = arbol_base
            commit_sha = "c" + sha_de(arbol_sha + punta_actual + "ajeno")
            r["commits"][commit_sha] = {
                "tree": arbol_sha,
                "parents": [punta_actual],
                "message": "ajeno",
            }
            r["branches"][rama] = commit_sha
            guardar()
        nueva = banderas["sha"][0]
        punta = r["branches"][rama]
        padres = r["commits"].get(nueva, {}).get("parents", [])
        if punta and punta not in padres:
            fallar(1, "Update is not a fast forward")
        r["branches"][rama] = nueva
        guardar()
        sys.exit(0)

    if resto[:2] == ["git", "commits"] and len(resto) == 3:
        commit = r["commits"].get(resto[2])
        if commit is None:
            fallar(1, "commit desconocido")
        emitir({'tree': {'sha': commit['tree']}, 'parents': [{'sha': padre} for padre in commit['parents']]})
        guardar()
        sys.exit(0)

    if resto[:2] == ["git", "commits"]:
        arbol = banderas["tree"][0]
        padres = banderas.get("parents[]", [])
        commit_sha = "c" + sha_de(arbol + "".join(padres) + banderas["message"][0])
        r["commits"][commit_sha] = {
            "tree": arbol,
            "parents": padres,
            "message": banderas["message"][0],
        }
        emitir({'sha': commit_sha})
        guardar()
        sys.exit(0)

    if resto[:2] == ["git", "blobs"]:
        contenido = banderas["content"][0]
        s = sha_de(contenido)
        r["blobs"][s] = contenido
        emitir({'sha': s})
        guardar()
        sys.exit(0)

    if resto[:2] == ["git", "trees"]:
        if not entrada:
            fallar(2, f"arbol sin cuerpo: args={args!r}")
        cuerpo = json.loads(entrada)
        base = dict(r["trees"][cuerpo["base_tree"]])
        for entrada_arbol in cuerpo["tree"]:
            if entrada_arbol.get("sha") is None:
                base.pop(entrada_arbol["path"], None)
            else:
                base[entrada_arbol["path"]] = entrada_arbol["sha"]
        arbol_sha = "t" + sha_de(json.dumps(base, sort_keys=True))
        r["trees"][arbol_sha] = base
        emitir({'sha': arbol_sha})
        guardar()
        sys.exit(0)

    fallar(1, f"endpoint no simulado: {ruta}")
    """
)


def _sembrar(repo, archivos, mensaje="base"):
    """Estado inicial: rama default con un commit raíz y sus archivos (texto)."""
    arbol = {}
    blobs = {}
    for ruta, contenido in archivos.items():
        s = hashlib.sha1(contenido.encode()).hexdigest()
        blobs[s] = base64.b64encode(contenido.encode()).decode()
        arbol[ruta] = s
    arbol_sha = (
        "t" + hashlib.sha1(json.dumps(arbol, sort_keys=True).encode()).hexdigest()
    )
    commit_sha = "c" + hashlib.sha1((arbol_sha + mensaje).encode()).hexdigest()
    return {
        "default": "main",
        "branches": {"main": commit_sha},
        "commits": {commit_sha: {"tree": arbol_sha, "parents": [], "message": mensaje}},
        "trees": {arbol_sha: arbol},
        "blobs": blobs,
        "prs": [],
    }


class InstaladorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.estado_ruta = str(Path(self.tmp.name) / "estado.json")
        self.registro_ruta = str(Path(self.tmp.name) / "llamadas.log")
        self.bin = Path(self.tmp.name) / "bin"
        self.bin.mkdir()
        gh = self.bin / "gh"
        gh.write_text(FAKE_GH)
        gh.chmod(0o755)
        self.estado = {"repos": {}}

    def _repo(self, nombre, archivos):
        self.estado["repos"][nombre] = _sembrar(nombre, archivos)

    def _guardar(self):
        json.dump(self.estado, open(self.estado_ruta, "w"), indent=1)

    def _correr(self, *argumentos, raza=None, extra=None):
        if raza:
            self.estado["razas"] = dict(raza)
        self._guardar()
        env = dict(os.environ)
        env.update(
            {
                "PATH": f"{self.bin}:{env['PATH']}",
                "GH_STATE": self.estado_ruta,
                "GH_LOG": self.registro_ruta,
            }
        )
        env.update(extra or {})
        return subprocess.run(
            [str(ROOT / "scripts" / "install.sh"), *argumentos],
            env=env,
            capture_output=True,
            text=True,
        )

    def _repo_final(self, nombre):
        self.estado = json.load(open(self.estado_ruta))
        return self.estado["repos"][nombre]

    def _archivos_de(self, repo, rama):
        punta = repo["branches"][rama]
        return repo["trees"][repo["commits"][punta]["tree"]]

    def _contenido(self, repo, archivos, ruta):
        return base64.b64decode(repo["blobs"][archivos[ruta]]).decode()


class AtomicInstall(InstaladorTest):
    def test_single_commit_replaces_writer_set(self):
        """T11: un único commit instala coordinador y worker y retira al
        escritor anterior, conservando los cambios ajenos del árbol."""
        viejo = "name: AI review\nuses: gon0801/goncloud-pr-review@main\n"
        self._repo(
            "o/r",
            {
                ".github/workflows/ai-review.yml": viejo,
                "README.md": "readme del consumidor\n",
                "src/app.py": "x = 1\n",
            },
        )
        punta_previa = self.estado["repos"]["o/r"]["branches"]["main"]
        resultado = self._correr("--coordinado", "o/r", extra={"ACTION_SHA": "a" * 40})
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        repo = self._repo_final("o/r")
        punta = repo["branches"]["chore/ai-review"]
        commit = repo["commits"][punta]
        archivos = self._archivos_de(repo, "chore/ai-review")
        self.assertIn(".github/workflows/ai-review-publish.yml", archivos)
        self.assertIn(".github/workflows/ai-review-worker.yml", archivos)
        self.assertNotIn(".github/workflows/ai-review.yml", archivos)
        self.assertIn("README.md", archivos, "los cambios ajenos se conservan")
        self.assertIn("src/app.py", archivos)
        publicador = self._contenido(
            repo, archivos, ".github/workflows/ai-review-publish.yml"
        )
        self.assertIn("repository: gon0801/goncloud-pr-review", publicador)
        self.assertIn("ref: " + "a" * 40, publicador)
        self.assertEqual(
            commit["parents"],
            [punta_previa],
            "un solo commit sobre la punta: sin commits partidos",
        )
        self.assertEqual(
            len(repo["commits"]), 2, "el instalador agrega exactamente un commit"
        )
        abiertos = [p for p in repo["prs"] if p["head"] == "chore/ai-review"]
        self.assertEqual(len(abiertos), 1, "el PR del conjunto queda abierto")
        self.assertIn(
            "a" * 40, abiertos[0]["body"], "el piloto queda fijado al SHA candidato"
        )

    def test_retry_preserves_unrelated_changes(self):
        """T11: si la rama cambia durante la instalación, reintenta sobre la
        punta nueva sin forzar y conserva el cambio ajeno."""
        viejo = "name: AI review\n"
        self._repo(
            "o/r", {".github/workflows/ai-review.yml": viejo, "README.md": "v1\n"}
        )
        resultado = self._correr(
            "--coordinado", "o/r", extra={"ACTION_SHA": "b" * 40}, raza={"o/r": 1}
        )
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        repo = self._repo_final("o/r")
        archivos = self._archivos_de(repo, "chore/ai-review")
        self.assertIn(
            "cambio ajeno",
            self._contenido(repo, archivos, "README.md"),
            "el cambio ajeno sobrevive al reintento",
        )
        self.assertIn(".github/workflows/ai-review-publish.yml", archivos)
        self.assertNotIn(".github/workflows/ai-review.yml", archivos)
        punta = repo["branches"]["chore/ai-review"]
        commit = repo["commits"][punta]
        self.assertEqual(len(commit["parents"]), 1)
        padre = repo["commits"][commit["parents"][0]]
        self.assertEqual(
            padre["message"],
            "ajeno",
            "el commit del instalador va sobre la punta nueva",
        )
        registro = open(self.registro_ruta).read()
        self.assertNotIn("force-prohibido", registro, "nunca fuerza la referencia")


class CompatibleRollback(InstaladorTest):
    def test_updates_expanded_schema3_in_current_mode(self):
        """T11: el escritor de retorno (modo actual) actualiza una memoria
        ampliada schema 3 conservando descarte, solicitud pendiente y presupuesto."""
        import hashlib
        import sys

        sys.path.insert(0, str(ROOT))
        import review
        import review_domain as domain

        lineas = tuple(f"línea {i}" for i in range(1, 31))
        digest = hashlib.sha256("\n".join(lineas[9:11]).encode()).hexdigest()
        hallazgos = [
            domain.Finding(
                id="F1",
                title="bug ubicado",
                severity="High",
                status=domain.StatusOpen(),
                primary_anchor=domain.AnchorLocated(
                    path="src/app.py",
                    blob_sha="a" * 40,
                    range=(10, 11),
                    excerpt_digest=digest,
                ),
            ),
            domain.Finding(
                id="F2",
                title="bug descartado",
                severity="Medium",
                status=domain.StatusDismissed(),
                primary_anchor=domain.AnchorLegacy(path="src/b.py", line=2),
            ),
        ]
        estado = domain.Snapshot(
            schema=3,
            generation=4,
            revision=domain.Revision(
                base_sha="b" * 40, head_sha="c" * 40, policy_digest="d" * 64
            ),
            next_id=3,
            completion=domain.PARTIAL,
            findings=hallazgos,
            command_cursor=7,
            pending_requests=[
                domain.WorkRequest(
                    id=2,
                    kind="review",
                    origin="comando",
                    basis_generation=4,
                    state="pending",
                    target=domain.ReviewTarget(
                        repository="o/r",
                        pr_number=1,
                        head_sha="c" * 40,
                        base_sha="b" * 40,
                        policy_digest="d" * 64,
                    ).json(),
                    solicitante="jefe",
                )
            ],
            request_count=2,
        )
        cuerpo = f"{review.MARKER}\n{domain.encode_snapshot(estado)}"
        load = domain.read_snapshot(cuerpo)
        self.assertIsInstance(load, domain.Valid)
        comentarios = [
            {"id": 3, "body": "ai-review: descartar F2", "user": "jefa"},
        ]
        resultado_modelo = {
            "result": "**Veredicto:** 1 High.\n\nDetalle.\n\nCOVERAGE: complete",
            "subtype": "success",
        }
        manifiesto = {
            "base": "b" * 40,
            "head": "c" * 40,
            "reviewed": ["src/app.py"],
            "excluded": [],
        }
        salida = review.actualizar_memoria_valida(
            load,
            resultado_modelo,
            manifiesto,
            "o/r",
            "1",
            "github-actions[bot]",
            comentarios,
        )
        self.assertNotIn(
            "keep", salida, f"la actualización del retorno no fue Keep: {salida}"
        )
        bloque = salida["block"]
        recarga = domain.read_snapshot(f"{review.MARKER}\n{bloque}")
        self.assertIsInstance(recarga, domain.Valid)
        final = recarga.snapshot
        self.assertEqual(final.schema, 3, "el esquema se conserva en el retorno")
        descartado = next(f for f in final.findings if f.id == "F2")
        self.assertIsInstance(descartado.status, domain.StatusDismissed)
        self.assertEqual(
            len(final.pending_requests), 1, "la solicitud pendiente sobrevive"
        )
        self.assertEqual(final.pending_requests[0].id, 2)
        self.assertEqual(final.pending_requests[0].state, "pending")
        self.assertEqual(final.command_cursor, 7)
        self.assertEqual(
            final.generation, 5, "la generación avanza con el escritor de retorno"
        )
