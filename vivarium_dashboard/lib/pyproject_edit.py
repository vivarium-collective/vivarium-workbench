"""Safe edits to workspace pyproject.toml [project.dependencies]."""
from __future__ import annotations
import re
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef]


def _find_deps_array_bounds(text: str) -> tuple[int, int] | None:
    """Return (body_start, body_end) of the [project.dependencies] array body.

    Handles brackets inside quoted strings (e.g. jsonschema[format-nongpl]).
    Returns the span of everything *between* 'dependencies = [' and the matching ']'.
    Returns None if the array is not found.
    """
    # Find `dependencies = [`
    head_m = re.search(r"dependencies\s*=\s*\[", text)
    if not head_m:
        return None

    # Walk forward from the '[' to find the matching ']',
    # skipping brackets that appear inside quoted strings.
    start_bracket = head_m.end() - 1  # position of '['
    pos = start_bracket + 1
    depth = 1
    in_single = False
    in_double = False
    while pos < len(text) and depth > 0:
        ch = text[pos]
        if in_double:
            if ch == '"' and text[pos - 1:pos] != '\\':
                in_double = False
        elif in_single:
            if ch == "'" and text[pos - 1:pos] != '\\':
                in_single = False
        else:
            if ch == '"':
                in_double = True
            elif ch == "'":
                in_single = True
            elif ch == '[':
                depth += 1
            elif ch == ']':
                depth -= 1
        pos += 1

    if depth != 0:
        return None  # unbalanced

    # body is between start_bracket+1 and pos-1 (the closing ']')
    return start_bracket + 1, pos - 1


def add_dependency(pyproject_path: Path, package: str, *, version_spec: str | None = None) -> bool:
    """Append `package[version_spec]` to [project.dependencies] if not already present.

    Returns True if a change was made; False if the dep was already declared.
    """
    if not pyproject_path.exists():
        raise FileNotFoundError(pyproject_path)

    text = pyproject_path.read_text(encoding="utf-8")
    data = tomllib.loads(text)
    deps = data.get("project", {}).get("dependencies", []) or []

    # Match existing dep by package name (ignoring version constraint / extras).
    pkg_re = re.compile(r"^\s*" + re.escape(package) + r"(\s*[\[<>=!~]|\s*$)")
    for d in deps:
        if pkg_re.match(d):
            return False  # Already declared

    new_dep = f"{package}{version_spec}" if version_spec else package

    bounds = _find_deps_array_bounds(text)
    if bounds is None:
        # No dependencies array — add one to [project] section.
        proj_m = re.search(r"^\s*\[project\]\s*$", text, re.MULTILINE)
        if not proj_m:
            raise ValueError("pyproject.toml has no [project] section")
        insertion = f'\ndependencies = [\n    "{new_dep}",\n]\n'
        text = text[:proj_m.end()] + insertion + text[proj_m.end():]
    else:
        body_start, body_end = bounds
        body = text[body_start:body_end]
        existing = body.rstrip()
        trailing_comma = existing.endswith(",")
        if not existing.strip():
            new_body = f'\n    "{new_dep}",\n'
        elif trailing_comma:
            new_body = body.rstrip() + f'\n    "{new_dep}",\n'
        else:
            new_body = body.rstrip() + f',\n    "{new_dep}",\n'
        text = text[:body_start] + new_body + text[body_end:]

    pyproject_path.write_text(text)
    return True


def remove_dependency(pyproject_path: Path, package: str) -> bool:
    """Remove `package` (any version spec) from [project.dependencies].

    Idempotent — returns True if the entry was removed, False if it was absent.
    Handles extras notation (e.g. ``package[extra]>=1.0``) and trailing commas.
    """
    if not pyproject_path.exists():
        raise FileNotFoundError(pyproject_path)

    text = pyproject_path.read_text(encoding="utf-8")
    data = tomllib.loads(text)
    deps = data.get("project", {}).get("dependencies", []) or []

    # Check whether the dep is actually present.
    pkg_re = re.compile(r"^\s*" + re.escape(package) + r"(\s*[\[<>=!~]|\s*$)")
    if not any(pkg_re.match(d) for d in deps):
        return False  # already absent

    bounds = _find_deps_array_bounds(text)
    if bounds is None:
        return False

    body_start, body_end = bounds
    body = text[body_start:body_end]

    # Remove the line(s) matching `package` (with optional extras/version).
    # We match a quoted entry that starts with the package name.
    line_re = re.compile(
        r'\n?\s*"' + re.escape(package) + r'(\[.*?\])?[^"]*",?\s*'
    )
    new_body, n_subs = line_re.subn("", body)
    if n_subs == 0:
        return False

    text = text[:body_start] + new_body + text[body_end:]
    pyproject_path.write_text(text)
    return True


def remove_uv_source(pyproject_path: Path, package: str) -> bool:
    """Remove the `package = { ... }` entry from [tool.uv.sources].

    Idempotent — returns True if removed, False if absent.
    """
    if not pyproject_path.exists():
        raise FileNotFoundError(pyproject_path)

    text = pyproject_path.read_text(encoding="utf-8")
    data = tomllib.loads(text)
    existing_sources = data.get("tool", {}).get("uv", {}).get("sources", {}) or {}
    if package not in existing_sources:
        return False  # already absent

    # Remove the key=value line inside [tool.uv.sources].
    # The entry looks like: `package = { path = "...", editable = true }` or similar.
    line_re = re.compile(
        r"^\s*" + re.escape(package) + r"\s*=\s*\{[^\n]*\}\s*\n?",
        re.MULTILINE,
    )
    new_text, n_subs = line_re.subn("", text)
    if n_subs == 0:
        return False

    pyproject_path.write_text(new_text)
    return True


def add_uv_source(
    pyproject_path: Path,
    package: str,
    *,
    path: str | None = None,
    git: str | None = None,
    editable: bool = True,
) -> bool:
    """Append a [tool.uv.sources] entry mapping `package` → local path or git URL.

    Required for git-only packages (pbg-* repos) that aren't on PyPI. Without
    this, `uv pip install -e .` fails to resolve them. Either path OR git
    must be set.

    Returns True if a change was made; False if the source was already declared.
    """
    if not pyproject_path.exists():
        raise FileNotFoundError(pyproject_path)
    if not path and not git:
        raise ValueError("either path or git must be provided")

    text = pyproject_path.read_text(encoding="utf-8")
    data = tomllib.loads(text)
    existing_sources = data.get("tool", {}).get("uv", {}).get("sources", {}) or {}
    if package in existing_sources:
        return False  # already declared

    if path is not None:
        entry = f'{{ path = "{path}", editable = {"true" if editable else "false"} }}'
    else:
        entry = f'{{ git = "{git}" }}'

    # Find existing [tool.uv.sources] block; append OR create.
    block_re = re.compile(r"^\s*\[tool\.uv\.sources\]\s*$", re.MULTILINE)
    block_m = block_re.search(text)

    if block_m:
        # Insert the new key right after the section header
        insertion = f"\n{package} = {entry}"
        head_end = block_m.end()
        text = text[:head_end] + insertion + text[head_end:]
    else:
        # Append a new [tool.uv.sources] block at the end of the file
        if not text.endswith("\n"):
            text += "\n"
        text += (
            "\n# Auto-managed by the dashboard catalog Install button. Maps git-only\n"
            "# pbg-* packages to their local external/<name> submodule path.\n"
            "[tool.uv.sources]\n"
            f"{package} = {entry}\n"
        )

    pyproject_path.write_text(text)
    return True
