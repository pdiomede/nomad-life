"""The mailer talks to the SMTP server of config.env: Gmail by default, port 465 with TLS."""
from unittest import mock

import mailer
from tests.helpers import AppTestCase


class SmtpServerTests(AppTestCase):
    def send(self, **config):
        self.app.config.update(GMAIL_USER="info@nomadlife.pro", GMAIL_APP_PASSWORD="pw", **config)
        with self.app.app_context():
            return mailer.send_email("you@example.com", "Hi", "plain")

    def test_default_is_gmail_with_starttls(self):
        with mock.patch.object(mailer.smtplib, "SMTP") as smtp, \
                mock.patch.object(mailer.smtplib, "SMTP_SSL") as ssl_smtp:
            self.assertTrue(self.send(SMTP_HOST="", SMTP_PORT=0))
        smtp.assert_called_once()
        self.assertEqual(smtp.call_args.args[:2], ("smtp.gmail.com", 587))
        ssl_smtp.assert_not_called()
        server = smtp.return_value
        server.starttls.assert_called_once()
        server.login.assert_called_once_with("info@nomadlife.pro", "pw")

    def test_port_465_uses_tls_at_once(self):
        with mock.patch.object(mailer.smtplib, "SMTP") as smtp, \
                mock.patch.object(mailer.smtplib, "SMTP_SSL") as ssl_smtp:
            self.assertTrue(self.send(SMTP_HOST="smtp.hostinger.com", SMTP_PORT=465))
        smtp.assert_not_called()
        self.assertEqual(ssl_smtp.call_args.args[:2], ("smtp.hostinger.com", 465))
        self.assertIsNotNone(ssl_smtp.call_args.kwargs["context"])
        server = ssl_smtp.return_value
        server.starttls.assert_not_called()
        server.login.assert_called_once()

    def test_other_ports_use_starttls_on_the_chosen_host(self):
        with mock.patch.object(mailer.smtplib, "SMTP") as smtp:
            self.assertTrue(self.send(SMTP_HOST="mail.example.com", SMTP_PORT=2525))
        self.assertEqual(smtp.call_args.args[:2], ("mail.example.com", 2525))
        smtp.return_value.starttls.assert_called_once()

    def test_a_failure_returns_false(self):
        with mock.patch.object(mailer.smtplib, "SMTP_SSL", side_effect=OSError("down")):
            self.assertFalse(self.send(SMTP_HOST="smtp.hostinger.com", SMTP_PORT=465))


class MailLogTests(AppTestCase):
    """What the log file says when email fails (config.env's LOG_FILE, by default
    data/nomadlife.log next to the database)."""

    def log_text(self):
        for h in mailer.log.handlers:
            h.flush()
        with open(self.app.config["LOG_FILE"], encoding="utf-8") as fh:
            return fh.read()

    def send(self, **config):
        self.app.config.update(GMAIL_USER="info@nomadlife.pro", GMAIL_APP_PASSWORD="s3cret-pw",
                               SMTP_HOST="smtp.hostinger.com", SMTP_PORT=465, **config)
        with self.app.app_context():
            return mailer.send_email("ada@example.com", "Hello", "plain")

    def test_log_file_sits_next_to_the_database(self):
        import os
        self.assertEqual(os.path.dirname(self.app.config["LOG_FILE"]),
                         os.path.dirname(self.app.config["DATABASE_PATH"]))

    def test_a_login_failure_names_the_stage_the_server_and_the_cause(self):
        import smtplib
        server = mock.MagicMock()
        server.login.side_effect = smtplib.SMTPServerDisconnected(
            "Connection unexpectedly closed")
        with mock.patch.object(mailer.smtplib, "SMTP_SSL", return_value=server):
            self.assertFalse(self.send())
        text = self.log_text()
        self.assertIn("failed at login", text)
        self.assertIn("smtp.hostinger.com:465 as info@nomadlife.pro", text)
        self.assertIn("SMTPServerDisconnected", text)
        self.assertIn("hung up while logging in", text)
        self.assertIn("@example.com", text)
        self.assertNotIn("ada@example.com", text)  # the person's address stays out
        self.assertNotIn("s3cret-pw", text)

    def test_a_wrong_password_gets_the_servers_answer(self):
        import smtplib
        server = mock.MagicMock()
        server.login.side_effect = smtplib.SMTPAuthenticationError(
            535, b"5.7.8 Authentication failed")
        with mock.patch.object(mailer.smtplib, "SMTP_SSL", return_value=server):
            self.assertFalse(self.send())
        text = self.log_text()
        self.assertIn("535 5.7.8 Authentication failed", text)
        self.assertIn("check GMAIL_USER and GMAIL_APP_PASSWORD", text)

    def test_a_connection_failure_is_logged_as_connect(self):
        import socket
        with mock.patch.object(mailer.smtplib, "SMTP_SSL", side_effect=socket.gaierror("no host")):
            self.assertFalse(self.send())
        self.assertIn("failed at connect", self.log_text())
        self.assertIn("does not resolve", self.log_text())

    def test_a_sent_email_is_logged(self):
        with mock.patch.object(mailer.smtplib, "SMTP_SSL"):
            self.assertTrue(self.send())
        self.assertIn("sent to @example.com through smtp.hostinger.com:465", self.log_text())

    def test_without_credentials_the_log_says_so(self):
        self.app.config.update(GMAIL_USER="", GMAIL_APP_PASSWORD="")
        with self.app.app_context():
            self.assertTrue(mailer.send_email("ada@example.com", "Hello", "plain"))
        self.assertIn("GMAIL_USER or GMAIL_APP_PASSWORD is empty", self.log_text())

    def test_settings_line_never_shows_the_password(self):
        line = mailer.describe_settings({"GMAIL_USER": "a@b.c", "GMAIL_APP_PASSWORD": "s3cret-pw",
                                         "SMTP_HOST": "smtp.hostinger.com", "SMTP_PORT": 465})
        self.assertIn("a@b.c through smtp.hostinger.com:465 (TLS at once)", line)
        self.assertIn("9 characters", line)
        self.assertNotIn("s3cret", line)
        self.assertIn("OFF", mailer.describe_settings({"GMAIL_USER": "a@b.c"}))

    def test_diagnose_stops_at_the_failing_step(self):
        import smtplib
        out = []
        server = mock.MagicMock()
        server.login.side_effect = smtplib.SMTPAuthenticationError(
            535, b"bad credentials")
        config = {"GMAIL_USER": "info@nomadlife.pro", "GMAIL_APP_PASSWORD": "s3cret-pw",
                  "SMTP_HOST": "smtp.hostinger.com", "SMTP_PORT": 465}
        with mock.patch.object(mailer.socket, "getaddrinfo",
                               return_value=[(0, 0, 0, "", ("203.0.113.5", 465))]), \
                mock.patch.object(mailer.smtplib, "SMTP_SSL", return_value=server):
            self.assertFalse(mailer.diagnose(config, "me@example.com", say=out.append))
        text = "\n".join(out)
        self.assertIn("203.0.113.5", text)
        self.assertIn("FAILED at step 'login'", text)
        self.assertIn("535", text)
        self.assertNotIn("Sending a test email", text)
        self.assertNotIn("s3cret-pw", text)

    def test_diagnose_reports_success(self):
        out = []
        config = {"GMAIL_USER": "info@nomadlife.pro", "GMAIL_APP_PASSWORD": "pw",
                  "SMTP_HOST": "smtp.hostinger.com", "SMTP_PORT": 587}
        with mock.patch.object(mailer.socket, "getaddrinfo",
                               return_value=[(0, 0, 0, "", ("203.0.113.5", 587))]), \
                mock.patch.object(mailer.smtplib, "SMTP"):
            self.assertTrue(mailer.diagnose(config, "me@example.com", say=out.append))
        self.assertIn("the server accepted the message", "\n".join(out))
