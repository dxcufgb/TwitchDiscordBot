"""Static checks for the Inno Setup script (compiling it needs Windows; CI does that)."""

import re
from pathlib import Path

ISS = Path(__file__).resolve().parent.parent / "packaging" / "windows" / "TwitchDiscordBot.iss"
DIRECTIVES = {"define", "ifdef", "ifndef", "if", "elif", "else", "endif", "error", "include", "pragma", "undef", "emit"}


def test_only_preprocessor_lines_start_with_hash():
    # ISPP treats every line whose first non-blank character is '#' as a directive, so Pascal
    # character codes like #13#10 must never start a line.
    for number, line in enumerate(ISS.read_text(encoding="ascii").splitlines(), 1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            word = re.match(r"#\s*(\w+)", stripped)
            assert word and word.group(1) in DIRECTIVES, f"line {number} would be read as a directive: {line!r}"


def test_crlf_and_ascii():
    data = ISS.read_bytes()
    data.decode("ascii")
    assert data.count(b"\n") == data.count(b"\r\n")


def test_packaged_files_exist():
    base = ISS.parent
    for source in re.findall(r'^Source: "([^"]+)"', ISS.read_text(encoding="ascii"), re.M):
        matches = list(base.glob(source.replace("\\", "/")))
        assert matches, f"[Files] source not found: {source}"


def test_plain_msgbox_only_in_interactive_code():
    # /SUPPRESSMSGBOXES does not suppress MsgBox, so a MsgBox reachable in a silent install hangs it.
    # Plain MsgBox is only allowed where the code already returns early when running silently.
    allowed = {"NextButtonClick", "InitializeUninstall"}
    current = None
    for number, line in enumerate(ISS.read_text(encoding="ascii").splitlines(), 1):
        header = re.match(r"\s*(?:function|procedure)\s+(\w+)", line)
        if header:
            current = header.group(1)
        if re.search(r"(?<!Suppressible)MsgBox\(", line):
            assert current in allowed, f"line {number}: plain MsgBox in {current}; use SuppressibleMsgBox"
