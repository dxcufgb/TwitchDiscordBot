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


WINDOWS = ISS.parent
INSTALL_PS1 = WINDOWS / "install.ps1"
AUTO_UPDATE_PS1 = WINDOWS / "auto-update.ps1"


def test_auto_update_task_is_opt_in():
    text = ISS.read_text(encoding="ascii")
    task = re.search(r'^Name: "autoupdate";.*$', text, re.M)
    assert task and "Flags: unchecked" in task.group(0)
    assert re.search(r"^UsePreviousTasks=no", text, re.M)
    assert 'Source: "auto-update.ps1"' in text


def test_update_token_goes_through_the_environment():
    # The token must never be on a command line (visible in process lists and logs).
    text = ISS.read_text(encoding="ascii")
    assert "SetEnvironmentVariable('UPDATE_GITHUB_TOKEN'" in text
    assert "UnsetEnvironmentVariable('UPDATE_GITHUB_TOKEN'" in text
    for line in text.splitlines():
        if "Exec(" in line or "RunInstallScript(" in line:
            assert "TokenPage" not in line and "UPDATE_GITHUB_TOKEN" not in line, line
    # install.ps1 reads it from the environment only (no -Token parameter).
    install = INSTALL_PS1.read_text(encoding="ascii")
    assert "$token = $env:UPDATE_GITHUB_TOKEN" in install
    assert not re.search(r"\[string\]\$\w*Token", install)


def test_updater_folder_is_admin_and_system_only():
    text = INSTALL_PS1.read_text(encoding="ascii")
    grants = re.findall(r"icacls @\(\$(UpdaterDir|TokenFile), '/inheritance:r', '/grant:r', (.*)'/Q'\)", text)
    assert {target for target, _ in grants} == {"UpdaterDir", "TokenFile"}
    for _, args in grants:
        sids = set(re.findall(r"\$\{(Sid\w+)\}", args))
        assert sids == {"SidAdmins", "SidSystem"}, args


def test_update_task_runs_as_system_and_is_removed_on_uninstall():
    text = INSTALL_PS1.read_text(encoding="ascii")
    assert "New-ScheduledTaskPrincipal -UserId (Get-AccountName $SidSystem)" in text
    assert text.count("Unregister-ScheduledTask -TaskName $UpdateTaskName") >= 3  # -RemoveService, -Uninstall, disable
    assert '"{app}\\updater"' in ISS.read_text(encoding="ascii")


def test_powershell_scripts_are_ascii_crlf():
    # Windows PowerShell 5.1 reads BOM-less scripts in the ANSI code page.
    for script in (INSTALL_PS1, AUTO_UPDATE_PS1):
        data = script.read_bytes()
        data.decode("ascii")
        assert data.count(b"\n") == data.count(b"\r\n"), script.name
