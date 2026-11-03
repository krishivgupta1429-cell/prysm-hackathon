"""
app/services/email_service.py
Email sending via Brevo (formerly Sendinblue) — verification and password reset flows.
"""

import httpx
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("email")

BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"


async def _send_email(to_email: str, subject: str, html: str) -> bool:
    """Send an email via Brevo transactional API."""
    payload = {
        "sender": {
            "name": settings.BREVO_FROM_NAME,
            "email": settings.BREVO_FROM_EMAIL,
        },
        "to": [{"email": to_email}],
        "subject": subject,
        "htmlContent": html,
    }
    headers = {
        "api-key": settings.BREVO_API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(BREVO_API_URL, json=payload, headers=headers)
        if resp.status_code in (200, 201):
            logger.info("Email sent via Brevo", extra={"x_email": to_email, "x_subject": subject})
            return True
        else:
            logger.error("Brevo API error", extra={
                "x_email": to_email, "x_status": resp.status_code, "x_body": resp.text,
            })
            return False
    except Exception as exc:
        logger.error("Failed to send email", extra={
            "x_email": to_email, "error": str(exc),
        })
        return False


def _base_template(content: str, preheader: str = "") -> str:
    """Wrap content in Prysm-branded email shell — warm, premium aesthetic."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="color-scheme" content="light dark">
  <title>Prysm</title>
</head>
<body style="margin:0;padding:0;background:#f5f0eb;font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;-webkit-font-smoothing:antialiased;">
  <!-- Preheader text (hidden) -->
  <div style="display:none;font-size:1px;color:#f5f0eb;line-height:1px;max-height:0;max-width:0;opacity:0;overflow:hidden;">
    {preheader}
  </div>

  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f5f0eb;">
  <tr><td align="center" style="padding:40px 20px 60px;">

    <!-- Container -->
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:480px;">

      <!-- Logo -->
      <tr><td style="padding:0 0 32px;text-align:center;">
        <span style="font-size:22px;font-weight:800;color:#1a1714;letter-spacing:-0.04em;text-transform:uppercase;">Prysm</span>
      </td></tr>

      <!-- Main card -->
      <tr><td style="background:#ffffff;border-radius:20px;padding:0;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,0.04);">
        <!-- Top accent bar -->
        <div style="height:3px;background:linear-gradient(90deg,#1a1714 0%,#3d3529 50%,#1a1714 100%);"></div>

        <!-- Card content -->
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
        <tr><td style="padding:44px 40px 48px;">
          {content}
        </td></tr>
        </table>
      </td></tr>

      <!-- Footer -->
      <tr><td style="padding:32px 0 0;text-align:center;">
        <p style="color:#9c8e7c;font-size:12px;margin:0;font-weight:500;">Prysm — AI Content Intelligence</p>
        <p style="color:#b8ad9e;font-size:11px;margin:10px 0 0;line-height:1.5;">
          You received this because you have an account at
          <a href="https://theprysm.io" style="color:#9c8e7c;text-decoration:none;">theprysm.io</a>
        </p>
        <table role="presentation" cellpadding="0" cellspacing="0" style="margin:16px auto 0;">
        <tr>
          <td style="padding:0 6px;">
            <a href="https://x.com/theprysm7" style="color:#b8ad9e;text-decoration:none;font-size:11px;">X</a>
          </td>
          <td style="color:#d4cdc4;font-size:11px;">·</td>
          <td style="padding:0 6px;">
            <a href="https://www.instagram.com/theprysm7/" style="color:#b8ad9e;text-decoration:none;font-size:11px;">Instagram</a>
          </td>
          <td style="color:#d4cdc4;font-size:11px;">·</td>
          <td style="padding:0 6px;">
            <a href="https://www.linkedin.com/company/the-prysm/" style="color:#b8ad9e;text-decoration:none;font-size:11px;">LinkedIn</a>
          </td>
        </tr>
        </table>
      </td></tr>

    </table>
  </td></tr>
  </table>
</body>
</html>"""


def _verification_template(verify_url: str) -> str:
    content = f"""
          <!-- Icon -->
          <table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 0 24px;">
          <tr><td style="width:48px;height:48px;background:#f5f0eb;border-radius:14px;text-align:center;vertical-align:middle;">
            <span style="font-size:22px;line-height:48px;">&#9993;</span>
          </td></tr>
          </table>

          <h1 style="color:#1a1714;font-size:24px;font-weight:700;margin:0 0 8px;letter-spacing:-0.03em;">
            Verify your email
          </h1>
          <p style="color:#6b5e50;font-size:15px;line-height:1.65;margin:0 0 32px;">
            Welcome to Prysm. Click below to verify your email address and start analysing your content.
          </p>

          <!-- CTA Button -->
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
          <tr><td align="center">
            <a href="{verify_url}" target="_blank"
               style="display:inline-block;background:#1a1714;color:#ffffff;font-size:14px;font-weight:600;
                      padding:15px 44px;border-radius:12px;text-decoration:none;letter-spacing:-0.01em;">
              Verify Email &rarr;
            </a>
          </td></tr>
          </table>

          <!-- Divider -->
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:32px 0 0;">
          <tr><td style="border-top:1px solid #eee8e0;padding-top:20px;">
            <p style="color:#b8ad9e;font-size:12px;margin:0;line-height:1.5;">
              Or copy this link into your browser:
            </p>
            <p style="margin:6px 0 0;">
              <a href="{verify_url}" style="color:#9c8e7c;font-size:12px;word-break:break-all;text-decoration:underline;">{verify_url}</a>
            </p>
            <p style="color:#ccc3b8;font-size:11px;margin:16px 0 0;">This link expires in 24 hours.</p>
          </td></tr>
          </table>
    """
    return _base_template(content, preheader="Verify your email to activate your Prysm account")


def _reset_template(reset_url: str) -> str:
    content = f"""
          <!-- Icon -->
          <table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 0 24px;">
          <tr><td style="width:48px;height:48px;background:#f5f0eb;border-radius:14px;text-align:center;vertical-align:middle;">
            <span style="font-size:22px;line-height:48px;">&#128274;</span>
          </td></tr>
          </table>

          <h1 style="color:#1a1714;font-size:24px;font-weight:700;margin:0 0 8px;letter-spacing:-0.03em;">
            Reset your password
          </h1>
          <p style="color:#6b5e50;font-size:15px;line-height:1.65;margin:0 0 32px;">
            You requested a password reset for your Prysm account. Click below to choose a new password.
          </p>

          <!-- CTA Button -->
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
          <tr><td align="center">
            <a href="{reset_url}" target="_blank"
               style="display:inline-block;background:#1a1714;color:#ffffff;font-size:14px;font-weight:600;
                      padding:15px 44px;border-radius:12px;text-decoration:none;letter-spacing:-0.01em;">
              Reset Password &rarr;
            </a>
          </td></tr>
          </table>

          <!-- Divider -->
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:32px 0 0;">
          <tr><td style="border-top:1px solid #eee8e0;padding-top:20px;">
            <p style="color:#b8ad9e;font-size:12px;margin:0;line-height:1.5;">
              Or copy this link into your browser:
            </p>
            <p style="margin:6px 0 0;">
              <a href="{reset_url}" style="color:#9c8e7c;font-size:12px;word-break:break-all;text-decoration:underline;">{reset_url}</a>
            </p>
            <p style="color:#ccc3b8;font-size:11px;margin:16px 0 0;">This link expires in 1 hour.</p>
            <p style="color:#ccc3b8;font-size:11px;margin:6px 0 0;">If you didn't request this, you can safely ignore this email.</p>
          </td></tr>
          </table>
    """
    return _base_template(content, preheader="Reset your Prysm account password")


async def send_verification_email(email: str, token: str) -> bool:
    """Send email verification link. Returns True on success."""
    verify_url = f"{settings.FRONTEND_URL}/verify-email?token={token}"
    return await _send_email(email, "Verify your Prysm account", _verification_template(verify_url))


async def send_password_reset_email(email: str, token: str) -> bool:
    """Send password reset link. Returns True on success."""
    reset_url = f"{settings.FRONTEND_URL}/reset-password?token={token}"
    return await _send_email(email, "Reset your Prysm password", _reset_template(reset_url))
