import pytest

from pwi.mailer import Mailer, MailNotConfigured

VALID = "abcdefghijklmnop"  # 16 characters, as Google issues them


def env(monkeypatch, password: str, address: str = "me@gmail.com", notify: str = "") -> None:
    monkeypatch.setenv("GMAIL_ADDRESS", address)
    monkeypatch.setenv("GMAIL_APP_PASSWORD", password)
    monkeypatch.setenv("NOTIFY_TO", notify)


def test_spaces_are_stripped(monkeypatch) -> None:
    # Google displays the password in groups of four and people paste it that way.
    env(monkeypatch, "abcd efgh ijkl mnop")

    assert Mailer.from_env()._password == VALID


def test_surrounding_quotes_are_stripped(monkeypatch) -> None:
    # A .env value is often quoted precisely because it contains spaces.
    env(monkeypatch, '"abcd efgh ijkl mnop"')

    assert Mailer.from_env()._password == VALID


def test_a_stray_character_is_rejected_loudly(monkeypatch) -> None:
    # A typo like GMAIL_APP_PASSWORD=="..." leaves an extra character in the value. Better
    # to fail at startup with a clear message than to get an opaque SMTP auth error.
    env(monkeypatch, '="abcd efgh ijkl mnop"')

    with pytest.raises(MailNotConfigured, match="16 characters"):
        Mailer.from_env()


def test_missing_configuration_is_rejected(monkeypatch) -> None:
    env(monkeypatch, "", address="")

    with pytest.raises(MailNotConfigured, match="must both be set"):
        Mailer.from_env()


def test_recipient_defaults_to_the_sending_account(monkeypatch) -> None:
    env(monkeypatch, VALID)

    assert Mailer.from_env()._recipient == "me@gmail.com"


def test_explicit_recipient_wins(monkeypatch) -> None:
    env(monkeypatch, VALID, notify="other@example.com")

    assert Mailer.from_env()._recipient == "other@example.com"
