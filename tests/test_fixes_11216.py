"""1.12.16 fixes from a review of every database call: a transaction whose commit fails is
rolled back, and the tests' helper connections are closed (the colors review's spelling bug is
in tests/test_calendar_colors.py)."""
import sqlite3
import unittest
from unittest import mock

import db
from tests.helpers import AppTestCase


class FakeConnection:
    def __init__(self):
        self.in_transaction, self.calls = False, []

    def execute(self, sql, args=()):
        self.calls.append(sql)
        if sql == "BEGIN IMMEDIATE":
            self.in_transaction = True

    def commit(self):
        self.calls.append("COMMIT")
        raise sqlite3.OperationalError("disk I/O error")

    def rollback(self):
        self.calls.append("ROLLBACK")
        self.in_transaction = False


class TransactionTests(unittest.TestCase):
    def test_a_failed_commit_is_rolled_back(self):
        conn = FakeConnection()
        with mock.patch.object(db, "get_db", return_value=conn):
            with self.assertRaises(sqlite3.OperationalError):
                with db.transaction():
                    pass
        self.assertEqual(conn.calls, ["BEGIN IMMEDIATE", "COMMIT", "ROLLBACK"])
        self.assertFalse(conn.in_transaction)


class HelperConnectionTests(AppTestCase):
    def test_the_helper_connection_is_closed(self):
        with self.db() as conn:
            conn.execute("SELECT 1")
        with self.assertRaises(sqlite3.ProgrammingError):  # closed
            conn.execute("SELECT 1")
