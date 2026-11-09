"""Delegate fixes of 2.1.3."""

from unittest import mock

import app as appmod
from tests.test_delegates import DelegateCase
from tests.test_fixes_158 import other_connection


class ResendTests(DelegateCase):
    def resend(self, did):
        return self.text(self.client.post("/settings", data={
            "action": "delegate_resend", "delegate_id": did}, follow_redirects=True))

    def test_pressing_send_again_too_early_does_not_use_up_the_invitation_limit(self):
        self.invite()
        did = self.share_id()
        sent = len(self.outbox)
        # Four early presses: each is answered with the cooldown, none sends an email, so
        # none may count against the 3 invitations an address can get in a quarter of an hour.
        for _ in range(4):
            self.assertIn("less than a minute ago", self.resend(did))
        self.assertEqual(len(self.outbox), sent)
        self.age_invitation(did)
        self.assertIn("was sent to d@example.com again", self.resend(did))

    def age_invitation(self, did):
        with self.db() as conn:
            conn.execute("UPDATE delegates SET invited_at = datetime('now', '-2 minutes') "
                         "WHERE id = ?", (did,))

    def test_a_withdrawn_invitation_is_said_so_after_the_claim(self):
        self.invite()
        did = self.share_id()
        self.age_invitation(did)
        real = appmod.db.execute_rowcount

        def claim_then_remove(*args, **kwargs):
            n = real(*args, **kwargs)  # Send again claimed the invitation ...
            other = other_connection(self)
            other.execute("DELETE FROM delegates WHERE id = ?", (did,))
            other.commit()
            other.close()  # ... and the owner removed it in another tab meanwhile
            return n

        sent = len(self.outbox)
        with mock.patch.object(appmod.db, "execute_rowcount", claim_then_remove):
            page = self.resend(did)
        self.assertIn("withdrawn in the meantime", page)
        self.assertEqual(len(self.outbox), sent)


class InviteLimitTests(DelegateCase):
    def test_refused_invitations_do_not_use_up_the_limits(self):
        self.invite()
        for _ in range(4):  # already a delegate: answered, nothing is sent
            self.assertIn("already one of your delegates", self.text(self.invite()))
        # Another owner can still invite the same address: the 3 invitations it may get in
        # a quarter of an hour were not spent by the refused ones.
        b = self.app.test_client()
        self.signup("b@example.com", client=b)
        sent = len(self.outbox)
        page = self.text(self.invite(client=b))
        self.assertIn("d@example.com was invited", page)
        self.assertEqual(len(self.outbox), sent + 1)
