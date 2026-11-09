"""2.1.7: turning the public calendar link on does not wait for the security alert email."""
from unittest import mock

import app as appmod
from tests.helpers import AppTestCase


class PublicCalendarOnIsQuickTests(AppTestCase):
    def test_alert_is_sent_in_the_background(self):
        self.signup()
        with mock.patch.object(appmod, "send_template_email", return_value=True) as send:
            self.client.post("/settings", data={"action": "public_calendar", "value": "on"})
        calls = [c for c in send.call_args_list if c.args[2] == "security_alert"]
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].kwargs.get("background"))
