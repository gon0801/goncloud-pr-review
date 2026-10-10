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
        if "[]." in expr:
            prefijo, campo = expr.split("[].", 1)
            lista = dato
            for parte in prefijo.strip(".").split("."):
                if parte:
                    lista = lista.get(parte) if isinstance(lista, dict) else None
            campo_final = campo.strip(".")
            return [
                fila.get(campo_final) for fila in (lista or []) if isinstance(fila, dict)
            ]
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
        if args[1] == "edit":
            numero = int(args[2])
            for p in r["prs"]:
                if p["number"] == numero:
                    if "--title" in args:
                        p["title"] = args[args.index("--title") + 1]
                    if "--body" in args:
                        p["body"] = args[args.index("--body") + 1]
            guardar()
            print(f"https://github.com/{repo}/pull/{numero}")
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

    for falla, pendientes in list(estado.get("fallos", {}).items()):
        if pendientes > 0 and falla in ruta:
            estado["fallos"][falla] = pendientes - 1
            guardar()
            fallar(1, f"fallo transitorio inyectado en {ruta}")

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
            ajeno = "cambio ajeno"
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

    def ancestros(commit):
        vistos, pila = set(), [commit]
        while pila:
            actual = pila.pop()
            if actual in vistos or actual not in r["commits"]:
                continue
            vistos.add(actual)
            pila.extend(r["commits"][actual]["parents"])
        return vistos

    if resto == ["merges"]:
        rama, cabeza = banderas["base"][0], banderas["head"][0]
        punta, otra = r["branches"][rama], r["branches"][cabeza]
        if otra in ancestros(punta):
            sys.exit(0)
        comunes = ancestros(punta) & ancestros(otra)
        base_merge = next(
            c for c in comunes if not any(c in r["commits"][o]["parents"] for o in comunes)
        )
        arbol_base = r["trees"][r["commits"][base_merge]["tree"]]
        nuestro = r["trees"][r["commits"][punta]["tree"]]
        suyo = r["trees"][r["commits"][otra]["tree"]]
        fusion = {}
        for ruta_arbol in set(arbol_base) | set(nuestro) | set(suyo):
            b, n, s = (t.get(ruta_arbol) for t in (arbol_base, nuestro, suyo))
            elegido = s if n == b else n if s == b or n == s else "conflicto"
            if elegido == "conflicto":
                fallar(1, f"Merge conflict (HTTP 409): {ruta_arbol}")
            if elegido is not None:
                fusion[ruta_arbol] = elegido
        arbol_sha = "t" + sha_de(json.dumps(fusion, sort_keys=True))
        r["trees"][arbol_sha] = fusion
        commit_sha = "c" + sha_de(arbol_sha + punta + otra)
        r["commits"][commit_sha] = {
            "tree": arbol_sha,
            "parents": [punta, otra],
            "message": f"Merge {cabeza} into {rama}",
        }
        r["branches"][rama] = commit_sha
        emitir({"sha": commit_sha})
        guardar()
        sys.exit(0)

    if resto[:2] == ["git", "blobs"]:
        contenido = banderas["content"][0]
        s = sha_de(contenido)
        r["blobs"][s] = contenido
        emitir({'sha': s})
        guardar()
        sys.exit(0)

    if resto[:2] == ["git", "trees"] and len(resto) == 3:
        apuntador = resto[2]
        if apuntador in r["commits"]:
            apuntador = r["commits"][apuntador]["tree"]
        arbol = r["trees"].get(apuntador)
        if arbol is None:
            fallar(1, "árbol desconocido")
        rutas = sorted(arbol)
        if "recursive=1" not in ruta:
            rutas = [ruta_hijo for ruta_hijo in rutas if "/" not in ruta_hijo]
        emitir({"tree": [{"path": ruta_hijo} for ruta_hijo in rutas]})
        guardar()
        sys.exit(0)

    if resto[:2] == ["git", "trees"]:
        if not entrada:
            fallar(2, f"arbol sin cuerpo: args={args!r}")
        cuerpo = json.loads(entrada)
        if cuerpo.get("base_tree"):
            base = dict(r["trees"][cuerpo["base_tree"]])
        else:
            base = {}
        for entrada_arbol in cuerpo["tree"]:
            if entrada_arbol.get("sha") is None:
                if entrada_arbol["path"] not in base:
                    fallar(1, f"no se puede borrar un path ausente: {entrada_arbol['path']}")
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

    def _correr(self, *argumentos, raza=None, extra=None, fallos=None):
        if fallos:
            self.estado["fallos"] = dict(fallos)
        if raza:
            self.estado["razas"] = dict(raza)
        self._guardar()
        env = dict(os.environ)
        env.update(
            {
                "PATH": f"{self.bin}:{env['PATH']}",
                "GH_STATE": self.estado_ruta,
                "GH_LOG": self.registro_ruta,
                # las pruebas del modo coordinado son de piloto; la negativa
                # sin la bandera tiene su propia prueba
                "AI_REVIEW_PILOTO": "1",
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
        worker = self._contenido(
            repo, archivos, ".github/workflows/ai-review-worker.yml"
        )
        for instalado in (publicador, worker):
            self.assertIn("repository: gon0801/goncloud-pr-review", instalado)
            self.assertIn("ref: " + "a" * 40, instalado)
            self.assertIn("path: ai-review-code", instalado)
            self.assertIn("$GITHUB_WORKSPACE/ai-review-code/review.py", instalado)
        self.assertIn(
            "uses: actions/checkout@v4\n        with:\n          persist-credentials: false",
            worker,
            "el checkout del consumidor queda para traer el PR como datos",
        )
        self.assertEqual(commit["parents"], [punta_previa])
        self.assertEqual(len(repo["commits"]), 2)
        abiertos = [p for p in repo["prs"] if p["head"] == "chore/ai-review"]
        self.assertEqual(len(abiertos), 1)
        self.assertIn("a" * 40, abiertos[0]["body"])
        self.assertIn("AI_REVIEW_API_KEY", abiertos[0]["body"])
        self.assertIn("DEEPSEEK_API_KEY", abiertos[0]["body"])
        self.assertNotIn("FALLBACK_API_KEY", abiertos[0]["body"])
        self.assertIn("AI_REVIEW_API_KEY", commit["message"])
        self.assertIn("DEEPSEEK_API_KEY", commit["message"])

    def test_retry_preserves_unrelated_changes(self):
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


class RamaPropiaVieja(InstaladorTest):
    """Tras un squash merge, chore/ai-review queda divergente de main: el PR que
    salga de ahí tiene que mergear limpio y dejar main con el conjunto pedido."""

    def _commit(self, repo, padres, archivos, mensaje):
        arbol = {}
        for ruta, contenido in archivos.items():
            s = hashlib.sha1(contenido.encode()).hexdigest()
            repo["blobs"][s] = base64.b64encode(contenido.encode()).decode()
            arbol[ruta] = s
        arbol_sha = (
            "t" + hashlib.sha1(json.dumps(arbol, sort_keys=True).encode()).hexdigest()
        )
        repo["trees"][arbol_sha] = arbol
        commit_sha = "c" + hashlib.sha1((arbol_sha + mensaje).encode()).hexdigest()
        repo["commits"][commit_sha] = {
            "tree": arbol_sha,
            "parents": padres,
            "message": mensaje,
        }
        return commit_sha

    def _squash_mergeado(self, previo, instalado):
        self._repo("o/r", previo)
        repo = self.estado["repos"]["o/r"]
        base = repo["branches"]["main"]
        rama = self._commit(repo, [base], instalado, "instalación en la rama")
        squash = self._commit(repo, [base], instalado, "squash del PR")
        repo["branches"]["chore/ai-review"] = rama
        repo["branches"]["main"] = squash
        return squash

    def _ancestros(self, repo, commit):
        vistos, pila = set(), [commit]
        while pila:
            actual = pila.pop()
            if actual not in vistos:
                vistos.add(actual)
                pila.extend(repo["commits"][actual]["parents"])
        return vistos

    def test_el_retorno_tras_un_squash_merge_parte_de_main(self):
        coordinado = {
            ".github/workflows/ai-review-publish.yml": "publish\n",
            ".github/workflows/ai-review-worker.yml": "worker\n",
            "README.md": "readme\n",
        }
        squash = self._squash_mergeado(
            {".github/workflows/ai-review.yml": "viejo\n", "README.md": "readme\n"},
            coordinado,
        )
        resultado = self._correr("o/r", extra={"ACTION_SHA": "c" * 40})
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        repo = self._repo_final("o/r")
        punta = repo["branches"]["chore/ai-review"]
        self.assertIn(
            squash,
            self._ancestros(repo, punta),
            "el PR de retorno contiene la punta de main: su merge no choca",
        )
        self.assertEqual(
            sorted(self._archivos_de(repo, "chore/ai-review")),
            [".github/workflows/ai-review.yml", "README.md"],
        )
        self.assertNotIn("force-prohibido", open(self.registro_ruta).read())

    def test_la_instalacion_coordinada_tras_un_squash_merge_parte_de_main(self):
        directo = {".github/workflows/ai-review.yml": "directo\n", "README.md": "v1\n"}
        squash = self._squash_mergeado(
            {
                ".github/workflows/ai-review-publish.yml": "publish viejo\n",
                ".github/workflows/ai-review-worker.yml": "worker viejo\n",
                "README.md": "v1\n",
            },
            directo,
        )
        resultado = self._correr("--coordinado", "o/r", extra={"ACTION_SHA": "a" * 40})
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        repo = self._repo_final("o/r")
        self.assertIn(
            squash, self._ancestros(repo, repo["branches"]["chore/ai-review"])
        )
        self.assertEqual(
            sorted(self._archivos_de(repo, "chore/ai-review")),
            [
                ".github/workflows/ai-review-publish.yml",
                ".github/workflows/ai-review-worker.yml",
                "README.md",
            ],
        )

    def _rama_en_conflicto(self):
        self._repo(
            "o/r",
            {
                ".github/workflows/ai-review-publish.yml": "publish\n",
                ".github/workflows/ai-review-worker.yml": "worker\n",
                "README.md": "a\n",
            },
        )
        repo = self.estado["repos"]["o/r"]
        base = repo["branches"]["main"]
        coordinado = {
            ".github/workflows/ai-review-publish.yml": "publish\n",
            ".github/workflows/ai-review-worker.yml": "worker\n",
        }
        repo["branches"]["chore/ai-review"] = self._commit(
            repo, [base], {**coordinado, "README.md": "rama\n"}, "rama"
        )
        repo["branches"]["main"] = self._commit(
            repo, [base], {**coordinado, "README.md": "main\n"}, "main"
        )
        return repo["branches"]["chore/ai-review"]

    def test_una_rama_propia_en_conflicto_con_main_sale_alto_sin_publicar(self):
        for modo in (("--coordinado", "o/r"), ("o/r",)):
            with self.subTest(modo=modo[0]):
                punta_previa = self._rama_en_conflicto()
                resultado = self._correr(*modo, extra={"ACTION_SHA": "a" * 40})
                self.assertNotEqual(resultado.returncode, 0)
                self.assertIn("tiene conflicto con main", resultado.stderr)
                self.assertNotIn("bórrala", resultado.stderr)
                repo = self._repo_final("o/r")
                self.assertEqual(repo["branches"]["chore/ai-review"], punta_previa)
                self.assertEqual(repo["prs"], [])

    def test_un_fallo_que_no_es_conflicto_no_se_reporta_como_conflicto(self):
        for modo in (("--coordinado", "o/r"), ("o/r",)):
            with self.subTest(modo=modo[0]):
                punta_previa = self._rama_en_conflicto()
                resultado = self._correr(
                    *modo, extra={"ACTION_SHA": "a" * 40}, fallos={"merges": 1}
                )
                self.assertNotEqual(resultado.returncode, 0)
                self.assertIn(
                    "no pude poner la rama chore/ai-review al día", resultado.stderr
                )
                self.assertNotIn("conflicto", resultado.stderr)
                repo = self._repo_final("o/r")
                self.assertEqual(repo["branches"]["chore/ai-review"], punta_previa)
                self.assertEqual(repo["prs"], [])


class CoordinadoSoloPiloto(InstaladorTest):
    """F3 de #86: el coordinado no se despliega en consumidores hasta pasar el
    piloto 2; sin la bandera explícita el instalador se niega sin tocar nada."""

    def test_sin_la_bandera_de_piloto_no_instala_el_coordinado(self):
        self._repo("o/r", {".github/workflows/ai-review.yml": "viejo\n"})
        for bandera in ("", "0", "si"):
            with self.subTest(bandera=bandera):
                resultado = self._correr(
                    "--coordinado",
                    "o/r",
                    extra={"ACTION_SHA": "a" * 40, "AI_REVIEW_PILOTO": bandera},
                )
                self.assertEqual(resultado.returncode, 2)
                self.assertIn("AI_REVIEW_PILOTO=1", resultado.stderr)
                repo = self._repo_final("o/r")
                self.assertNotIn("chore/ai-review", repo["branches"])
                self.assertEqual(repo["prs"], [])

    def test_el_retorno_al_modo_actual_no_necesita_la_bandera(self):
        self._repo(
            "o/r",
            {
                ".github/workflows/ai-review-publish.yml": "publish\n",
                ".github/workflows/ai-review-worker.yml": "worker\n",
            },
        )
        resultado = self._correr(
            "o/r", extra={"ACTION_SHA": "c" * 40, "AI_REVIEW_PILOTO": ""}
        )
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        repo = self._repo_final("o/r")
        self.assertEqual(
            sorted(self._archivos_de(repo, "chore/ai-review")),
            [".github/workflows/ai-review.yml"],
        )


class CompatibleRollback(InstaladorTest):
    def test_el_retiro_atomico_restaura_el_escritor_anterior(self):
        self._repo(
            "o/r",
            {
                ".github/workflows/ai-review-publish.yml": "publish\n",
                ".github/workflows/ai-review-worker.yml": "worker\n",
                "README.md": "readme del consumidor\n",
            },
        )
        repo_antes = self.estado["repos"]["o/r"]
        repo_antes["branches"]["chore/ai-review"] = repo_antes["branches"]["main"]
        punta_previa = repo_antes["branches"]["main"]
        resultado = self._correr("o/r", extra={"ACTION_SHA": "c" * 40})
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        repo = self._repo_final("o/r")
        punta = repo["branches"]["chore/ai-review"]
        commit = repo["commits"][punta]
        archivos = self._archivos_de(repo, "chore/ai-review")
        self.assertIn(".github/workflows/ai-review.yml", archivos)
        self.assertNotIn(".github/workflows/ai-review-publish.yml", archivos)
        self.assertNotIn(".github/workflows/ai-review-worker.yml", archivos)
        restituido = self._contenido(repo, archivos, ".github/workflows/ai-review.yml")
        self.assertIn("gon0801/goncloud-pr-review@" + "c" * 40, restituido)
        self.assertIn("README.md", archivos, "los cambios ajenos se conservan")
        self.assertEqual(commit["parents"], [punta_previa], "un solo commit atómico")
        self.assertNotIn("force-prohibido", open(self.registro_ruta).read())

    def test_un_fallo_transitorio_no_publica_arbol_sin_base(self):
        self._repo(
            "o/r",
            {
                ".github/workflows/ai-review.yml": "viejo\n",
                "README.md": "readme del consumidor\n",
            },
        )
        punta_previa = self.estado["repos"]["o/r"]["branches"]["main"]
        resultado = self._correr(
            "--coordinado",
            "o/r",
            extra={"ACTION_SHA": "d" * 40},
            fallos={"git/commits/": 1},
        )
        self.assertNotEqual(
            resultado.returncode, 0, "el fallo transitorio debe salir alto"
        )
        repo = self._repo_final("o/r")
        self.assertEqual(
            len(repo["commits"]),
            1,
            "sin fallo de referencia no se publica ningún commit",
        )
        self.assertEqual(
            repo["branches"].get("chore/ai-review"),
            punta_previa,
            "la rama queda en la punta de partida",
        )

    def test_el_retorno_sale_alto_si_no_puede_leer_el_arbol(self):
        self._repo(
            "o/r",
            {
                ".github/workflows/ai-review-publish.yml": "publish\n",
                ".github/workflows/ai-review-worker.yml": "worker\n",
                "README.md": "readme\n",
            },
        )
        repo_antes = self.estado["repos"]["o/r"]
        repo_antes["branches"]["chore/ai-review"] = repo_antes["branches"]["main"]
        punta_previa = repo_antes["branches"]["main"]
        resultado = self._correr(
            "o/r", extra={"ACTION_SHA": "e" * 40}, fallos={"git/trees": 1}
        )
        self.assertNotEqual(resultado.returncode, 0)
        self.assertIn(
            "no pude leer el árbol",
            resultado.stderr,
            "sale alto por la lectura del árbol, no degradando al PUT",
        )
        repo = self._repo_final("o/r")
        self.assertEqual(repo["branches"]["chore/ai-review"], punta_previa)

    def test_coordinado_funciona_sin_el_escritor_anterior(self):
        self._repo(
            "o/r", {"README.md": "readme del consumidor\n", "src/app.py": "x = 1\n"}
        )
        resultado = self._correr("--coordinado", "o/r", extra={"ACTION_SHA": "f" * 40})
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        repo = self._repo_final("o/r")
        archivos = self._archivos_de(repo, "chore/ai-review")
        self.assertIn(".github/workflows/ai-review-publish.yml", archivos)
        self.assertIn(".github/workflows/ai-review-worker.yml", archivos)
        self.assertNotIn(".github/workflows/ai-review.yml", archivos)
        self.assertIn("README.md", archivos)

    def test_el_pr_existente_se_actualiza_con_el_cuerpo_del_modo(self):
        self._repo(
            "o/r",
            {
                ".github/workflows/ai-review-publish.yml": "publish\n",
                ".github/workflows/ai-review-worker.yml": "worker\n",
                "README.md": "readme\n",
            },
        )
        repo_antes = self.estado["repos"]["o/r"]
        repo_antes["branches"]["chore/ai-review"] = repo_antes["branches"]["main"]
        repo_antes["prs"].append(
            {
                "number": 9,
                "head": "chore/ai-review",
                "base": "main",
                "title": "viejo",
                "body": "cuerpo viejo",
            }
        )
        resultado = self._correr("o/r")
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        repo = self._repo_final("o/r")
        pr = repo["prs"][0]
        self.assertEqual(pr["number"], 9)
        self.assertNotIn("@" + "e" * 40, pr["body"], "sin ACTION_SHA no hay pin")
        self.assertIn("ci: revisión automática", pr["title"])
        self.assertNotIn("cuerpo viejo", pr["body"])

    def test_updates_expanded_schema3_in_current_mode(self):
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
        self.assertEqual(final.schema, 3)
        descartado = next(f for f in final.findings if f.id == "F2")
        self.assertIsInstance(descartado.status, domain.StatusDismissed)
        self.assertEqual(len(final.pending_requests), 1)
        self.assertEqual(final.pending_requests[0].id, 2)
        self.assertEqual(final.pending_requests[0].state, "pending")
        self.assertEqual(final.command_cursor, 7)
        self.assertEqual(final.generation, 5)
        self.assertEqual([f.id for f in final.findings], ["F1", "F2"])
