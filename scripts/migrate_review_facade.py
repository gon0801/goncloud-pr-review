import ast
from pathlib import Path

import review_context
import review_domain
import review_render
import review_sticky


MODULES = (review_context, review_domain, review_sticky, review_render)


def owner(name):
    if name in {"OPEN", "RESOLVED", "DISMISSED"}:
        return "review_domain"
    for module in MODULES:
        if hasattr(module, name):
            return module.__name__
    return None


def migrate(path):
    source = path.read_text()
    tree = ast.parse(source, filename=str(path))
    review_names = {
        alias.asname or alias.name
        for node in ast.walk(tree) if isinstance(node, ast.Import)
        for alias in node.names if alias.name == "review"
    }
    if not review_names:
        return ()
    aliases = {
        alias.name: alias.asname or alias.name
        for node in ast.walk(tree) if isinstance(node, ast.Import)
        for alias in node.names
    }
    offsets = [0]
    lines = source.splitlines(keepends=True)
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    edits = []
    owners = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
            continue
        if node.value.id not in review_names or not (module := owner(node.attr)):
            continue
        start = offsets[node.lineno - 1] + len(
            lines[node.lineno - 1].encode()[:node.col_offset].decode()
        )
        end = offsets[node.end_lineno - 1] + len(
            lines[node.end_lineno - 1].encode()[:node.end_col_offset].decode()
        )
        alias = aliases.get(module, module)
        edits.append((start, end, f"{alias}.{node.attr}"))
        owners.add(module)
    for start, end, replacement in sorted(edits, reverse=True):
        source = source[:start] + replacement + source[end:]
    needed = sorted(module for module in owners if module not in aliases)
    if needed:
        lines = source.splitlines(keepends=True)
        insert_at = next(
            i + 1 for i, line in enumerate(lines)
            if line.partition("#")[0].strip() == "import review"
        )
        line = lines[insert_at - 1]
        indent = line[:len(line) - len(line.lstrip())]
        lines[insert_at:insert_at] = [f"{indent}import {module}\n" for module in needed]
        source = "".join(lines)
    if edits:
        path.write_text(source)
    return tuple(sorted(owners))


def main():
    for path in sorted(Path("tests").rglob("*.py")):
        owners = migrate(path)
        if owners:
            print(f"{path}: {', '.join(owners)}")


if __name__ == "__main__":
    main()
