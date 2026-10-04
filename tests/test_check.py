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
