"""Gmail SMTP mailer used for password reset emails."""
import smtplib
from email.message import EmailMessage

from flask import current_app


def send_email(to, subject, body):
    """Send an email through Gmail. Returns True when sent.

    When Gmail credentials are not configured the message is printed to the
    console instead, so the app keeps working during local development.
    """
    user = current_app.config.get("GMAIL_USER")
    password = current_app.config.get("GMAIL_APP_PASSWORD")

    if not user or not password:
        print("\n[mailer] Gmail is not configured. Email printed below.")
        print(f"To: {to}\nSubject: {subject}\n\n{body}\n", flush=True)
        return False

    msg = EmailMessage()
    msg["From"] = f"Nomad Life <{user}>"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as smtp:
            smtp.starttls()
            smtp.login(user, password)
            smtp.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError) as exc:
        current_app.logger.error("Could not send email: %s", exc)
        return False
