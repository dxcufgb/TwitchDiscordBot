"""The Linux installer's command line: the options shared by every dxcufgb installer come first in --help."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = ROOT / "packaging" / "linux" / "install.sh"
AUTO_UPDATE_SH = ROOT / "packaging" / "linux" / "auto-update.sh"

COMMON = [
    "(no option)",
    "--yes, -y",
    "--auto-update",
    "--no-auto-update",
    "--enable-auto-update",
    "--disable-auto-update",
    "--check-update",
    "--update-now",
    "--uninstall [--purge]",
    "--help, -h",
]
# Options that already worked before the common set and must keep working.
SPECIFIC = ["--reconfigure", "--prefix DIR", "--user NAME", "--no-service", "--skip-check"]


def help_text() -> str:
    result = subprocess.run(["bash", str(INSTALL_SH), "--help"], capture_output=True, text=True, check=True)
    return result.stdout


def test_help_lists_the_common_options_first():
    options = [m.group(1) for m in re.finditer(r"^  (\(no option\)|-\S+(?: \S+)?(?:, -\w)?)", help_text(), re.M)]
    options = [o.strip() for o in options]
    assert options[: len(COMMON)] == COMMON
    assert options[len(COMMON) :] == SPECIFIC


def test_every_documented_option_is_parsed():
    text = INSTALL_SH.read_text()
    for option in ["--yes|-y", "--auto-update", "--no-auto-update", "--enable-auto-update", "--disable-auto-update",
                   "--check-update", "--update-now", "--uninstall", "--purge", "-h|--help", "--reconfigure",
                   "--prefix", "--user", "--no-service", "--skip-check"]:  # fmt: skip
        assert re.search(rf"^\s+{re.escape(option)}\)", text, re.M), option


def test_unknown_option_fails():
    result = subprocess.run(["bash", str(INSTALL_SH), "--bogus"], capture_output=True, text=True)
    assert result.returncode == 2 and "Unknown option" in result.stderr


def test_tokens_never_come_from_arguments():
    # The token is read from the environment or a hidden prompt only, and not exported to child processes.
    text = INSTALL_SH.read_text()
    assert not re.search(r"--\w*token", text, re.I)
    assert "export -n UPDATE_GITHUB_TOKEN INSTALL_GITHUB_TOKEN" in text
    updater = AUTO_UPDATE_SH.read_text()
    assert "-u UPDATE_GITHUB_TOKEN" in updater and "-u INSTALL_GITHUB_TOKEN" in updater


def test_check_update_exit_codes():
    text = AUTO_UPDATE_SH.read_text()
    block = text[text.index("if [[ $MODE == check-update ]]") :]
    block = block[: block.index("esac")]
    assert "0) return 10" in block and "3) return 0" in block and "*) return 1" in block
