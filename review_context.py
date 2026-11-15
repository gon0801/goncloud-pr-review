"""Preparación del contexto de revisión sobre un repositorio Git.

Construye el PreparedReview (archivos entregados, omisiones y partes del diff)
que el revisor incremental consume, y reúne los ayudantes de
contexto que la fase 2 de T14 eliminará de review.py.
"""

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

from review_domain import (
    DISMISSED,
    OPEN,
    PARTIAL,
    RESOLVED,
    is_complete,
)

# Centinela del libro de cobertura: un archivo borrado cuyo diff (la
# eliminación) ya fue entregado y acreditado por una pasada.
LIBRO_BORRADO = "deleted"

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
class GitRepository:
    """Ventana a Git: todo comando corre con cwd explícito en root.

    Con `attr_source`, git lee .gitattributes de ese árbol y no del worktree.
    """

    root: Path
    attr_source: str = ""

    def run(self, *args, check=True):
        atributos = [f"--attr-source={self.attr_source}"] if self.attr_source else []
        return subprocess.run(
            ["git", *atributos, *args], cwd=self.root, check=check, capture_output=True
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

    def diff_sin_renombres(self, *args, check=True):
        return self.run("diff", "--no-renames", *args, check=check)

    def changed_files(self, a, b):
        out = self.diff_sin_renombres("--name-only", "-z", a, b).stdout
        return [GitPath(rec) for rec in out.split(b"\0") if rec]

    def numstat(self, base, head):
        return self.diff_sin_renombres("--numstat", "-z", base, head).stdout

    def diff_bytes(self, base, head, path):
        raw = path.raw if isinstance(path, GitPath) else path.encode("utf-8")
        return self.diff_sin_renombres(
            "--no-color", "-U10", base, head, b"--", raw
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
    entregados: tuple
    omisiones: tuple
    partes: dict = dataclasses.field(default_factory=dict)
    sin_codificar: tuple = ()
    over_budget_bytes: int = 0


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
    """Digest de la política que decide si la cobertura previa sigue válida.

    Quedan fuera las perillas de una corrida concreta (`diff_mode`,
    `diff_max_bytes`, `strict_budget`, `exclude_patterns`): cambiar el techo
    de difusión, las exclusiones extra o la etiqueta de modo de una pasada no
    cambia lo que significa la cobertura que esa pasada dejó registrada, y el
    digest publicado lleva el modo de SU corrida y nunca sus exclusiones de
    entorno. Lo que sí invalida la cobertura (identidad, reglas, schema)
    entra completo.
    """
    campos = {
        campo: valor
        for campo, valor in dataclasses.asdict(policy).items()
        if campo
        not in ("diff_mode", "diff_max_bytes", "strict_budget", "exclude_patterns")
    }
    canon = json.dumps(sorted(campos.items()), sort_keys=True)
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
            repo.diff_sin_renombres(
                "--quiet", base, head, "--", path, check=False
            ).returncode
            == 0
        ):
            same.add(path)
    return same


def files_reverted(repo, paths, base, previous, head):
    """Files the PR changed at `previous` and no longer changes at `head`."""
    if not previous:
        return set()
    changed_before = [
        path
        for path in paths
        if repo.run(
            "diff", "--quiet", f"{base}...{previous}", "--", path, check=False
        ).returncode
        == 1
    ]
    return files_matching_base(repo, changed_before, base, head)


def delta_real(repo, desde, head):
    """Delta real desde..head: (rutas decodificables, omisiones, calculado)."""
    if desde == head:
        # El diff de un commit consigo mismo es vacío por definición; no
        # consultarlo evita que un delta re-run dependa del repositorio local.
        return (), (), True
    try:
        proc = repo.diff_sin_renombres("--name-only", "-z", desde, head, check=False)
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


def delta_desde_previo(repo, previous_head, head):
    if not previous_head:
        return (), (), False
    if not repo.cat_file_exists(f"{previous_head}^{{commit}}"):
        return (), (), False
    if not repo.is_ancestor(previous_head, head):
        return (), (), False
    return delta_real(repo, previous_head, head)


def decidir_modo(repo, current, head, base_sha, digest, cobertura_previa=None):
    """(modo, motivo, previous_head) según la memoria y el target (D0).

    Incremental exige revisión previa completa, head distinto y ancestro,
    y la misma política con que se creó la memoria. Una parcial con
    libro de cobertura continúa donde el libro quedó. Un movimiento de
    base no invalida la cobertura: el libro acredita blobs, que siguen
    siendo los revisados, y el motivo lo documenta mientras prepare acota
    el alcance por esa cobertura. La política distinta sí fuerza full:
    cambia la identidad de las reglas que acreditó la pasada.
    """
    revision = current.revision if current is not None else None
    if revision is None or not revision.head_sha:
        return "full", "memoria: no hay revisión previa registrada", None
    previous_head = revision.head_sha
    motivo_base = (
        "base: la base previa difiere de la solicitada; "
        "el alcance se acota por la cobertura válida"
        if revision.base_sha != base_sha
        else ""
    )
    if not is_complete(current.completion):
        if (
            current.completion == PARTIAL
            and cobertura_previa
            and revision.policy_digest == digest
        ):
            modo, motivo = decide_mode(repo, previous_head, head)
            if modo == "incremental" or motivo == "same-sha":
                return "continuation", motivo_base, previous_head
        return "full", "cobertura: la revisión previa no fue completa", previous_head
    modo, motivo = decide_mode(repo, previous_head, head)
    if modo == "full":
        return modo, motivo, previous_head
    if revision.policy_digest != digest:
        return "full", "política: el digest previo difiere", previous_head
    return "incremental", motivo_base, previous_head


def prepare_review(
    repo,
    request,
    current,
    policy,
    cobertura_previa=None,
    alcance_legado=None,
):
    target = request.target or {}
    head = target.get("head_sha", "")
    base = target.get("base_sha", "")
    # Un `-diff` en el .gitattributes del propio PR sacaría del diff cualquier archivo.
    repo = dataclasses.replace(repo, attr_source=base)
    merge_base = repo.merge_base(base, head) or base
    digest = digest_de_politica(policy)

    if alcance_legado is None:
        modo, _, previous_head = decidir_modo(
            repo, current, head, base, digest, cobertura_previa
        )
    else:
        modo, previous_head = alcance_legado
    delta_rutas, _, _ = delta_desde_previo(repo, previous_head, head)

    historicas = []
    for path in repo.changed_files(merge_base, head):
        try:
            historicas.append(path.text)
        except UnicodeDecodeError:
            continue

    omisiones = []

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

    previa = cobertura_previa or {}
    base_movida = False
    if modo in ("incremental", "continuation"):
        previous_base = current.revision.base_sha if current.revision else None
        merge_base_previo = repo.merge_base(previous_base or base, previous_head)
        base_movida = merge_base_previo != merge_base
        alcance_previo = {
            path.label()
            for path in repo.changed_files(merge_base_previo, previous_head)
        }
        # Alcance acotado por la cobertura válida del libro: el delta real
        # desde la acreditación más lo del alcance del PR que el libro no
        # acredita contra head. Acreditado y sin cambios queda fuera: ese
        # contenido ya lo revisó la pasada que lo acreditó. Sin libro no hay
        # acreditación fina que conservar y el alcance es solo el delta.
        # Del delta entra lo que el PR cambia en head o cambiaba en
        # previous_head: así entra una reversión del PR y queda fuera lo que
        # trae un merge de la base sin que el PR lo haya tocado.
        # Mover la base puede meter en una ruta acreditada un tramo que antes
        # pertenecía a la base y nunca llegó al revisor: ese diff distinto sí entra.
        alcance_pr = set(historicas) | alcance_previo
        candidatas = [ruta for ruta in delta_rutas if ruta in alcance_pr]
        if previa:
            vistas = set(candidatas)
            for ruta in historicas:
                if ruta in vistas:
                    continue
                blob = repo.blob_at(head, ruta)
                if blob is None:
                    if previa.get(ruta) != LIBRO_BORRADO:
                        candidatas.append(ruta)
                elif blob != previa.get(ruta):
                    candidatas.append(ruta)
            if base_movida:
                for ruta in historicas:
                    if ruta in candidatas or ruta not in previa:
                        continue
                    if repo.diff_bytes(
                        merge_base_previo, previous_head, ruta
                    ) != repo.diff_bytes(merge_base, head, ruta):
                        candidatas.append(ruta)
        elif base_movida:
            for ruta in historicas:
                if ruta in candidatas:
                    continue
                if ruta not in alcance_previo or repo.diff_bytes(
                    merge_base_previo, previous_head, ruta
                ) != repo.diff_bytes(merge_base, head, ruta):
                    candidatas.append(ruta)
    else:
        candidatas = historicas
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
    # El delta desde previous_head solo lo paga la ruta que el libro acredita:
    # su tramo merge_base..previous_head ya lo revisó la pasada que la acreditó
    # y solo prev..head es nuevo. La ruta del delta ausente del libro llega
    # desde merge_base: ese contenido nunca se revisó, aunque haya cambiado
    # desde previous_head. En un archivo nuevo ambos orígenes dan lo mismo.
    delta_set = set(delta_rutas)
    for ruta in sorted(a_revisar, key=lambda r: (priority(r), r)):
        # previous_head solo si el tramo ya acreditado sigue siendo el mismo
        # contra la base nueva. Si el cambio de base metió contenido que la
        # pasada anterior no vio, el origen es la base actual del PR.
        acreditado = modo != "full" and ruta in delta_set and ruta in previa
        if acreditado and base_movida and repo.diff_bytes(
            merge_base_previo, previous_head, ruta
        ) != repo.diff_bytes(merge_base, previous_head, ruta):
            acreditado = False
        origen = previous_head if acreditado else merge_base
        crudo = repo.diff_bytes(origen, head, ruta)
        try:
            chunk = crudo.decode("utf-8")
        except UnicodeDecodeError:
            omisiones.append(Omission(ruta=ruta, causa="encoding"))
            continue
        chunk_bytes = len(chunk.encode("utf-8"))
        # En continuación no hay excepción histórica del primer archivo: lo que
        # no cabe queda excluido y la pasada degrada, igual que con los demás.
        if usados + chunk_bytes > presupuesto and (
            entregados or modo == "continuation"
        ):
            causa = "presupuesto" if policy.strict_budget else "budget"
            omisiones.append(Omission(ruta=ruta, causa=causa))
            continue
        if not entregados and chunk_bytes > presupuesto:
            if policy.strict_budget:
                omisiones.append(Omission(ruta=ruta, causa="presupuesto"))
                continue
            over_budget = max(over_budget, chunk_bytes - presupuesto)
        entregados.append(GitPath(ruta.encode("utf-8")))
        partes[ruta] = chunk
        usados += chunk_bytes

    return PreparedReview(
        entregados=tuple(entregados),
        omisiones=tuple(omisiones),
        partes=partes,
        sin_codificar=tuple(sin_codificar),
        over_budget_bytes=over_budget,
    )
