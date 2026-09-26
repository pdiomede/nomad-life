"""Gmail SMTP mailer used for account confirmation and password reset emails."""
import os
import smtplib
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from flask import current_app

# The HTML emails show the logo from an inline attachment: remote images are often blocked,
# and APP_BASE_URL may be localhost, which the reader's mail app cannot reach.
LOGO_CID = "logo@nomadlife"


def build_message(sender, to, subject, text, html=None, logo_path=None):
    """Plain text email, with an HTML alternative (and the inline logo) when html is given."""
    msg = EmailMessage()
    msg["From"] = f"Nomad Life <{sender}>"
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=sender.rpartition("@")[2] or None)
    msg.set_content(text)
    if html is not None:
        msg.add_alternative(html, subtype="html")
        if logo_path:
            with open(logo_path, "rb") as fh:
                logo = fh.read()
            msg.get_payload()[1].add_related(logo, maintype="image", subtype="png",
                                             cid=f"<{LOGO_CID}>", disposition="inline",
                                             filename="nomad-life-logo.png")
    return msg


def send_email(to, subject, text, html=None):
    """Send an email through Gmail. Returns False only when sending failed.

    When Gmail credentials are not configured the message is printed to the
    console instead, so the app keeps working during local development.
    """
    user = current_app.config.get("GMAIL_USER")
    password = current_app.config.get("GMAIL_APP_PASSWORD")

    if not user or not password:
        print("\n[mailer] Gmail is not configured. Email printed below.")
        print(f"To: {to}\nSubject: {subject}\n\n{text}\n", flush=True)
        return True

    logo_path = os.path.join(current_app.static_folder, "img", "logo-mark.png")
    msg = build_message(user, to, subject, text, html, logo_path)

    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as smtp:
            smtp.starttls()
            smtp.login(user, password)
            smtp.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError) as exc:
        current_app.logger.error("Could not send email: %s", exc)
        return False
