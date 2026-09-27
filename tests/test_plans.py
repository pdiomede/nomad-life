"""Plans: header chip, upgrade page, landing pricing and per plan storage limits."""
import json
import os
import re
import shutil
import sqlite3
import tempfile
import unittest

import app as appmod
from tests.helpers import AppTestCase

MB = appmod.MB


class PlanTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()
        self.new_year(2026)

    def set_plan(self, key):
        with self.db() as conn:
            conn.execute("UPDATE users SET plan = ?", (key,))

    def page(self, url):
        return self.client.get(url).get_data(as_text=True)

    def test_new_users_are_on_free_and_see_the_chip(self):
        html = self.page("/year/2026")
        self.assertIn('aria-label="Your plan: Free"', html)
        self.assertLess(html.index("plan-chip"), html.index("topnav-email"))

    def test_chip_follows_the_plan(self):
        self.set_plan("pro")
        self.assertIn('aria-label="Your plan: Pro"', self.page("/year/2026"))

    def test_storage_card_links_to_the_plan_page(self):
        html = self.page("/year/2026")
        self.assertIn('href="/plan">Upgrade</a>', html)

    def test_plan_page_shows_current_and_next_tier(self):
        html = self.page("/plan")
        self.assertIn("Your plan", html)
        self.assertIn("Upgrade to Pro, &euro;4 / month", html)
        self.assertIn("Online payment is coming soon.", html)
        self.assertIn("5 GB of receipt storage", html)

    def test_pro_sees_nomad_plus(self):
        self.set_plan("pro")
        self.assertIn("Upgrade to Nomad+, &euro;9 / month", self.page("/plan"))

    def test_top_plan(self):
        self.set_plan("plus")
        html = self.page("/plan")
        self.assertIn("You are on the top plan", html)
        self.assertNotIn("Upgrade to", html)
        self.assertIn('href="/plan">Your plan</a>', self.page("/year/2026"))

    def test_unknown_plan_counts_as_free(self):
        self.set_plan("gold")
        self.assertIn('aria-label="Your plan: Free"', self.page("/year/2026"))

    def test_plan_page_needs_sign_in_and_is_private(self):
        self.client.post("/logout")
        self.assertIn("/login", self.client.get("/plan").headers["Location"])
        self.assertIn("Disallow: /plan", self.page("/robots.txt"))

    def test_landing_lists_every_tier(self):
        self.client.post("/logout")
        html = self.page("/")
        for text in ('id="pricing"', ">Free<", ">Pro<", ">Nomad+<", "&euro;4", "&euro;9",
                     "25 GB of receipt storage", "Priority support"):
            self.assertIn(text, html)

    def test_quota_follows_the_plan(self):
        self.app.config["USER_QUOTA_BYTES"] = 1 * MB
        body = b"%PDF" + b"x" * (2 * MB)
        self.upload("/year/2026/base", "big.pdf", body)
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM documents").fetchone()[0], 0)
        self.set_plan("pro")
        self.upload("/year/2026/base", "big.pdf", body)
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM documents").fetchone()[0], 1)

    def test_receipt_limit_follows_the_plan(self):
        body = b"%PDF" + b"x" * (MB * 21 // 2)  # 10.5 MB: over the Free limit, under its cap
        self.upload("/year/2026/base", "big.pdf", body)
        html = self.page("/year/2026/base")
        self.assertIn("larger than the 10 MB limit per receipt", html)
        self.set_plan("pro")
        self.upload("/year/2026/base", "big.pdf", body)
        with self.db() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM documents").fetchone()[0], 1)
        self.assertIn("max 25 MB", self.page("/year/2026/base"))

    def test_paid_plans_never_below_free(self):
        self.app.config["USER_QUOTA_BYTES"] = 50 * 1024 * MB
        pro = appmod.plan_catalog(self.app.config)[1]
        self.assertEqual(pro["quota_bytes"], 50 * 1024 * MB)


class PlanMigrationTests(unittest.TestCase):
    def test_existing_users_become_free(self):
        tmp = tempfile.mkdtemp(prefix="nomadlife-test-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        path = os.path.join(tmp, "old.db")
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT "
                     "NULL UNIQUE, password_hash TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT "
                     "CURRENT_TIMESTAMP, verified_at TEXT, email_sent_at TEXT)")
        conn.execute("INSERT INTO users (email, password_hash) VALUES ('old@example.com', 'x')")
        conn.commit()
        conn.close()
        appmod.create_app({"TESTING": True, "SECRET_KEY": "k", "DATABASE_PATH": path,
                           "UPLOAD_DIR": os.path.join(tmp, "uploads")})
        conn = sqlite3.connect(path)
        self.assertEqual(conn.execute("SELECT plan FROM users").fetchall(), [("free",)])
        conn.close()


if __name__ == "__main__":
    unittest.main()


class SizeTests(unittest.TestCase):
    def test_gigabytes(self):
        self.assertEqual(appmod.format_size(5 * 1024 * MB), "5 GB")
        self.assertEqual(appmod.format_size(1536 * MB, "down"), "1.5 GB")
        self.assertEqual(appmod.format_size(1024 * MB - 1, "down"), "1023.9 MB")
        self.assertEqual(appmod.format_size(1024 * MB - 1, "up"), "1 GB")
        self.assertEqual(appmod.format_size(500 * MB), "500 MB")


class PlanBugTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()
        self.new_year(2026)

    def test_oversized_request_is_refused_before_reading_on_free(self):
        # The global cap fits the largest plan; a Free user's request keeps the Free cap.
        resp = self.upload("/year/2026/base", "big.pdf", b"%PDF" + b"x" * (15 * MB))
        self.assertEqual(resp.status_code, 302)
        html = self.client.get(resp.headers["Location"], follow_redirects=True).get_data(as_text=True)
        self.assertIn("This form was too large to save. Receipts can be at most 10 MB each.", html)

    def test_structured_data_lists_every_plan_in_euro(self):
        self.client.post("/logout")
        html = self.client.get("/").get_data(as_text=True)
        data = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>',
                                    html, re.S).group(1))
        app_node = next(n for n in data["@graph"] if n["@type"] == "WebApplication")
        offers = {o["name"]: (o["price"], o["priceCurrency"]) for o in app_node["offers"]}
        self.assertEqual(offers, {"Free": ("0", "EUR"), "Pro": ("4", "EUR"),
                                  "Nomad+": ("9", "EUR")})


class AdminPlanTests(AppTestCase):
    def test_admin_default_quota_is_the_plan_quota(self):
        self.app.config["ADMIN_EMAILS"] = frozenset({"admin@example.com"})
        user = self.app.test_client()
        self.signup("user@example.com", client=user)
        with self.db() as conn:
            conn.execute("UPDATE users SET plan = 'pro' WHERE email = 'user@example.com'")
        self.assertIn(b"of 5 GB", user.get("/plan").data)
        self.signup("admin@example.com")
        html = self.client.get("/admin").get_data(as_text=True)
        self.assertIn("0 B used of 5 GB", html)
        self.assertIn("(Pro plan)", html)


class AdminPlanSelectorTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.app.config["ADMIN_EMAILS"] = frozenset({"admin@example.com"})
        self.user = self.app.test_client()
        self.signup("user@example.com", client=self.user)
        self.signup("admin@example.com")
        with self.db() as conn:
            self.uid = conn.execute("SELECT id FROM users WHERE email = 'user@example.com'"
                                    ).fetchone()[0]

    def set_plan(self, key):
        return self.client.post(f"/admin/users/{self.uid}", data={"action": "plan", "plan": key},
                                follow_redirects=True).get_data(as_text=True)

    def plan(self):
        with self.db() as conn:
            return conn.execute("SELECT plan FROM users WHERE id = ?", (self.uid,)).fetchone()[0]

    def test_selector_lists_every_plan_with_the_current_one_selected(self):
        html = self.client.get("/admin").get_data(as_text=True)
        self.assertIn(f'id="plan-{self.uid}"', html)
        self.assertIn('<option value="free" selected>Free</option>', html)
        self.assertIn('<option value="plus">Nomad+</option>', html)

    def test_admin_changes_the_plan(self):
        html = self.set_plan("pro")
        self.assertIn("user@example.com is now on the Pro plan (5 GB of storage).", html)
        self.assertEqual(self.plan(), "pro")
        self.assertIn('aria-label="Your plan: Pro"', self.user.get("/plan").get_data(as_text=True))

    def test_unknown_plan_is_refused(self):
        self.assertIn("Choose one of the plans.", self.set_plan("gold"))
        self.assertEqual(self.plan(), "free")

    def test_custom_quota_is_mentioned(self):
        self.client.post(f"/admin/users/{self.uid}", data={"action": "quota", "quota_mb": "100"})
        self.assertIn("custom storage quota (100 MB) still applies", self.set_plan("plus"))

    def test_only_admins(self):
        resp = self.user.post(f"/admin/users/{self.uid}", data={"action": "plan", "plan": "plus"})
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(self.plan(), "free")
