"""A delegate sees what is shared with them (2.1.4): a red dot on the account menu while an
invitation waits, a banner on their own dashboard linking to the shared calendar, and Switch
back while viewing it."""

from tests.test_delegates import DelegateCase


class SharedNoticeTests(DelegateCase):
    def setUp(self):
        super().setUp()
        self.new_year(2026, client=self.d)  # the delegate has a year of their own

    def own(self, path="/year/2026"):
        return self.text(self.d.get(path))

    def test_nothing_shared_nothing_shown(self):
        page = self.own()
        self.assertNotIn("user-menu-dot", page)
        self.assertIn('data-tip="d@example.com"', page)
        self.assertNotIn("share-notice", page)

    def test_a_waiting_invitation_shows_the_dot_everywhere_and_the_banner_on_the_dashboard(self):
        self.invite()
        for path in ("/year/2026", "/settings", "/support"):
            page = self.own(path)
            self.assertIn('<span class="user-menu-dot" aria-hidden="true"></span>', page, path)
            self.assertIn('data-tip="1 new notification: an account was shared with you"', page)
            self.assertIn("(1 new notification: an account was shared with you)", page)
        page = self.own()
        self.assertIn("<strong dir=\"auto\">a@example.com</strong> invited you to view their "
                      "Nomad Life account.", page)
        self.assertIn('<a class="btn btn-ghost btn-sm tip tip-end share-btn" href="/shared" data-tip="Accept or decline on Shared with you"><span class="btn-icon icon-mail" aria-hidden="true"></span>Review invitation</a>', page)
        self.assertNotIn("share-notice", self.own("/settings"))

    def test_after_accepting_the_dot_goes_and_the_banner_links_to_the_calendar(self):
        self.invite()
        _resp, sid = self.accept()
        page = self.own()
        self.assertNotIn("user-menu-dot", page)
        self.assertIn("shared their account with you.", page)
        self.assertIn(f'<a class="btn btn-ghost btn-sm tip tip-end share-btn" href="/shared/{sid}#calendar" data-tip="Open the calendar of a@example.com, read only"><span class="btn-icon icon-eye" aria-hidden="true"></span>View shared calendar</a>', page)
        resp = self.d.get(f"/shared/{sid}")
        self.assertTrue(resp.headers["Location"].endswith(f"/shared/{sid}/year/2026"))

    def test_the_banner_names_the_owner_once_accepted(self):
        with self.db() as conn:
            conn.execute("UPDATE users SET first_name = 'Paolo', last_name = 'Diomede' "
                         "WHERE email = 'a@example.com'")
        self.invite()
        self.assertIn("<strong dir=\"auto\">a@example.com</strong> invited you", self.own())
        self.accept()
        self.assertIn("<strong dir=\"auto\">Paolo Diomede (a@example.com)</strong> shared "
                      "their account with you.", self.own())

    def test_two_shared_accounts_link_to_the_list(self):
        self.invite()
        self.accept()
        b = self.signup("b@example.com", client=self.app.test_client())
        self.invite(client=b)
        sid = self.rows()[-1]["id"]
        self.d.post(f"/shared/{sid}/accept")
        page = self.own()
        self.assertIn("2 accounts are shared with you.", page)
        self.assertIn('<a class="btn btn-ghost btn-sm tip tip-end share-btn" href="/shared" data-tip="Choose which shared account to open"><span class="btn-icon icon-eye" aria-hidden="true"></span>View shared calendars</a>', page)

    def test_a_disabled_owner_shows_nothing(self):
        self.invite()
        with self.db() as conn:
            conn.execute("UPDATE users SET disabled = 1 WHERE email = 'a@example.com'")
        page = self.own()
        self.assertNotIn("user-menu-dot", page)
        self.assertNotIn("share-notice", page)

    def test_the_shared_view_says_switch_back_and_shows_no_notice(self):
        self.invite()
        _resp, sid = self.accept()
        page = self.text(self.d.get(f"/shared/{sid}/year/2026"))
        self.assertIn('<a class="btn btn-ghost btn-sm tip tip-end share-btn" href="/app" data-tip="Back to your own account"><span class="btn-icon icon-back" aria-hidden="true"></span>Switch back</a>', page)
        self.assertIn('<a class="btn btn-ghost btn-sm tip tip-end share-btn" href="/shared" data-tip="All the accounts shared with you"><span class="btn-icon icon-people" aria-hidden="true"></span>Shared with you</a>', page)
        self.assertNotIn("Back to my account", page)
        self.assertNotIn("share-notice", page)

    def test_the_owner_sees_no_notice(self):
        self.invite()
        page = self.text(self.client.get("/year/2026"))
        self.assertNotIn("user-menu-dot", page)
        self.assertNotIn("share-notice", page)


class StyleTests(DelegateCase):
    """2.1.6: the banner buttons' icons, and Settings titles bold and larger (CSS text)."""

    def css(self):
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "static", "css", "style.css"), encoding="utf-8") as fh:
            return fh.read()

    def test_every_banner_icon_is_defined(self):
        css = self.css()
        for icon in ("icon-people", "icon-back", "icon-plus", "icon-eye", "icon-mail"):
            self.assertIn(f".{icon} {{ --btn-icon: url(", css)

    def test_settings_titles_are_bold_and_larger(self):
        self.assertIn(".settings-page .card-head h2 { font-size: 20px; font-weight: 700; }",
                      self.css())
        self.assertIn('<div class="narrow settings-page">', self.text(self.client.get("/settings")))
