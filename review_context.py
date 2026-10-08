"""Preparación del contexto de revisión sobre un repositorio Git.

Construye el PreparedReview (modo, delta real, alcance histórico, entregados
y omisiones) que el revisor incremental consume, y reúne los ayudantes de
contexto que la fase 2 de T14 eliminará de review.py.
"""

import ast
import dataclasses
import fnmatch
import hashlib
import json
import os
import re
import select
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import review_domain as domain
from review_domain import (
    COMPLETE_CLAIM,
    DISMISSED,
    OPEN,
    PARTIAL,
    RESOLVED,
    UNKNOWN,
)

DEFAULT_EXCLUDES = [
    "*.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "go.sum",
    "*.min.js",
    "*.min.css",
    "*.map",
    "*.snap",
    "dist/**",
    "build/**",
    "vendor/**",
    "**/node_modules/**",
    "**/__snapshots__/**",
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.webp",
    "*.ico",
    "*.pdf",
    "*.woff",
    "*.woff2",
    "*.ttf",
    "*.zip",
    "*.gz",
    "*.tgz",
]

PRIORITY = [
    ("config", (".json", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".env.example")),
    ("docs", (".md", ".mdx", ".txt", ".rst")),
]

CALLERS_MAX_FILES = 20
CALLERS_MAX_SYMBOLS = 30
CALLERS_MAX_SYMBOLS_PER_FILE = 6
CALLERS_MAX_MATCHES = 10
CALLERS_MAX_BYTES = 30000
TESTS_MAX_FILES = 20
TESTS_MAX_RESULTS = 40
TESTS_MAX_BYTES = 20000
CONVENTIONS_MAX_BYTES = 8000
CONVENTION_FILES = ("CLAUDE.md", "AGENTS.md")
CONTEXTO_SELECTIVO_MAX_BYTES = 6000

GREP_MAX_EXAMINED_BYTES = 8_000_000
GREP_MAX_SECONDS = 15
GREP_READ_CHUNK = 65536

INCREMENTAL_MAX_TURNS = 30
INCREMENTAL_SMALL_BYTES = 30_000
INCREMENTAL_SMALL_FILES = 5
DEFAULT_MAX_TURNS = 60
LARGE_DIFF_MAX_TURNS = 80

IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
STOPWORDS = frozenset(
    """
def class return import from pass none true false null nil self this new delete
const let var function func fn struct enum interface type package range go chan
for while if elif else elseif unless switch case match break continue do done
with try except catch finally raise throw throws async await yield public private
protected static final abstract virtual override void int long short float double
string bool boolean byte char auto signed unsigned sizeof typedef union goto
the and for with from that this these those are was were will would have has had
not but you your can all any each per use used into over more most other than
then when what which who how why code file files test tests should must may
que del los las una uno unos unas para por con como mas pero sus este esta estos
estas entre sin sobre hasta desde donde porque cuando muy mucho tambien solo
cada dos son esta estan hay ser fue eran ello esto eso aqui alli ahora antes
""".split()
)

TEST_DIRS = frozenset(
    {
        "test",
        "tests",
        "__tests__",
        "spec",
        "specs",
        "testing",
        "test_utils",
        "test-utils",
        "testutils",
    }
)
TEST_NAME_RE = re.compile(r"^(test|spec)s?([_.-]|$)|[_.-](test|spec)s?$")
TEST_CAMEL_RE = re.compile(r"^Tests?[A-Z_]|[a-z0-9]Tests?$")


@dataclasses.dataclass(frozen=True)
class GitPath:
    raw: bytes

    @property
    def text(self):
        return self.raw.decode("utf-8")

    def diagnostic(self):
        return self.raw.decode("utf-8", "backslashreplace")

    def label(self):
        try:
            return self.raw.decode("utf-8")
        except UnicodeDecodeError:
            return self.diagnostic()


@dataclasses.dataclass(frozen=True)
class SearchComplete:
    paths: tuple


@dataclasses.dataclass(frozen=True)
class SearchTruncated:
    paths: tuple
    reason: str


@dataclasses.dataclass(frozen=True)
class SearchFailed:
    reason: str


@dataclasses.dataclass(frozen=True)
class Omission:
    ruta: str
    causa: str


@dataclasses.dataclass(frozen=True)
class Obligation:
    ruta: str


@dataclasses.dataclass(frozen=True)
class ContextRef:
    """Pista con procedencia hacia un archivo relacionado con el diff (C0).

    `relacion` distingue "sintactica" (un ast.Call al símbolo, verificado
    con ast.parse del archivo .py tal como está en HEAD) de "textual"
    (comentarios, strings, prosa, otros lenguajes o .py que no parsean).
    `papel` distingue pruebas de consumidores. `busqueda` declara el estado
    de la búsqueda que produjo la referencia: "completa", "truncada: <motivo>"
    o "falló: <motivo>".
    """

    archivo: str
    simbolo: str
    relacion: str
    papel: str
    busqueda: str


@dataclasses.dataclass(frozen=True)
class GitRepository:
    """Ventana a Git: todo comando corre con cwd explícito en root."""

    root: Path

    def run(self, *args, check=True):
        return subprocess.run(
            ["git", *args], cwd=self.root, check=check, capture_output=True
        )

    def merge_base(self, a, b):
        return self.run("merge-base", a, b).stdout.decode("utf-8").strip()

    def is_ancestor(self, prev_sha, head):
        return (
            self.run(
                "merge-base", "--is-ancestor", prev_sha, head, check=False
            ).returncode
            == 0
        )

    def changed_files(self, a, b):
        out = self.run("diff", "--name-only", "-z", a, b).stdout
        return [GitPath(rec) for rec in out.split(b"\0") if rec]

    def numstat(self, base, head, renames=False):
        extra = [] if renames else ["--no-renames"]
        return self.run("diff", "--numstat", "-z", *extra, base, head).stdout

    def diff_bytes(self, base, head, path):
        raw = path.raw if isinstance(path, GitPath) else path.encode("utf-8")
        return self.run(
            "diff", "--no-color", "--no-renames", "-U10", base, head, b"--", raw
        ).stdout

    def blob_at(self, rev, path):
        raw = path.raw if isinstance(path, GitPath) else path.encode("utf-8")
        proc = self.run("rev-parse", rev.encode("utf-8") + b":" + raw, check=False)
        sha = proc.stdout.strip()
        if proc.returncode != 0 or not sha:
            return None
        return sha.decode("utf-8")

    def cat_file_exists(self, spec):
        return self.run("cat-file", "-e", spec, check=False).returncode == 0

    def cat_file_text(self, sha):
        return self.run("cat-file", "-p", sha).stdout.decode("utf-8")


@dataclasses.dataclass(frozen=True)
class PreparedReview:
    plan: domain.ReviewPlan
    hechos: domain.RepositoryFacts
    entregados: tuple
    omisiones: tuple
    modo: str
    motivo: str
    cobertura: str
    partes: dict = dataclasses.field(default_factory=dict)
    sin_codificar: tuple = ()
    over_budget_bytes: int = 0
    avisos: tuple = ()


def matches(path, pattern):
    if "/" not in pattern:
        return fnmatch.fnmatch(path.rsplit("/", 1)[-1], pattern)
    if pattern.startswith("**/"):
        parts = path.split("/")
        return any(matches("/".join(parts[i:]), pattern[3:]) for i in range(len(parts)))
    if pattern.endswith("/**"):
        pattern = pattern[:-3] + "/*"
    return fnmatch.fnmatch(path, pattern)


def excluded_by(path, patterns):
    return next((p for p in patterns if matches(path, p)), None)


def priority(path):
    lower = path.lower()
    for rank, (_, exts) in enumerate(PRIORITY, start=1):
        if lower.endswith(exts):
            return rank
    return 0


def changed_symbols(chunk, limit=CALLERS_MAX_SYMBOLS_PER_FILE):
    """Identifiers on added/removed diff lines, most frequent first, minus stopwords."""
    counts = {}
    for line in chunk.splitlines():
        if not line.startswith(("+", "-")) or line.startswith(("+++", "---")):
            continue
        for ident in IDENT_RE.findall(line[1:]):
            if ident.lower() in STOPWORDS:
                continue
            counts[ident] = counts.get(ident, 0) + 1
    return sorted(counts, key=lambda s: (-counts[s], s.lower()))[:limit]


def trim_utf8(text, ceiling, notice):
    """Cut text so the final UTF-8 bytes (notice included) fit the ceiling.

    Drops bytes from the end until the remainder decodes as valid UTF-8, so a
    multibyte character is never replaced or split mid-sequence.
    """
    if len(text.encode("utf-8")) <= ceiling:
        return text
    room = ceiling - len(notice.encode("utf-8"))
    data = text.encode("utf-8")[: max(0, room)].rstrip()
    while data:
        try:
            return data.decode("utf-8") + notice
        except UnicodeDecodeError:
            data = data[:-1]
    return notice


def looks_like_test(path):
    parts = path.split("/")
    name = parts[-1]
    stem = name.split(".", 1)[0]
    dirs = [p.lower() for p in parts[:-1]]
    return (
        any(d in TEST_DIRS or d.endswith((".tests", ".test")) for d in dirs)
        or name.lower() == "conftest.py"
        or bool(TEST_NAME_RE.search(stem.lower()))
        or ".test." in name.lower()
        or ".spec." in name.lower()
        or bool(TEST_CAMEL_RE.search(stem))
    )


def prev_findings_markdown(state):
    findings = (state or {}).get("findings", []) if state else []
    if not findings:
        return "# Sin hallazgos previos en este PR.\n"

    def line(finding):
        where = (
            finding["file"]
            if not finding["line"]
            else f"{finding['file']}:{finding['line']}"
        )
        also = [path for path in finding.get("files", [])[1:]]
        extra = f" (archivos relacionados: {', '.join(also)})" if also else ""
        return f"- {finding['id']} {finding['severity']} · `{where}` · {finding['title']}{extra}"

    lines = ["# Hallazgos anteriores de este PR", ""]
    groups = [
        (OPEN, "## Abiertos: verifícalos contra el diff y repítelos con su mismo id"),
        (
            RESOLVED,
            "## Resueltos: repítelos con su mismo id; no los describas de nuevo salvo que hayan vuelto",
        ),
        (
            DISMISSED,
            "## Descartados por una persona: NO los reportes, NO los repitas en el bloque y NO los describas en el texto",
        ),
    ]
    for state_name, header in groups:
        group = [f for f in findings if f["state"] == state_name]
        if group:
            lines += [header, "", *[line(f) for f in group], ""]
    lines += [
        'Los hallazgos nuevos llevan `"id": "F-new"`. Nunca emitas "dismissed": solo una persona descarta.'
    ]
    return "\n".join(lines) + "\n"


def max_turns_for_diff(diff_bytes, n_files):
    if diff_bytes > 300_000 or n_files > 20:
        return LARGE_DIFF_MAX_TURNS
    return DEFAULT_MAX_TURNS


def resolve_max_turns(manifest, work):
    raw = os.environ.get("MAX_TURNS", "auto").strip().lower()
    if raw not in ("", "auto"):
        try:
            turns = int(raw)
        except ValueError:
            sys.exit(f"ai-review: MAX_TURNS inválido: {raw!r} (usa un número o 'auto')")
        if turns < 1:
            sys.exit(f"ai-review: MAX_TURNS inválido: {raw!r} (usa un número o 'auto')")
        return turns
    diff_bytes = manifest.get("diff_bytes")
    if diff_bytes is None:
        patch = work / "diff.patch"
        diff_bytes = len(patch.read_bytes()) if patch.exists() else 0
    n_files = len(manifest["reviewed"])
    if (
        manifest.get("mode") == "incremental"
        and diff_bytes <= INCREMENTAL_SMALL_BYTES
        and n_files <= INCREMENTAL_SMALL_FILES
    ):
        return INCREMENTAL_MAX_TURNS
    return max_turns_for_diff(diff_bytes, n_files)


def digest_de_politica(policy):
    canon = json.dumps(sorted(dataclasses.asdict(policy).items()), sort_keys=True)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def grep_files(repo, patterns, limit, predicate=None, *, max_bytes=None, timeout=None):
    """Files at HEAD mentioning any of the patterns (fixed strings, OR).

    Streams `git grep -z -l` and applies the candidate filter BEFORE the limit,
    so a capped search can never swallow candidates that a later filter would
    have kept. The result states what happened: Complete (the stream ended or
    the designed limit was reached and no match had to be skipped), Truncated
    (a ceiling was hit or non-UTF-8 matches had to be skipped, maybe with zero
    paths), Failed (git errored). Only an empty Complete means "the search
    found nothing".
    """
    if not patterns:
        return SearchComplete(())
    ceiling = max_bytes if max_bytes is not None else GREP_MAX_EXAMINED_BYTES
    deadline = time.monotonic() + (timeout if timeout is not None else GREP_MAX_SECONDS)
    with tempfile.TemporaryFile() as errors:
        proc = subprocess.Popen(
            [
                "git",
                "grep",
                "-z",
                "-l",
                "--fixed-strings",
                *[arg for pattern in patterns for arg in ("-e", pattern)],
            ],
            stdout=subprocess.PIPE,
            stderr=errors,
            cwd=repo.root,
        )

        def stop_proc():
            proc.kill()
            proc.wait()
            proc.stdout.close()

        accepted, examined, tail, skipped = [], 0, b"", 0
        reason, limit_hit = None, False
        while reason is None and not limit_hit:
            left = deadline - time.monotonic()
            if left <= 0:
                reason = f"techo de tiempo ({timeout if timeout is not None else GREP_MAX_SECONDS:g} s)"
                break
            ready, _, _ = select.select([proc.stdout], [], [], left)
            if not ready:
                reason = f"techo de tiempo ({timeout if timeout is not None else GREP_MAX_SECONDS:g} s)"
                break
            chunk = os.read(proc.stdout.fileno(), GREP_READ_CHUNK)
            if not chunk:
                break
            examined += len(chunk)
            if examined > ceiling:
                reason = f"techo de salida ({ceiling} bytes examinados)"
                break
            parts = (tail + chunk).split(b"\0")
            tail = parts.pop()
            for raw in parts:
                if not raw:
                    continue
                try:
                    path = raw.decode("utf-8")
                except UnicodeDecodeError:
                    skipped += 1
                    continue
                if predicate is not None and not predicate(path):
                    continue
                accepted.append(path)
                if len(accepted) >= limit:
                    limit_hit = True
                    break
        if reason is not None or limit_hit:
            stop_proc()
        else:
            code = proc.wait()
            if code not in (0, 1):
                errors.seek(0)
                detail = errors.read().decode("utf-8", "backslashreplace").strip()
                proc.stdout.close()
                return SearchFailed(detail or f"git grep terminó con código {code}")
            proc.stdout.close()
        if skipped:
            omitted = f"{skipped} ruta(s) no UTF-8 omitida(s)"
            reason = f"{reason}; {omitted}" if reason else omitted
        if reason is not None:
            return SearchTruncated(tuple(accepted), reason)
        return SearchComplete(tuple(accepted))


def build_callers(repo, reviewed, chunks):
    lines = [
        "# Dónde aparece cada símbolo cambiado (búsqueda de texto con git grep: es una pista, no una prueba;",
        "# no ve usos dinámicos, por reflexión o armados con strings)",
        "",
    ]
    total = 0
    for path in reviewed[:CALLERS_MAX_FILES]:
        symbols = (
            changed_symbols(chunks.get(path, "")) if total < CALLERS_MAX_SYMBOLS else []
        )
        if not symbols:
            continue
        lines.append(f"## {path}")
        for symbol in symbols:
            if total >= CALLERS_MAX_SYMBOLS:
                break
            total += 1
            lines.append(f"### `{symbol}`")
            result = grep_files(repo, [symbol], CALLERS_MAX_MATCHES + 1)
            if isinstance(result, SearchFailed):
                lines.append(f"- búsqueda falló: {result.reason}")
                continue
            matches = list(result.paths)
            if isinstance(result, SearchTruncated):
                lines += [f"- {m}" for m in matches[:CALLERS_MAX_MATCHES]]
                if len(matches) > CALLERS_MAX_MATCHES:
                    lines.append(
                        "- … y más (símbolo muy común, acota con Grep si lo necesitas)"
                    )
                lines.append(f"- búsqueda truncada: {result.reason}")
                continue
            if not matches:
                lines.append(
                    "- (git grep no encontró el texto en otro archivo; puede haber usos dinámicos)"
                )
            else:
                lines += [f"- {m}" for m in matches[:CALLERS_MAX_MATCHES]]
                if len(matches) > CALLERS_MAX_MATCHES:
                    lines.append(
                        "- … y más (símbolo muy común, acota con Grep si lo necesitas)"
                    )
        lines.append("")
    if total == 0:
        return "(El diff no trae símbolos identificables; explora con Grep.)\n"
    text = "\n".join(lines)
    return trim_utf8(text, CALLERS_MAX_BYTES, "\n…(recortado por tamaño)…\n")


def build_tests(repo, reviewed):
    lines = [
        "# Pruebas que mencionan archivos del diff (precalculado con git grep)",
        "",
    ]
    seen, truncated = set(), None
    for path in reviewed[:TESTS_MAX_FILES]:
        name = path.rsplit("/", 1)[-1]
        stem = name.rsplit(".", 1)[0] if "." in name else name
        patterns = [p for p in (stem, name) if len(p) >= 3]
        result = grep_files(
            repo, patterns, TESTS_MAX_RESULTS, predicate=looks_like_test
        )
        if isinstance(result, SearchFailed):
            return trim_utf8(
                f"(La búsqueda de pruebas falló: {result.reason})\n",
                TESTS_MAX_BYTES,
                "…(recortado por tamaño)…\n",
            )
        if isinstance(result, SearchTruncated):
            truncated = result.reason
        for match in result.paths:
            if len(seen) >= TESTS_MAX_RESULTS:
                break
            if match not in seen:
                seen.add(match)
                lines.append(f"- {match} (menciona `{stem}`)")
        if len(seen) >= TESTS_MAX_RESULTS:
            break
    if not seen and truncated is None:
        return "(Ninguna prueba menciona los archivos del diff; busca la cobertura con Grep.)\n"
    if truncated is not None:
        lines.append(f"- búsqueda de pruebas truncada: {truncated}")
    text = "\n".join(lines) + "\n"
    return trim_utf8(text, TESTS_MAX_BYTES, "…(recortado por tamaño)…\n")


def build_conventions(repo, base):
    parts = []
    for name in CONVENTION_FILES:
        found = repo.run("show", f"{base}:{name}", check=False)
        if found.returncode == 0 and found.stdout.strip():
            contenido = found.stdout.decode("utf-8")
            parts.append(f"# {name} (de la rama base)\n\n{contenido}")
    if not parts:
        return "(Este repo no tiene CLAUDE.md ni AGENTS.md en la rama base.)\n"
    return trim_utf8(
        "\n\n".join(parts),
        CONVENTIONS_MAX_BYTES,
        "\n\n…(recortado)…\n",
    )


def is_ancestor(repo, prev_sha, head):
    return repo.is_ancestor(prev_sha, head)


def decide_mode(repo, prev_sha, head):
    """Full on first review, same-sha re-run, or rebase; incremental otherwise (B2/B7)."""
    if not prev_sha:
        return "full", "no-prev"
    if prev_sha == head:
        return "full", "same-sha"
    if not is_ancestor(repo, prev_sha, head):
        return "full", "rebase"
    return "incremental", ""


def changed_since(repo, prev_sha, head):
    return repo.changed_files(prev_sha, head)


def files_matching_base(repo, paths, base, head):
    """Files whose blob is identical at base and head: the PR no longer changes them."""
    same = set()
    for path in paths:
        if (
            repo.run("diff", "--quiet", base, head, "--", path, check=False).returncode
            == 0
        ):
            same.add(path)
    return same


def delta_real(repo, desde, head):
    """Delta real desde..head: (rutas decodificables, omisiones, calculado)."""
    try:
        proc = repo.run("diff", "--name-only", "-z", desde, head, check=False)
    except Exception:
        return (), ("delta real no calculable",), False
    if proc.returncode != 0:
        return (), ("delta real no calculable",), False
    rutas, ilegibles = [], 0
    for rec in proc.stdout.split(b"\0"):
        if not rec:
            continue
        try:
            rutas.append(rec.decode("utf-8"))
        except UnicodeDecodeError:
            ilegibles += 1
    omisiones = ()
    if ilegibles:
        omisiones = (f"ruta no representable en el delta: {ilegibles}",)
    return tuple(rutas), omisiones, True


def blobs_de_head(repo, rutas):
    """Contenido HEAD por ruta (líneas sin salto final) para las citas (F0)."""
    blobs = {}
    for ruta in rutas:
        blob = repo.run("rev-parse", f"HEAD:{ruta}", check=False).stdout.strip()
        if not blob:
            continue
        sha = blob.decode("utf-8")
        contenido = repo.run("cat-file", "-p", sha, check=False).stdout
        blobs[(ruta, sha)] = tuple(contenido.decode("utf-8").splitlines())
    return blobs


def delta_real_con_renombres(repo, previous_head, head):
    """Delta previous_head..head con detección de renombres (D0).

    Devuelve (rutas, renombres, omisiones, calculado): las rutas usan la
    nueva para renombres (vieja, nueva en ese orden dentro del registro -z)
    y la vieja para borrados; sin previous_head verificable o sin relación
    de ancestro el delta no es calculable y queda vacío.
    """
    if not previous_head:
        return (), (), (), False
    if not repo.cat_file_exists(f"{previous_head}^{{commit}}"):
        return (), (), (), False
    if not repo.is_ancestor(previous_head, head):
        return (), (), (), False
    proc = repo.run(
        "diff", "--name-status", "-z", "-M", previous_head, head, check=False
    )
    if proc.returncode != 0:
        return (), (), ("delta real no calculable",), False
    rutas, renombres, ilegibles = [], [], 0
    registros = proc.stdout.split(b"\0")
    i = 0
    while i < len(registros) and registros[i]:
        letra = registros[i][:1]
        i += 1
        if letra in (b"R", b"C"):
            vieja, nueva = registros[i], registros[i + 1]
            i += 2
            try:
                par = (nueva.decode("utf-8"), vieja.decode("utf-8"))
            except UnicodeDecodeError:
                ilegibles += 1
                continue
            renombres.append(par)
            rutas.append(par[0])
            continue
        try:
            rutas.append(registros[i].decode("utf-8"))
        except UnicodeDecodeError:
            ilegibles += 1
        i += 1
    omisiones = ()
    if ilegibles:
        omisiones = (f"ruta no representable en el delta: {ilegibles}",)
    return tuple(rutas), tuple(renombres), omisiones, True


def decidir_modo(repo, current, head, base_sha, digest):
    """(modo, motivo, previous_head) según la memoria y el target (D0).

    Incremental exige revisión previa completa, head distinto y ancestro,
    y la misma base y política con que se creó la memoria.
    """
    revision = current.revision if current is not None else None
    if revision is None or not revision.head_sha:
        return "full", "memoria: no hay revisión previa registrada", None
    previous_head = revision.head_sha
    if current.completion != COMPLETE_CLAIM:
        return "full", "cobertura: la revisión previa no fue completa", previous_head
    modo, motivo = decide_mode(repo, previous_head, head)
    if modo == "full":
        return modo, motivo, previous_head
    if revision.base_sha != base_sha:
        return "full", "base: la base previa difiere de la solicitada", previous_head
    if revision.policy_digest != digest:
        return "full", "política: el digest previo difiere", previous_head
    return "incremental", "", previous_head


def rutas_abiertas(current):
    """Rutas de los hallazgos abiertos cuya ancla primaria trae ruta."""
    rutas = []
    for hallazgo in current.findings if current is not None else []:
        if not isinstance(hallazgo.status, domain.StatusOpen):
            continue
        ancla = hallazgo.primary_anchor
        ruta = getattr(ancla, "path", "") if ancla is not None else ""
        if ruta:
            rutas.append(ruta)
    return rutas


def estado_de_busqueda(result):
    if isinstance(result, SearchComplete):
        return "completa"
    if isinstance(result, SearchTruncated):
        return f"truncada: {result.reason}"
    return f"falló: {result.reason}"


def nombres_invocados(repo, head, archivo, cache):
    if archivo in cache:
        return cache[archivo]
    nombres = None
    if archivo.endswith(".py"):
        sha = repo.blob_at(head, archivo)
        if sha:
            try:
                arbol = ast.parse(repo.cat_file_text(sha))
            except (SyntaxError, ValueError):
                arbol = None
            if arbol is not None:
                llamadas = set()
                for nodo in ast.walk(arbol):
                    if isinstance(nodo, ast.Call):
                        if isinstance(nodo.func, ast.Name):
                            llamadas.add(nodo.func.id)
                        elif isinstance(nodo.func, ast.Attribute):
                            llamadas.add(nodo.func.attr)
                nombres = frozenset(llamadas)
    cache[archivo] = nombres
    return nombres


def contexto_selectivo(
    repo, head, entregados, partes, contexto_max_bytes, busqueda_max_bytes
):
    fuentes = {}
    for path in entregados:
        for simbolo in changed_symbols(partes.get(path.text, "")):
            fuentes.setdefault(simbolo, []).append(path.text)
    refs, avisos = {}, []
    if len(fuentes) > CALLERS_MAX_SYMBOLS:
        avisos.append(f"símbolos recortados: {CALLERS_MAX_SYMBOLS} de {len(fuentes)}")
    cache = {}
    for simbolo in list(fuentes)[:CALLERS_MAX_SYMBOLS]:
        propias = set(fuentes[simbolo])
        simple = grep_files(
            repo,
            [simbolo],
            CALLERS_MAX_MATCHES + 1,
            max_bytes=busqueda_max_bytes,
        )
        llamada = grep_files(
            repo,
            [f"{simbolo}("],
            CALLERS_MAX_MATCHES + 1,
            max_bytes=busqueda_max_bytes,
        )
        razones = [
            resultado.reason
            for resultado in (simple, llamada)
            if isinstance(resultado, SearchTruncated)
        ]
        if razones:
            avisos.append(f"pista truncada: {simbolo} ({razones[0]})")
        fallas = [
            resultado.reason
            for resultado in (simple, llamada)
            if isinstance(resultado, SearchFailed)
        ]
        if fallas:
            avisos.append(f"pista falló: {simbolo} ({fallas[0]})")
        por_archivo = {}
        for resultado in (llamada, simple):
            estado = estado_de_busqueda(resultado)
            for archivo in getattr(resultado, "paths", ()):
                por_archivo.setdefault(archivo, estado)
        for archivo, estado in por_archivo.items():
            if archivo in propias:
                continue
            invocados = nombres_invocados(repo, head, archivo, cache)
            refs[(archivo, simbolo)] = ContextRef(
                archivo=archivo,
                simbolo=simbolo,
                relacion=(
                    "sintactica"
                    if invocados is not None and simbolo in invocados
                    else "textual"
                ),
                papel="prueba" if looks_like_test(archivo) else "consumidor",
                busqueda=estado,
            )
    ordenadas = sorted(
        refs.values(),
        key=lambda ref: (
            ref.relacion != "sintactica",
            ref.papel != "prueba",
            ref.archivo,
            ref.simbolo,
        ),
    )
    seleccion, usados = [], 0
    for ref in ordenadas:
        linea = (
            f"{ref.archivo} {ref.relacion} {ref.papel} {ref.simbolo} {ref.busqueda}\n"
        )
        costo = len(linea.encode("utf-8"))
        if usados + costo > contexto_max_bytes:
            avisos.append("contexto truncado")
            break
        seleccion.append(ref)
        usados += costo
    return tuple(seleccion), tuple(avisos)


def prepare_review(
    repo,
    request,
    current,
    policy,
    selectivo=False,
    contexto_max_bytes=CONTEXTO_SELECTIVO_MAX_BYTES,
    busqueda_max_bytes=None,
):
    target = request.target or {}
    head = target.get("head_sha", "")
    base = target.get("base_sha", "")
    merge_base = repo.merge_base(base, head) or base
    digest = digest_de_politica(policy)

    modo, motivo, previous_head = decidir_modo(repo, current, head, base, digest)
    delta_rutas, renombres, omis_delta, delta_calculado = delta_real_con_renombres(
        repo, previous_head, head
    )

    blobs, revertidas = {}, ()
    if delta_calculado:
        revertidas = tuple(
            ruta
            for ruta in delta_rutas
            if repo.blob_at(head, ruta) == repo.blob_at(merge_base, ruta)
        )
        for nueva, vieja in renombres:
            for revision, ruta in ((previous_head, vieja), (head, nueva)):
                sha = repo.blob_at(revision, ruta)
                if sha:
                    blobs[(ruta, sha)] = tuple(repo.cat_file_text(sha).splitlines())

    historicas, ilegibles_alcance = [], 0
    for path in repo.changed_files(merge_base, head):
        try:
            historicas.append(path.text)
        except UnicodeDecodeError:
            ilegibles_alcance += 1

    omisiones, hechos_omisiones = [], list(omis_delta)
    if not delta_calculado:
        hechos_omisiones.append("delta real no calculable")
    if ilegibles_alcance:
        hechos_omisiones.append(
            f"ruta no representable en el alcance: {ilegibles_alcance}"
        )

    patrones = list(DEFAULT_EXCLUDES) + list(policy.exclude_patterns or ())
    numstat = repo.numstat(merge_base, head)
    binarias, sin_codificar = set(), []
    for rec in filter(None, numstat.split(b"\0")):
        added, deleted, raw = rec.split(b"\t", 2)
        path = GitPath(raw)
        try:
            texto = path.text
        except UnicodeDecodeError:
            sin_codificar.append(path.diagnostic())
            continue
        if added == b"-" and deleted == b"-":
            binarias.add(texto)
    if modo != "incremental":
        for diagnostico in sin_codificar:
            omisiones.append(Omission(ruta=diagnostico, causa="encoding"))
            hechos_omisiones.append(f"archivo excluido (encoding): {diagnostico}")

    rutas_plan = list(delta_rutas) if modo == "incremental" else historicas
    candidatas = delta_rutas if modo == "incremental" else historicas
    a_revisar = []
    for ruta in candidatas:
        patron = excluded_by(ruta, patrones)
        if patron:
            omisiones.append(Omission(ruta=ruta, causa=f"filtro {patron}"))
        elif ruta in binarias:
            omisiones.append(Omission(ruta=ruta, causa="binario"))
        else:
            a_revisar.append(ruta)

    presupuesto = policy.diff_max_bytes
    entregados, usados, partes, over_budget = [], 0, {}, 0
    for ruta in sorted(a_revisar, key=lambda r: (priority(r), r)):
        crudo = repo.diff_bytes(merge_base, head, ruta)
        try:
            chunk = crudo.decode("utf-8")
        except UnicodeDecodeError:
            omisiones.append(Omission(ruta=ruta, causa="encoding"))
            hechos_omisiones.append(f"archivo excluido (encoding): {ruta}")
            continue
        chunk_bytes = len(chunk.encode("utf-8"))
        if usados + chunk_bytes > presupuesto and entregados:
            causa = "presupuesto" if policy.strict_budget else "budget"
            omisiones.append(Omission(ruta=ruta, causa=causa))
            hechos_omisiones.append(f"archivo excluido ({causa}): {ruta}")
            continue
        if not entregados and chunk_bytes > presupuesto:
            if policy.strict_budget:
                omisiones.append(Omission(ruta=ruta, causa="presupuesto"))
                hechos_omisiones.append(f"archivo excluido (presupuesto): {ruta}")
                continue
            hechos_omisiones.append(
                f"over_budget_bytes {max(0, chunk_bytes - presupuesto)}: {ruta}"
            )
            over_budget = max(over_budget, chunk_bytes - presupuesto)
        entregados.append(GitPath(ruta.encode("utf-8")))
        partes[ruta] = chunk
        usados += chunk_bytes

    context_refs, avisos = (), ()
    if selectivo:
        context_refs, avisos = contexto_selectivo(
            repo, head, entregados, partes, contexto_max_bytes, busqueda_max_bytes
        )

    obligaciones = tuple(
        Obligation(ruta=ruta)
        for ruta in dict.fromkeys(list(rutas_plan) + rutas_abiertas(current))
    )
    if any(om.causa == "presupuesto" for om in omisiones):
        cobertura = PARTIAL
    elif not entregados:
        cobertura = UNKNOWN
    else:
        cobertura = COMPLETE_CLAIM

    revision = domain.Revision(base_sha=merge_base, head_sha=head, policy_digest=digest)
    plan = domain.ReviewPlan(
        revision=revision,
        basis_generation=request.basis_generation,
        mode=modo,
        previous_sha=previous_head,
        changed_paths=tuple(rutas_plan),
        obligations=obligaciones,
        context_refs=context_refs,
        exclusions=tuple(patrones),
        delivered=tuple(path.text for path in entregados),
        policy=policy,
    )
    hechos = domain.RepositoryFacts(
        revision=revision,
        changed_paths=tuple(delta_rutas),
        delta_calculado=delta_calculado,
        reverted_paths=revertidas,
        renames=tuple(renombres),
        blobs=blobs,
        omissions=tuple(hechos_omisiones),
        historical_paths=tuple(historicas),
    )
    return PreparedReview(
        plan=plan,
        hechos=hechos,
        entregados=tuple(entregados),
        omisiones=tuple(omisiones),
        modo=modo,
        motivo=motivo,
        cobertura=cobertura,
        partes=partes,
        sin_codificar=tuple(sin_codificar),
        over_budget_bytes=over_budget,
        avisos=avisos,
    )
