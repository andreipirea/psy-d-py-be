import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

load_dotenv()

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_EMAIL = os.getenv("SMTP_EMAIL")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
CONTACT_EMAIL = os.getenv("CONTACT_EMAIL")


def send_contact_email(name: str, sender_email: str, phone: str, message: str) -> None:
    """Send a contact form email via Gmail SMTP.

    Raises RuntimeError on failure so the route can return a clear error.
    """
    if not SMTP_EMAIL or not SMTP_PASSWORD or not CONTACT_EMAIL:
        raise RuntimeError(
            "Email is not configured. Set SMTP_EMAIL, SMTP_PASSWORD, "
            "and CONTACT_EMAIL in your .env file."
        )

    msg = MIMEMultipart("alternative")
    msg["From"] = f"{name} via Contact Form <{SMTP_EMAIL}>"
    msg["To"] = CONTACT_EMAIL
    msg["Reply-To"] = sender_email
    msg["Subject"] = f"New contact from {name}"

    html = f"""\
<html>
<body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
  <h2 style="color: #2c3e50;">New Contact Form Message</h2>
  <table style="border-collapse: collapse; width: 100%; max-width: 600px;">
    <tr>
      <td style="padding: 8px 12px; font-weight: bold; border-bottom: 1px solid #ddd;">Name</td>
      <td style="padding: 8px 12px; border-bottom: 1px solid #ddd;">{name}</td>
    </tr>
    <tr>
      <td style="padding: 8px 12px; font-weight: bold; border-bottom: 1px solid #ddd;">Email</td>
      <td style="padding: 8px 12px; border-bottom: 1px solid #ddd;">{sender_email}</td>
    </tr>
    <tr>
      <td style="padding: 8px 12px; font-weight: bold; border-bottom: 1px solid #ddd;">Phone</td>
      <td style="padding: 8px 12px; border-bottom: 1px solid #ddd;">{phone}</td>
    </tr>
    <tr>
      <td style="padding: 8px 12px; font-weight: bold; vertical-align: top;">Message</td>
      <td style="padding: 8px 12px; white-space: pre-wrap;">{message}</td>
    </tr>
  </table>
</body>
</html>
"""

    text_part = MIMEText(
        f"Name: {name}\nEmail: {sender_email}\nPhone: {phone}\n\nMessage:\n{message}",
        "plain",
    )
    html_part = MIMEText(html, "html")
    msg.attach(text_part)
    msg.attach(html_part)

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(SMTP_EMAIL, SMTP_PASSWORD)
            server.sendmail(SMTP_EMAIL, CONTACT_EMAIL, msg.as_string())
    except Exception as exc:
        raise RuntimeError(f"Failed to send email: {exc}") from exc
