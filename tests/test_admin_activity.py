"""Admin Security activity: a table with 20 events a page, sortable columns and the account filter."""
import re

import db as dbmod
from tests.helpers import AppTestCase


class AdminActivityTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.app.config["ADMIN_EMAILS"] = frozenset({"admin@example.com"})
        self.app.config["ADMIN_REQUIRE_2FA"] = False
        self.signup("admin@example.com")
        with self.db() as conn:
            conn.execute("DELETE FROM audit_log")

    def add(self, rows):
        """rows: (event, email, ip) tuples, oldest first."""
        with self.db() as conn:
            conn.executemany("INSERT INTO audit_log (event, email, ip) VALUES (?, ?, ?)", rows)

    def listed(self, query=""):
        html = self.client.get("/admin" + query).get_data(as_text=True)
        section = html.split('id="activity"', 1)[1]
        rows = re.findall(r'<tr>\s*<td class="small activity-time">.*?</tr>', section, re.S)
        return section, rows

    def ids(self, query=""):
        """The email of each listed row (or its IP, for rows without one)."""
        _section, rows = self.listed(query)
        out = []
        for row in rows:
            m = re.search(r'activity-account hide-sm">(.*?)</td>', row, re.S)
            out.append(re.sub(r"<[^>]+>", "", m[1]).strip())
        return out

    def test_twenty_a_page_newest_first(self):
        self.add([("sign_in", f"u{i:02d}@example.com", "") for i in range(45)])
        self.assertEqual(self.ids()[:2], ["u44@example.com", "u43@example.com"])
        self.assertEqual(len(self.ids()), 20)
        self.assertEqual(self.ids("?apage=3"), [f"u{i:02d}@example.com" for i in range(4, -1, -1)])
        section, _ = self.listed("?apage=2")
        self.assertIn("Showing 21 to 40 of 45 events", section)
        self.assertIn('aria-label="Security activity pages"', section)
        # Out of range or invalid pages are clamped.
        self.assertEqual(self.ids("?apage=99"), self.ids("?apage=3"))
        self.assertEqual(self.ids("?apage=x"), self.ids())
        self.assertEqual(self.ids("?apage=-4"), self.ids())

    def test_sort_by_every_column(self):
        self.add([("sign_in_failed", "b@example.com", "10.0.0.1"),
                  ("sign_in", "A@example.com", "9.1.1.1"),
                  ("admin_price", "", ""),
                  ("account_confirmed", "c@example.com", "2001:db8::1"),
                  ("sign_in", "d@example.com", "::ffff:8.8.8.8")])
        self.assertEqual(self.ids("?asort=account&adir=asc"),
                         ["A@example.com", "b@example.com", "c@example.com", "d@example.com", "none"])
        self.assertEqual(self.ids("?asort=account"),
                         ["d@example.com", "c@example.com", "b@example.com", "A@example.com", "none"])
        # IPs sort by number, IPv4 (also written as ::ffff:) before IPv6, empty last.
        self.assertEqual(self.ids("?asort=ip&adir=asc"),
                         ["d@example.com", "A@example.com", "b@example.com", "c@example.com", "none"])
        self.assertEqual(self.ids("?asort=ip")[-1], "none")
        # Event sorts by the label shown: Account confirmed, Plan prices changed, Signed in (x2),
        # Wrong password at sign in; ties newest first.
        self.assertEqual(self.ids("?asort=event&adir=asc"),
                         ["c@example.com", "none", "d@example.com", "A@example.com", "b@example.com"])
        self.assertEqual(self.ids("?adir=asc")[0], "b@example.com")
        # An unknown column or direction falls back to the default.
        self.assertEqual(self.ids("?asort=password&adir=sideways"), self.ids())

    def test_sort_headers_and_pager_keep_the_accounts_table(self):
        self.add([("sign_in", f"u{i}@example.com", "") for i in range(25)])
        self.add([("sign_in", "u", "")] * 25)  # an account filter with a second page
        section, _ = self.listed("?search=adm&sort=email&page=1&asort=ip&adir=asc")
        self.assertIn('href="/admin?search=adm&amp;sort=email&amp;asort=ip#activity"', section)
        self.assertIn('href="/admin?search=adm&amp;sort=email&amp;asort=event&amp;adir=asc#activity"'
                      .replace("&amp;adir=asc", ""), section)
        self.assertIn('href="/admin?search=adm&amp;sort=email&amp;asort=ip&amp;adir=asc&amp;apage=2'
                      '#activity"', section)
        self.assertIn('<input type="hidden" name="search" value="adm">', section)
        self.assertIn('<input type="hidden" name="asort" value="ip">', section)
        # And the accounts table keeps the activity table.
        html = self.client.get("/admin?asort=ip&apage=2&q=u").get_data(as_text=True)
        accounts = html.split('id="activity"', 1)[0]
        self.assertIn('href="/admin?sort=email&amp;q=u&amp;asort=ip&amp;apage=2#accounts"', accounts)
        self.assertIn('<input type="hidden" name="asort" value="ip">', accounts)

    def test_filter_by_account_with_paging(self):
        self.add([("sign_in", "a@example.com" if i % 2 else "b@example.com", "") for i in range(50)])
        section, rows = self.listed("?q=A@Example.com")
        self.assertEqual(len(rows), 20)
        self.assertIn("Showing 1 to 20 of 25 events for &ldquo;a@example.com&rdquo;", section)
        self.assertEqual(set(self.ids("?q=a@example.com&apage=2")), {"a@example.com"})
        self.assertIn('href="/admin#activity">Clear</a>', section)
        section, rows = self.listed("?q=nobody@example.com")
        self.assertEqual(rows, [])
        self.assertIn("No events for &ldquo;nobody@example.com&rdquo;.", section)

    def test_account_page_table_and_history_link(self):
        self.signup("user@example.com", client=self.app.test_client())
        html = self.client.get("/admin/users/2?search=us&asort=ip").get_data(as_text=True)
        self.assertIn('<table class="table activity-table">', html)
        self.assertIn("Account confirmed", html)
        self.assertIn('href="/admin?search=us&amp;q=user@example.com&amp;asort=ip#activity">Full history',
                      html)
        # Its forms carry both tables' state back.
        self.assertIn('<input type="hidden" name="asort" value="ip">', html)
        resp = self.client.post("/admin/users/2", data={"action": "plan", "plan": "pro",
                                                        "search": "us", "asort": "ip"})
        self.assertEqual(resp.headers["Location"], "/admin/users/2?search=us&asort=ip")


class IpSortKeyTests(AppTestCase):
    def test_keys(self):
        key = dbmod.ip_sort_key
        self.assertLess(key("9.255.255.255"), key("10.0.0.0"))
        self.assertEqual(key("::ffff:1.2.3.4"), key("1.2.3.4"))
        self.assertLess(key("255.255.255.255"), key("::1"))
        self.assertLess(key("::1"), key("unknown"))
        self.assertIsNone(key(""))
        self.assertIsNone(key(None))
