"""scripts/backup.py: one archive with the database, the secret key and the receipts."""
import contextlib
import importlib.util
import io
import os
import sqlite3
import stat
import tarfile
import tempfile
import unittest

import app as appmod

spec = importlib.util.spec_from_file_location(
    "backup", os.path.join(appmod.BASE_DIR, "scripts", "backup.py"))
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.data = os.path.join(self.tmp, "data")
        self.uploads = os.path.join(self.tmp, "uploads")
        self.dest = os.path.join(self.tmp, "backup")
        os.makedirs(self.data)
        os.makedirs(os.path.join(self.uploads, "1"))
        self.db = os.path.join(self.data, "nomad.db")
        with contextlib.closing(sqlite3.connect(self.db)) as conn:
            conn.execute("CREATE TABLE users (email TEXT)")
            conn.execute("INSERT INTO users VALUES ('ada@example.com')")
            conn.commit()
        with open(os.path.join(self.data, ".secret_key"), "w") as fh:
            fh.write("k" * 64)
        with open(os.path.join(self.uploads, "1", "a.zip"), "wb") as fh:
            fh.write(b"receipt")
        with open(os.path.join(self.uploads, "1", "b.zip.tmp"), "wb") as fh:
            fh.write(b"half written")
        self.config = os.path.join(self.tmp, "config.env")
        with open(self.config, "w") as fh:
            fh.write(f"DATABASE_PATH={self.db}\nUPLOAD_DIR={self.uploads}\n")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_backup(self, *extra):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = backup.main(["--dest", self.dest, "--config", self.config, *extra])
        return code, out.getvalue()

    def archives(self):
        return sorted(n for n in os.listdir(self.dest) if backup.ARCHIVE.match(n))

    def test_archive_holds_database_key_and_receipts(self):
        code, out = self.run_backup()
        self.assertEqual(code, 0, out)
        [name] = self.archives()
        path = os.path.join(self.dest, name)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        with tarfile.open(path) as tar:
            self.assertEqual(sorted(tar.getnames()),
                             [".secret_key", "nomad.db", "uploads/1/a.zip"])
            self.assertEqual(tar.extractfile("uploads/1/a.zip").read(), b"receipt")
            restored = os.path.join(self.tmp, "restored.db")
            with open(restored, "wb") as fh:
                fh.write(tar.extractfile("nomad.db").read())
        with contextlib.closing(sqlite3.connect(restored)) as conn:
            self.assertEqual(conn.execute("SELECT email FROM users").fetchall(),
                             [("ada@example.com",)])
        self.assertEqual([n for n in os.listdir(self.dest) if n.endswith(".partial")], [])

    def test_keeps_only_the_newest_and_leaves_other_files(self):
        os.makedirs(self.dest)
        old = [f"nomadlife-2026-01-0{d}_030000.tar.gz" for d in range(1, 5)]
        for name in old + ["notes.txt"]:
            open(os.path.join(self.dest, name), "w").close()
        code, out = self.run_backup("--keep", "2")
        self.assertEqual(code, 0, out)
        kept = self.archives()
        self.assertEqual(len(kept), 2)
        self.assertEqual(kept[0], old[-1])
        self.assertTrue(os.path.exists(os.path.join(self.dest, "notes.txt")))

    def test_missing_database_fails_without_creating_one(self):
        os.remove(self.db)
        code, out = self.run_backup()
        self.assertEqual(code, 1)
        self.assertIn("no database", out)
        self.assertFalse(os.path.exists(self.db))
        self.assertEqual(self.archives() if os.path.isdir(self.dest) else [], [])

    def test_receipt_removed_while_running_is_skipped(self):
        real_walk = os.walk

        def walk_with_a_vanished_file(top):
            for folder, dirs, files in real_walk(top):
                yield folder, dirs, files + (["gone.zip"] if folder.endswith("1") else [])

        backup.os.walk = walk_with_a_vanished_file
        try:
            code, out = self.run_backup()
        finally:
            backup.os.walk = real_walk
        self.assertEqual(code, 0, out)
        with tarfile.open(os.path.join(self.dest, self.archives()[0])) as tar:
            self.assertEqual(sorted(tar.getnames()),
                             [".secret_key", "nomad.db", "uploads/1/a.zip"])

    def test_relative_paths_start_at_the_app_folder(self):
        self.assertEqual(backup.resolve("data/nomad.db"),
                         os.path.join(appmod.BASE_DIR, "data", "nomad.db"))
