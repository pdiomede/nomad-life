"""SMTP mailer (Gmail by default) used for account confirmation and password reset emails."""
import logging
import logging.handlers
import os
import re
import smtplib
import socket
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from flask import current_app

# The HTML emails show the logo from an inline attachment: remote images are often blocked,
# and APP_BASE_URL may be localhost, which the reader's mail app cannot reach.
LOGO_CID = "logo@nomadlife"

# Everything about sending email goes to this logger: to the terminal or journal like the rest
# of the app's log, and to a file (data/nomadlife.log, see setup_logging) that keeps the lines
# of the last days, so "why was the email not sent" can be read afterwards. Never a password.
log = logging.getLogger("nomadlife.mail")
LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"
LOG_FILE_BYTES = 1_000_000
LOG_FILE_KEEP = 5


# Personal data stays out of the log: an address is cut to its domain, and the signed links
# that grant access (reset, confirmation, email change and its undo) to "<token>". Flask logs
# the path of a request that fails ("Exception on /reset/<token> [POST]"), and servers
# answer with the address in some refusals ("<ada@example.com>: Recipient address rejected").
EMAIL_RE = re.compile(r"[\w.+\-']+@([\w\-]+(?:\.[\w\-]+)+)")
TOKEN_RE = re.compile(r"(/(?:reset|verify|account/email/undo|account/email)/)[^\s\"'?#]+")


def redact_emails(text):
    """text with every address replaced by @domain."""
    return EMAIL_RE.sub(lambda m: "@" + m.group(1), text)


def mail_domain(address):
    """"@domain" of an address, for a log line."""
    return "@" + address.rpartition("@")[2] if "@" in (address or "") else "(no address)"


class RedactTokens(logging.Filter):
    """Replaces the signed links in a log record, wherever they appear in its message."""

    def filter(self, record):
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - a bad format string is logged as it is
            return True
        clean = TOKEN_RE.sub(r"\1<token>", message)
        if clean != message:
            record.msg, record.args = clean, ()
        return True


def setup_logging(app):
    """Send the app's own log (the mail lines at INFO, everything else from WARNING up) to a
    rotating file too: LOG_FILE in config.env, data/nomadlife.log next to the database by
    default, empty to turn the file off. Safe to call again (tests make many apps): earlier
    handlers of ours are closed and replaced."""
    for logger in (log, app.logger):
        for h in list(logger.handlers):
            if getattr(h, "nomadlife", False):
                logger.removeHandler(h)
                h.close()
        for f in list(logger.filters):
            if isinstance(f, RedactTokens):
                logger.removeFilter(f)
        logger.addFilter(RedactTokens())
    log.setLevel(logging.INFO)
    log.propagate = False  # not through the app logger: no line twice
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("[mail] " + LOG_FORMAT))
    console.nomadlife = True
    log.addHandler(console)
    path = app.config.get("LOG_FILE")
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        # Only the owner reads it: it names the accounts the emails go to (their domains).
        os.close(os.open(path, os.O_CREAT | os.O_APPEND, 0o600))
        fh = logging.handlers.RotatingFileHandler(
            path, maxBytes=LOG_FILE_BYTES, backupCount=LOG_FILE_KEEP, encoding="utf-8",
            delay=True)
    except OSError as exc:
        log.warning("Cannot write the log file %s: %s", path, exc)
        return
    fh.setFormatter(logging.Formatter(LOG_FORMAT))
    fh.nomadlife = True
    # One handler for both loggers: the app's logger only passes WARNING and above anyway.
    log.addHandler(fh)
    app.logger.addHandler(fh)


def describe_settings(config):
    """One line saying what the mailer will use: the first thing to read when mail fails."""
    user = config.get("GMAIL_USER") or ""
    password = config.get("GMAIL_APP_PASSWORD") or ""
    if not user or not password:
        return ("Email sending is OFF (GMAIL_USER or GMAIL_APP_PASSWORD is empty in "
                "config.env): emails are printed in the terminal instead.")
    host = config.get("SMTP_HOST") or "smtp.gmail.com"
    port = config.get("SMTP_PORT") or 587
    mode = "TLS at once" if port == 465 else "STARTTLS"
    return (f"Email sending is ON: {user} through {host}:{port} ({mode}), password set "
            f"({len(password)} characters).")


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


def smtp_failure(stage, exc):
    """What went wrong at `stage`, in words that name the likely cause."""
    detail = f"{type(exc).__name__}: {exc}"
    code = getattr(exc, "smtp_code", None)
    if code:
        error = getattr(exc, "smtp_error", b"")
        error = error.decode("utf-8", "replace") if isinstance(error, bytes) else str(error)
        detail += f" (server answer {code} {error.strip()})"
    detail = redact_emails(detail)  # a refusal names the recipient: SMTPRecipientsRefused does
    hint = ""
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        hint = ("the server refused the user name or password: check GMAIL_USER and "
                "GMAIL_APP_PASSWORD (an app password for Gmail, the mailbox or app password "
                "for another provider)")
    elif isinstance(exc, smtplib.SMTPServerDisconnected) and stage == "login":
        hint = ("the server hung up while logging in: the provider blocks this login (Gmail "
                "Advanced Protection and some security blocks do this) or the SMTP_HOST and "
                "SMTP_PORT do not belong to this mailbox")
    elif isinstance(exc, smtplib.SMTPServerDisconnected):
        hint = "the server hung up: wrong SMTP_PORT for the TLS mode, or a firewall"
    elif isinstance(exc, (socket.gaierror,)):
        hint = "the server name SMTP_HOST does not resolve: check the spelling and the network"
    elif isinstance(exc, (TimeoutError, socket.timeout, ConnectionRefusedError)):
        hint = "no answer from the server: the port is closed or blocked by a firewall"
    elif isinstance(exc, ssl.SSLError):
        hint = ("the TLS handshake failed: port 465 needs TLS at once and 587 STARTTLS, and "
                "the certificate must match SMTP_HOST")
    elif isinstance(exc, smtplib.SMTPSenderRefused):
        hint = "the server refuses this sender: GMAIL_USER must be the mailbox you log in with"
    elif isinstance(exc, smtplib.SMTPRecipientsRefused):
        hint = "the server refused the recipient address"
    return f"{detail}; {hint}" if hint else detail


def connect(host, port, context, timeout=20):
    """An SMTP connection with its TLS up, not yet logged in: 465 speaks TLS from the first
    byte, the other ports start plain and upgrade."""
    if port == 465:
        return smtplib.SMTP_SSL(host, port, timeout=timeout, context=context)
    smtp = smtplib.SMTP(host, port, timeout=timeout)
    try:
        smtp.starttls(context=context)
    except Exception:
        smtp.close()
        raise
    return smtp


def send_email(to, subject, text, html=None):
    """Send an email through the SMTP server of config.env (Gmail unless SMTP_HOST says
    otherwise). Returns False only when sending failed.

    When the mailbox credentials are not configured the message is printed to the
    console instead, so the app keeps working during local development. Every attempt is
    logged (log file: see setup_logging) with the stage that failed and why.
    """
    user = current_app.config.get("GMAIL_USER")
    password = current_app.config.get("GMAIL_APP_PASSWORD")

    if not user or not password:
        print("\n[mailer] Email sending is not configured. Email printed below.")
        print(f"To: {to}\nSubject: {subject}\n\n{text}\n", flush=True)
        log.warning("Email not sent: GMAIL_USER or GMAIL_APP_PASSWORD is empty in config.env "
                    "(subject: %s)", subject)
        return True

    logo_path = os.path.join(current_app.static_folder, "img", "logo-mark.png")
    msg = build_message(user, to, subject, text, html, logo_path)

    host = current_app.config.get("SMTP_HOST") or "smtp.gmail.com"
    port = current_app.config.get("SMTP_PORT") or 587
    # smtplib's own default checks neither the certificate nor the host name, so anyone on
    # the network path could read the password and every reset link: always verify.
    context = ssl.create_default_context()
    domain = to.rpartition("@")[2]  # the log keeps the domain, not the person's address
    where = f"{host}:{port} as {user}"
    stage = "connect"
    sent = False
    try:
        smtp = connect(host, port, context)
        with smtp:
            stage = "login"
            smtp.login(user, password)
            stage = "send"
            smtp.send_message(msg)
            sent = True
    except (smtplib.SMTPException, OSError) as exc:
        if sent:  # the server accepted the message; only saying goodbye (QUIT) failed
            log.warning("Email sent to @%s, but closing the connection failed: %s", domain, exc)
            return True
        log.error("Could not send email %r to @%s through %s: failed at %s: %s",
                  subject, domain, where, stage, smtp_failure(stage, exc))
        return False
    log.info("Email %r sent to @%s through %s", subject, domain, where)
    return True


def diagnose(config, to, say=print):
    """Walk through every step of sending (the `flask test-email` command), saying what each
    one found, and stop at the first that fails. Returns True when the test email was sent.
    Passwords are never printed, and smtplib's own debug output (which shows them) is not
    used."""
    user = config.get("GMAIL_USER") or ""
    password = config.get("GMAIL_APP_PASSWORD") or ""
    host = config.get("SMTP_HOST") or "smtp.gmail.com"
    port = config.get("SMTP_PORT") or 587
    say(describe_settings(config))
    if not user or not password:
        say("Fix this first: set GMAIL_USER and GMAIL_APP_PASSWORD in config.env.")
        return False
    stage = "dns"
    try:
        say(f"1. Looking up {host} ...")
        say("   ok: " + ", ".join(sorted({i[4][0] for i in socket.getaddrinfo(host, port)})))
        stage = "connect"
        say(f"2. Connecting to {host}:{port} "
            f"({'TLS at once' if port == 465 else 'plain, then STARTTLS'}) ...")
        context = ssl.create_default_context()
        smtp = connect(host, port, context)
        with smtp:
            say(f"   ok: connected, certificate for {host} accepted")
            stage = "login"
            say(f"3. Logging in as {user} ...")
            smtp.login(user, password)
            say("   ok: the server accepted the user name and password")
            stage = "send"
            say(f"4. Sending a test email to {to} ...")
            smtp.send_message(build_message(
                user, to, "Nomad Life test email",
                "This is a test of the Nomad Life email settings. It arrived.\n"))
            say("   ok: the server accepted the message")
    except (smtplib.SMTPException, OSError) as exc:
        say(f"   FAILED at step '{stage}': {smtp_failure(stage, exc)}")
        log.error("test-email failed at %s through %s:%s as %s: %s", stage, host, port, user,
                  smtp_failure(stage, exc))
        return False
    log.info("test-email sent to @%s through %s:%s as %s", to.rpartition("@")[2], host, port,
             user)
    say("Done: check the inbox (and the spam folder) of " + to + ".")
    return True
