from email.message import EmailMessage

import aiosmtplib

from app.core.config import settings
from app.core.logging import logger

# ---------------------------------------------------------------------------
# Brand palette — kept in sync with the frontend's tailwind theme so the
# emails feel like a continuation of the product.
# ---------------------------------------------------------------------------
_PRIMARY = "#2545e6"
_PRIMARY_DARK = "#1d34c6"
_ACCENT = "#c026d3"
_ACCENT_DARK = "#a21caf"
_INK = "#0f172a"
_INK_MUTED = "#475569"
_INK_SUBTLE = "#94a3b8"
_LINE = "#e2e8f0"
_SURFACE = "#ffffff"
_SURFACE_MUTED = "#f8fafc"
_BRAND_NAME = "Smart Bot"


async def _send(to: str, subject: str, html: str, text: str | None = None) -> None:
    if not settings.smtp_user or not settings.smtp_password:
        # Dev convenience: log what we would have sent rather than failing.
        logger.info("email.skipped", to=to, subject=subject, reason="smtp_not_configured")
        return

    msg = EmailMessage()
    msg["From"] = settings.email_from or settings.smtp_user
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text or _strip_html(html))
    msg.add_alternative(html, subtype="html")

    try:
        await aiosmtplib.send(
            msg,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_user,
            password=settings.smtp_password,
            start_tls=settings.smtp_tls,
        )
        logger.info("email.sent", to=to, subject=subject)
    except Exception as exc:
        logger.exception("email.failed", to=to, subject=subject, error=str(exc))
        return


def _strip_html(html: str) -> str:
    import re

    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Layout helpers — we use table-based layout because that's still the only
# way to get reliable rendering in Outlook & Gmail. CSS is inlined for the
# same reason.
# ---------------------------------------------------------------------------
def _layout(*, preheader: str, headline: str, body_html: str) -> str:
    """Wraps inner body HTML in a polished, branded email shell."""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta name="x-apple-disable-message-reformatting" />
  <meta name="color-scheme" content="light" />
  <meta name="supported-color-schemes" content="light" />
  <title>{headline}</title>
  <!--[if mso]>
  <style type="text/css">
    body, table, td {{ font-family: 'Segoe UI', Arial, sans-serif !important; }}
  </style>
  <![endif]-->
</head>
<body style="margin:0;padding:0;background:{_SURFACE_MUTED};-webkit-font-smoothing:antialiased;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Inter,Helvetica,Arial,sans-serif;color:{_INK};">
  <!-- Preheader: shown in inbox preview but hidden in the email body. -->
  <div style="display:none;max-height:0;overflow:hidden;mso-hide:all;font-size:1px;line-height:1px;color:{_SURFACE_MUTED};opacity:0;">
    {preheader}
  </div>

  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{_SURFACE_MUTED};padding:32px 16px;">
    <tr>
      <td align="center">
        <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;width:100%;">

          <!-- Brand bar -->
          <tr>
            <td align="center" style="padding-bottom:20px;">
              <table role="presentation" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td style="background:linear-gradient(135deg,{_PRIMARY} 0%,{_ACCENT} 100%);width:40px;height:40px;border-radius:12px;text-align:center;vertical-align:middle;color:#ffffff;font-weight:700;font-size:18px;line-height:40px;letter-spacing:-0.5px;">
                    ✦
                  </td>
                  <td style="padding-left:12px;font-size:18px;font-weight:700;color:{_INK};letter-spacing:-0.3px;">
                    {_BRAND_NAME}
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Card -->
          <tr>
            <td style="background:{_SURFACE};border:1px solid {_LINE};border-radius:20px;overflow:hidden;box-shadow:0 1px 2px rgba(15,23,42,0.04),0 8px 24px -12px rgba(15,23,42,0.08);">

              <!-- Gradient header strip -->
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td style="height:6px;background:linear-gradient(90deg,{_PRIMARY} 0%,{_ACCENT} 100%);font-size:0;line-height:0;">&nbsp;</td>
                </tr>
              </table>

              <!-- Body -->
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td style="padding:40px 40px 36px 40px;">
                    <h1 style="margin:0 0 16px 0;font-size:26px;font-weight:700;line-height:1.25;color:{_INK};letter-spacing:-0.4px;">
                      {headline}
                    </h1>
                    {body_html}
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="padding:28px 8px 0 8px;text-align:center;font-size:12px;line-height:18px;color:{_INK_SUBTLE};">
              You're receiving this because someone used your address on {_BRAND_NAME}.<br />
              If that wasn't you, you can safely ignore this message.
              <div style="margin-top:14px;color:{_INK_SUBTLE};">
                © {_BRAND_NAME} — crafted for developers.
              </div>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def _otp_block(code: str, *, label: str = "Your code") -> str:
    """A bold, monospaced one-time-code display in a soft framed card."""
    return f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:8px 0 8px 0;">
      <tr>
        <td align="center" style="padding:6px 0;">
          <table role="presentation" cellpadding="0" cellspacing="0" border="0">
            <tr>
              <td style="
                  padding:22px 32px;
                  background:linear-gradient(180deg,{_SURFACE_MUTED} 0%,#ffffff 100%);
                  border:1px solid {_LINE};
                  border-radius:16px;
                  text-align:center;
                ">
                <div style="font-size:11px;font-weight:600;letter-spacing:1.4px;text-transform:uppercase;color:{_INK_SUBTLE};margin-bottom:10px;">
                  {label}
                </div>
                <div style="
                    font-family:'JetBrains Mono',ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
                    font-size:34px;
                    font-weight:700;
                    color:{_PRIMARY_DARK};
                    letter-spacing:12px;
                    padding-left:12px;
                  ">
                  {code}
                </div>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
    """


def _expiry_note(minutes: int = 10) -> str:
    return f"""
    <p style="margin:18px 0 0 0;font-size:13px;line-height:20px;color:{_INK_SUBTLE};text-align:center;">
      ⏱ This code expires in {minutes} minutes.
    </p>
    """


def _paragraph(text: str) -> str:
    return (
        f'<p style="margin:0 0 14px 0;font-size:15px;line-height:24px;color:{_INK_MUTED};">'
        f"{text}</p>"
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
async def send_verification_email(to: str, code: str) -> None:
    body = (
        _paragraph(
            "Welcome aboard! To finish setting up your account, enter the code below "
            f"in the {_BRAND_NAME} app."
        )
        + _otp_block(code, label="Verification code")
        + _expiry_note(10)
        + _paragraph(
            "Didn't sign up? You can ignore this email — no account will be created without "
            "this code."
        )
    )
    html = _layout(
        preheader=f"Your {_BRAND_NAME} verification code is {code}.",
        headline="Confirm your email",
        body_html=body,
    )
    await _send(to, f"Verify your email — {_BRAND_NAME}", html)


async def send_password_reset_email(to: str, code: str) -> None:
    body = (
        _paragraph(
            "We received a request to reset the password on your account. "
            "Use the code below to choose a new one."
        )
        + _otp_block(code, label="Reset code")
        + _expiry_note(10)
        + _paragraph(
            "If you didn't request this, you can safely ignore this email — your "
            "password won't change."
        )
    )
    html = _layout(
        preheader=f"Your {_BRAND_NAME} password reset code is {code}.",
        headline="Reset your password",
        body_html=body,
    )
    await _send(to, f"Reset your password — {_BRAND_NAME}", html)


async def send_welcome_email(to: str, username: str) -> None:
    body = (
        _paragraph(
            f"Hey <strong style=\"color:{_INK};\">{username}</strong> — your account is "
            f"verified and ready. Welcome to {_BRAND_NAME}."
        )
        + _paragraph(
            "You can now create projects, browse and search your code, and chat with "
            "the assistant to ship FastAPI services faster."
        )
        + f"""
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:18px auto 4px auto;">
          <tr>
            <td style="
                background:linear-gradient(135deg,{_PRIMARY} 0%,{_ACCENT} 100%);
                border-radius:12px;
                padding:0;
              ">
              <a href="http://localhost:3000/dashboard" target="_blank"
                 style="
                   display:inline-block;
                   padding:14px 28px;
                   font-size:15px;
                   font-weight:600;
                   color:#ffffff;
                   text-decoration:none;
                   letter-spacing:-0.2px;
                 ">
                Open your workspace →
              </a>
            </td>
          </tr>
        </table>
        """
        + _paragraph(
            f"<span style=\"color:{_INK_SUBTLE};font-size:13px;\">Tip: bookmark the "
            "dashboard so you can jump back in with one click.</span>"
        )
    )
    html = _layout(
        preheader=f"Welcome to {_BRAND_NAME}, {username}.",
        headline=f"You're all set, {username} 🎉",
        body_html=body,
    )
    await _send(to, f"Welcome to {_BRAND_NAME}", html)
