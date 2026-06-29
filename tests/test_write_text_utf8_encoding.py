"""
Static guard: every .write_text(...) call in vivarium_dashboard/lib/*.py and
vivarium_dashboard/server.py must pass an encoding= argument.

The check handles multi-line calls by tracking paren depth until the closing
`)` of the write_text call is found (no fixed lookahead limit).

This test PASSES on the swept tree and will FAIL if a bare write_text is
reintroduced.
"""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent / "vivarium_dashboard"


def _bare_write_text_calls(text: str) -> list[tuple[int, str]]:
    """Return (1-based lineno, stripped line) for every uncovered write_text call.

    A call is 'covered' when encoding= appears somewhere between the opening
    paren of write_text and its matching closing paren.
    """
    lines = text.splitlines()
    offenders = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if ".write_text(" not in line or "write_bytes" in line:
            i += 1
            continue

        # Find the position just after the opening `(` of write_text
        wt_pos = line.index(".write_text(")
        after_open = wt_pos + len(".write_text(")  # index of char after `(`

        # Track paren depth to find the matching `)` that closes write_text
        depth = 1
        call_chars: list[str] = []
        found = False
        close_line = i

        for j in range(i, len(lines)):
            scan = lines[j][after_open:] if j == i else lines[j]
            for ch in scan:
                call_chars.append(ch)
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        found = True
                        close_line = j
                        break
            if found:
                break

        if not found:
            # Malformed — conservatively flag the line
            offenders.append((i + 1, line.strip()))
            i += 1
            continue

        call_text = "".join(call_chars)
        if "encoding=" not in call_text:
            offenders.append((i + 1, line.strip()))

        i = close_line + 1

    return offenders


def test_all_lib_write_text_calls_are_utf8():
    lib_files = list((ROOT / "lib").rglob("*.py"))
    server_file = ROOT / "server.py"
    all_files = sorted(lib_files) + [server_file]

    offenders: list[str] = []
    for f in all_files:
        src = f.read_text(encoding="utf-8")
        for lineno, snippet in _bare_write_text_calls(src):
            offenders.append(f"{f.relative_to(ROOT.parent.parent)}:{lineno}: {snippet}")

    assert not offenders, (
        "Found write_text() calls without encoding=:\n"
        + "\n".join(f"  {o}" for o in offenders)
    )
