"""Shared helpers: an isolated app (temporary database and upload folder) per test."""
import csv
import io
import os
import re
import shutil
import sqlite3
import tempfile
import unittest
import uuid
import zipfile
from unittest import mock

import app as appmod


def ref(n):
    """The number of the n-th ticket opened this year (UTC), as shown and in URLs: 2026-1."""
    from datetime import datetime, timezone
    return f"{datetime.now(timezone.utc).year}-{n}"


class AppTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nomadlife-test-")
        self.app = appmod.create_app({
            "TESTING": True, "WTF_CSRF_ENABLED": False, "SECRET_KEY": "test-secret",
            "DATABASE_PATH": os.path.join(self.tmp, "test.db"),
            "UPLOAD_DIR": os.path.join(self.tmp, "uploads"),
            # config.env may hold real Gmail credentials: tests must never send email.
            "GMAIL_USER": "", "GMAIL_APP_PASSWORD": "",
            # Nor real hCaptcha keys: the captcha is off unless a test turns it on (test_captcha.py).
            "HCAPTCHA_SITEKEY": "", "HCAPTCHA_SECRET": "",
            # Nor real Stripe keys: online payment is off unless a test turns it on.
            "STRIPE_SECRET_KEY": "", "STRIPE_WEBHOOK_SECRET": "", "STRIPE_PRICE_PRO": "",
            "STRIPE_PRICE_PLUS": "",
            # Nor may its size settings change what the tests expect: use the defaults.
            "MAX_RECEIPT_BYTES": 10 * appmod.MB, "USER_QUOTA_BYTES": 500 * appmod.MB,
            # Admin page tests of other features skip the two-factor requirement; the tests in
            # test_security_129.py turn it back on.
            "ADMIN_REQUIRE_2FA": False,
            # Tests never call Have I Been Pwned; test_security_1210.py checks it with a fake.
            "PWNED_CHECK": False,
            # Emails are recorded at once, so tests can read the outbox right after a request.
            "EMAIL_IN_BACKGROUND": False,
        })
        self.client = self.app.test_client()
        self.outbox = []
        patcher = mock.patch.object(appmod, "send_email", side_effect=self._record_email)
        self.send_email = patcher.start()
        self.addCleanup(patcher.stop)

    def _record_email(self, to, subject, text, html=None):
        self.outbox.append({"to": to, "subject": subject, "text": text, "html": html})
        return True

    def set_plan(self, key, email=None):
        with self.db() as conn:
            if email:
                conn.execute("UPDATE users SET plan = ? WHERE email = ?", (key, email))
            else:
                conn.execute("UPDATE users SET plan = ?", (key,))

    def age_emails(self, seconds=61):
        """Pretend the last emails went out long enough ago to send another one."""
        with self.db() as conn:
            conn.execute("UPDATE users SET email_sent_at = datetime('now', ?)",
                         (f"-{seconds} seconds",))

    def last_link(self, to=None, kind="verify"):
        """Path of the newest emailed link of this kind (optionally to one address)."""
        for mail in reversed(self.outbox):
            if to is None or mail["to"] == to:
                found = re.search(rf"https?://[^/\s]+(/{kind}/\S+)", mail["text"])
                if found:
                    return found.group(1)
        raise AssertionError(f"no {kind} link was emailed")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- data helpers ---

    def db(self):
        conn = sqlite3.connect(self.app.config["DATABASE_PATH"])
        conn.row_factory = sqlite3.Row
        return conn

    def signup(self, email="a@example.com", client=None, password="password1"):
        """Sign up, open the confirmation link from the email and press its button, then sign
        in."""
        client = client or self.client
        client.post("/signup", data={"email": email, "password": password, "confirm": password})
        client.post(self.last_link(email))
        client.post("/login", data={"email": email, "password": password})
        return client

    def new_year(self, year, base_city="Lisbon", base_country="Portugal", client=None):
        (client or self.client).post("/year/new", data={
            "year": str(year), "base_city": base_city, "base_country": base_country})
        with self.db() as conn:
            return conn.execute("SELECT id, user_id FROM years WHERE year = ? ORDER BY id DESC",
                                (year,)).fetchone()

    def add_movement(self, year, city, country, start, end, notes=""):
        self.client.post(f"/year/{year}/movements/new", data={
            "city": city, "country": country, "start_date": start, "end_date": end,
            "notes": notes})
        with self.db() as conn:
            return conn.execute("SELECT max(id) FROM movements").fetchone()[0]

    def upload(self, url, name, body, kind="other"):
        return self.client.post(url, data={"action": "upload", "kind": kind,
                                           "file": (io.BytesIO(body), name)},
                                content_type="multipart/form-data")

    def insert_document(self, year_row, original_name, body, movement_id=None, kind="other",
                        ext="pdf"):
        """Insert a document directly, bypassing upload cleaning, to test package safety."""
        stored = f"{uuid.uuid4().hex}.{ext}"
        folder = os.path.join(self.app.config["UPLOAD_DIR"], str(year_row["user_id"]))
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, stored), "wb") as fh:
            fh.write(body)
        with self.db() as conn:
            conn.execute(
                "INSERT INTO documents (user_id, year_id, movement_id, kind, original_name, "
                "stored_name, mime, size) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (year_row["user_id"], year_row["id"], movement_id, kind, original_name, stored,
                 "application/pdf", len(body)))
        return stored

    # --- package helpers ---

    def package(self, year, notes=False, client=None):
        resp = (client or self.client).get(f"/year/{year}/package" + ("?notes=1" if notes else ""))
        self.assertEqual(resp.status_code, 200, resp.data[:200])
        return resp, zipfile.ZipFile(io.BytesIO(resp.data))

    @staticmethod
    def root(zf):
        return zf.namelist()[0].split("/")[0]

    def read_csv(self, zf, name):
        raw = zf.read(f"{self.root(zf)}/{name}")
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"), f"{name} should start with a UTF-8 BOM")
        return list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
