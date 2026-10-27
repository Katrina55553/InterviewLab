"""SMTP transport uses TLS and never exposes delivery secrets in errors."""

import smtplib
import ssl

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.platform import mailer


@pytest.fixture
def smtp(monkeypatch):
    for key, value in {
        "SMTP_HOST": "smtp.example.test",
        "SMTP_PORT": 465,
        "SMTP_SECURITY": "ssl",
        "SMTP_USERNAME": "sender@example.test",
        "SMTP_PASSWORD": "secret-authorization-code",
        "SMTP_FROM": "sender@example.test",
        "SMTP_FROM_NAME": "知面",
        "SMTP_TIMEOUT_SECONDS": 8,
    }.items():
        monkeypatch.setattr(mailer.settings, key, value)
    calls = []

    class SMTP:
        def __init__(self, *args, **kwargs):
            calls.append(("connect", args, kwargs))

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            calls.append(("close",))

        def ehlo(self):
            calls.append(("ehlo",))

        def starttls(self, **kwargs):
            calls.append(("starttls", kwargs))

        def login(self, *args):
            calls.append(("login", args))

        def send_message(self, message, **kwargs):
            calls.append(("send", message, kwargs))

    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", SMTP)
    monkeypatch.setattr(mailer.smtplib, "SMTP", SMTP)
    return calls


@pytest.mark.asyncio
async def test_ssl_delivery_verifies_certificate_and_sets_explicit_envelope(smtp, monkeypatch):
    def forbid_plaintext(*_args, **_kwargs):
        raise AssertionError("Implicit TLS must never use a plaintext SMTP connection")

    monkeypatch.setattr(mailer.smtplib, "SMTP", forbid_plaintext)
    await mailer.send_verification_code("recipient@example.test", "123456", "register")
    assert [call[0] for call in smtp] == ["connect", "login", "send", "close"]
    _, args, options = smtp[0]
    assert args == ("smtp.example.test", 465)
    assert options["timeout"] == 8
    assert options["context"].verify_mode == ssl.CERT_REQUIRED
    assert options["context"].check_hostname is True
    _, message, envelope = smtp[2]
    assert message["To"].addresses[0].addr_spec == "recipient@example.test"
    assert message["From"].addresses[0].addr_spec == "sender@example.test"
    assert message["From"].addresses[0].display_name == "知面"
    assert "注册账号" in message["Subject"]
    assert "123456" in message.get_content() and "10 分钟" in message.get_content()
    assert envelope == {"from_addr": "sender@example.test", "to_addrs": ["recipient@example.test"]}
    assert "secret-authorization-code" not in message.as_string()


@pytest.mark.asyncio
async def test_starttls_completes_tls_before_authenticating(smtp, monkeypatch):
    monkeypatch.setattr(mailer.settings, "SMTP_SECURITY", "starttls")
    monkeypatch.setattr(mailer.settings, "SMTP_PORT", 587)
    await mailer.send_verification_code("recipient@example.test", "654321", "reset")
    assert [call[0] for call in smtp] == ["connect", "ehlo", "starttls", "ehlo", "login", "send", "close"]
    assert smtp[0][1] == ("smtp.example.test", 587)
    assert smtp[2][1]["context"].verify_mode == ssl.CERT_REQUIRED
    assert "重置密码" in smtp[5][1]["Subject"]


@pytest.mark.asyncio
async def test_starttls_failure_never_sends_credentials_or_retries_plaintext(smtp, monkeypatch):
    monkeypatch.setattr(mailer.settings, "SMTP_SECURITY", "starttls")

    class FailedTLS:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def ehlo(self):
            pass

        def starttls(self, **_kwargs):
            raise smtplib.SMTPNotSupportedError("server echo: secret-authorization-code recipient@example.test 123456")

        def login(self, *_args):
            pytest.fail("Credentials must not be sent after TLS fails")

    monkeypatch.setattr(mailer.smtplib, "SMTP", FailedTLS)
    with pytest.raises(mailer.MailDeliveryError) as error:
        await mailer.send_verification_code("recipient@example.test", "123456", "bind")
    assert str(error.value) == "邮件发送失败，请稍后重试或联系管理员"
    assert error.value.__suppress_context__
    assert not smtp


@pytest.mark.asyncio
async def test_missing_credentials_disable_mail_without_attempting_connection(smtp, monkeypatch):
    monkeypatch.setattr(mailer.settings, "SMTP_PASSWORD", "")
    assert not mailer.smtp_configured()
    with pytest.raises(mailer.MailDeliveryError, match="尚未配置"):
        await mailer.send_verification_code("recipient@example.test", "123456", "register")
    assert not smtp


@pytest.mark.parametrize("name", ["SMTP_FROM", "SMTP_FROM_NAME", "SMTP_USERNAME", "SMTP_HOST"])
def test_smtp_settings_reject_header_control_characters(name):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{name: "sender@example.test\r\nBcc: other@example.test"})


@pytest.mark.parametrize("address", ["not-an-email", "a@example.test,b@example.test", "Name <a@example.test>"])
def test_smtp_from_requires_single_plain_email_address(address):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, SMTP_FROM=address)


def test_smtp_settings_require_tls_and_hide_password_from_repr():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, SMTP_SECURITY="none")
    config = Settings(_env_file=None, SMTP_PASSWORD="never-print-this")
    assert "never-print-this" not in repr(config)
