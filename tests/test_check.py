from twitchAPI.type import TwitchAuthorizationException

from twitchbot import check
from twitchbot.check import CheckResult, check_twitch, print_results


async def test_twitch_failure_never_leaks_secret(monkeypatch):
    async def fake_twitch(app_id, secret):
        raise TwitchAuthorizationException(f"https://id.twitch.tv/oauth2/token?client_secret={secret}")

    monkeypatch.setattr(check, "Twitch", fake_twitch)
    result = await check_twitch("id", "SUPERSECRET")
    assert not result.ok
    assert "SUPERSECRET" not in result.detail


async def test_twitch_network_error(monkeypatch):
    async def fake_twitch(app_id, secret):
        raise OSError(f"boom {secret}")

    monkeypatch.setattr(check, "Twitch", fake_twitch)
    result = await check_twitch("id", "SUPERSECRET")
    assert not result.ok and "SUPERSECRET" not in result.detail


def test_print_results(capsys):
    assert print_results([CheckResult("a", True, "fine")])
    assert not print_results([CheckResult("a", True, "x"), CheckResult("b", False, "bad")])
    assert "[FAIL] b: bad" in capsys.readouterr().out


def test_members_intent_flags():
    from twitchbot.check import members_intent_result

    assert members_intent_result(1 << 14).ok
    assert members_intent_result(1 << 15).ok  # "limited" (unverified bots under 100 servers)
    result = members_intent_result(1 << 23)
    # Off is a warning, not a failure: the bot starts without it and DMs server owners only.
    assert result.ok and result.warning and "Privileged Gateway Intents" in result.detail
    assert "UPDATE_NOTIFY=owner" in result.detail


def test_print_results_warning_does_not_fail(capsys):
    assert print_results([CheckResult("a", True, "fine"), CheckResult("b", True, "hmm", warning=True)])
    assert "[WARN] b: hmm" in capsys.readouterr().out


async def test_run_checks_only_checks_the_intent_in_admins_mode(monkeypatch, tmp_path):
    from twitchbot.config import Config

    async def ok(*args):
        return CheckResult("x", True, "ok")

    async def intent(token):
        return check.members_intent_result(0)

    monkeypatch.setattr(check, "check_discord", ok)
    monkeypatch.setattr(check, "check_twitch", ok)
    monkeypatch.setattr(check, "check_slash_commands", ok)
    monkeypatch.setattr(check, "check_members_intent", intent)
    for mode, expected in (("admins", True), ("owner", False), ("off", False)):
        config = Config("t", "i", "s", tmp_path, tmp_path, "INFO", 60, None, None, tmp_path / ".env", {}, mode)
        results = await check.run_checks(config)
        assert any(r.warning for r in results) is expected
        assert print_results(results)
