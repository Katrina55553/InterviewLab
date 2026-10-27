"""Provider-independent, TLS-only SMTP delivery for account verification codes."""

import asyncio
import smtplib
import ssl
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from app.core.config import settings

_PURPOSE_NAMES = {"register": "注册账号", "reset": "重置密码", "bind": "绑定邮箱"}


class MailDeliveryError(Exception):
    """A safe delivery error that never contains SMTP responses or credentials."""


def smtp_configured() -> bool:
    return bool(settings.SMTP_HOST and settings.SMTP_USERNAME and settings.SMTP_PASSWORD and settings.SMTP_FROM)


def _send_verification_code(email: str, code: str, purpose: str) -> None:
    message = EmailMessage()
    message["Subject"] = f"知面 · {_PURPOSE_NAMES[purpose]}验证码"
    message["From"] = Address(display_name=settings.SMTP_FROM_NAME, addr_spec=settings.SMTP_FROM)
    message["To"] = Address(addr_spec=email)
    message["Date"] = formatdate(usegmt=True)
    message["Message-ID"] = make_msgid(domain=settings.SMTP_FROM.rsplit("@", 1)[-1])
    message.set_content(
        f"你正在知面{_PURPOSE_NAMES[purpose]}。\n\n"
        f"验证码：{code}\n\n"
        "验证码在 10 分钟内有效，使用后失效。请勿将验证码告诉他人。\n"
        "如果这不是你本人操作，请忽略此邮件。\n"
    )
    context = ssl.create_default_context()
    if settings.SMTP_SECURITY == "ssl":
        connection = smtplib.SMTP_SSL(
            settings.SMTP_HOST, settings.SMTP_PORT, timeout=settings.SMTP_TIMEOUT_SECONDS, context=context
        )
    else:
        connection = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=settings.SMTP_TIMEOUT_SECONDS)
    with connection as client:
        if settings.SMTP_SECURITY == "starttls":
            client.ehlo()
            client.starttls(context=context)
            client.ehlo()
        client.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
        client.send_message(message, from_addr=settings.SMTP_FROM, to_addrs=[email])


async def send_verification_code(email: str, code: str, purpose: str) -> None:
    """Deliver once; the caller owns code persistence, throttling, and retry policy."""
    if not smtp_configured():
        raise MailDeliveryError("邮件服务尚未配置，请联系管理员")
    if purpose not in _PURPOSE_NAMES:
        raise ValueError("不支持的邮件验证码用途")
    try:
        await asyncio.to_thread(_send_verification_code, email, code, purpose)
    except Exception:
        # SMTP errors can echo authentication data, addresses, or message content.
        raise MailDeliveryError("邮件发送失败，请稍后重试或联系管理员") from None
