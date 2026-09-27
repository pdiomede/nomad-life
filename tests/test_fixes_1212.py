"""Regression tests for the bugs found in the 1.2.12 review of receipt compression."""
import io
import os
import re
import resource
import threading
import zipfile

from PIL import Image

import app as appmod
from tests.helpers import AppTestCase


def png(img):
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


class ReceiptFixTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()
        self.new_year(2026)
        self.folder = os.path.join(self.app.config["UPLOAD_DIR"], "1")

    def doc(self):
        with self.db() as conn:
            return conn.execute("SELECT * FROM documents ORDER BY id DESC").fetchone()

    def member(self, row):
        with zipfile.ZipFile(os.path.join(self.folder, row["stored_name"])) as zf:
            return zf.namelist()[0], zf.read(zf.namelist()[0])

    def test_parallel_writes_of_one_receipt_never_corrupt_it(self):
        os.makedirs(self.folder, exist_ok=True)
        path = os.path.join(self.folder, "same.zip")
        body = os.urandom(300_000)
        errors = []

        def write(i):
            try:
                appmod.write_receipt_zip(path, f"name-{i}.pdf", body)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        for _ in range(10):
            threads = [threading.Thread(target=write, args=(i,)) for i in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            with zipfile.ZipFile(path) as zf:
                self.assertIsNone(zf.testzip())
                self.assertEqual(zf.read(zf.namelist()[0]), body)
        self.assertEqual(errors, [])
        self.assertEqual([n for n in os.listdir(self.folder) if n.endswith(".tmp")], [])

    def test_parallel_renames_leave_name_and_zip_agreeing(self):
        self.upload("/year/2026/base", "Hotel.pdf", b"%PDF-1.7\n" + os.urandom(200_000))
        row = self.doc()

        def rename(i):
            client = self.app.test_client()
            client.post("/login", data={"email": "a@example.com", "password": "password1"})
            client.post(f"/documents/{row['id']}/edit", data={"name": f"Hotel {i}",
                                                              "kind": "other"})

        threads = [threading.Thread(target=rename, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        row = self.doc()
        name, _data = self.member(row)
        self.assertEqual(name, row["original_name"])
        self.assertEqual(row["size"], os.path.getsize(os.path.join(self.folder,
                                                                   row["stored_name"])))

    def test_sixteen_bit_grey_keeps_its_brightness(self):
        # A 16 bit scan with mid grey noise (a smooth gradient would stay smaller as PNG).
        grey = Image.effect_noise((4000, 3000), 30).convert("I").point(lambda v: v * 257)
        with self.app.app_context():
            out = appmod.shrink_photo(png(grey), "png")
        self.assertIsNotNone(out)
        with Image.open(io.BytesIO(out)) as img:
            mean = sum(img.convert("L").getdata()) / (img.width * img.height)
        self.assertAlmostEqual(mean, 127.5, delta=5)

    def test_a_tiny_file_with_huge_dimensions_is_not_decoded(self):
        huge = png(Image.new("P", (7000, 7000)))  # 49 megapixels in a few KB
        before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        with self.app.app_context():
            self.assertIsNone(appmod.shrink_photo(huge, "png"))
        grown_mb = (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - before) / 1024
        self.assertLess(grown_mb, 100)
        self.assertLessEqual(appmod.PHOTO_MAX_PIXELS, 24_000_000)
        self.assertEqual(appmod.IMAGE_SLOTS._initial_value, 1)

    def test_long_screenshot_stays_readable(self):
        shot = Image.effect_noise((1170, 9000), 12).convert("L")  # fits the 10 MB limit
        out = io.BytesIO()
        shot.save(out, "PNG")
        self.assertGreater(len(out.getvalue()), 1024 * 1024)
        self.upload("/year/2026/base", "Hotel bill.png", out.getvalue())
        _name, data = self.member(self.doc())
        with Image.open(io.BytesIO(data)) as img:
            self.assertGreaterEqual(min(img.size), 1000)
        self.assertEqual(appmod.photo_target(1170, 9000), (1000, 7692))
        self.assertEqual(appmod.photo_target(4000, 3000), (2309, 1732))
        self.assertEqual(appmod.photo_target(1500, 1000), (1500, 1000))

    def test_colour_key_transparency_becomes_white(self):
        img = Image.effect_noise((3000, 2000), 40).convert("RGB")
        img.paste((0, 0, 0), (0, 0, 400, 400))
        out = io.BytesIO()
        img.save(out, "PNG", transparency=(0, 0, 0))
        with self.app.app_context():
            data = appmod.shrink_photo(out.getvalue(), "png")
        self.assertIsNotNone(data)
        with Image.open(io.BytesIO(data)) as shrunk:
            self.assertGreater(min(shrunk.convert("RGB").getpixel((5, 5))), 240)

    def test_download_name_never_empty(self):
        self.upload("/year/2026/base", "report.pdf", b"%PDF-1.7\n x")
        row = self.doc()
        with self.db() as conn:
            conn.execute("UPDATE documents SET original_name = '.pdf'")
        resp = self.client.get(f"/documents/{row['id']}")
        self.assertIn("filename=receipt.zip", resp.headers["Content-Disposition"])

    def test_member_names_extract_on_windows(self):
        self.assertEqual(appmod.zip_member_name('Invoice: March? "A"<1>|*.pdf', "pdf"),
                         "Invoice- March- -A--1---.pdf")

    def test_damaged_receipt_is_listed_missing_not_breaking_the_package(self):
        self.set_plan("pro")
        self.upload("/year/2026/base", "Good.pdf", b"%PDF-1.7\n" + os.urandom(3000))
        self.upload("/year/2026/base", "Bad.pdf", b"%PDF-1.7\n" + os.urandom(3000))
        bad = self.doc()
        path = os.path.join(self.folder, bad["stored_name"])
        raw = bytearray(open(path, "rb").read())
        raw[100] ^= 0xFF  # one flipped byte in the stored data
        with open(path, "wb") as fh:
            fh.write(raw)
        resp = self.client.get("/year/2026/package")
        with zipfile.ZipFile(io.BytesIO(resp.data)) as zf:
            self.assertIsNone(zf.testzip())
            names = zf.namelist()
            self.assertTrue(any(n.endswith("Good.pdf") for n in names))
            self.assertFalse(any(n.endswith("Bad.pdf") for n in names))
            readme = zf.read([n for n in names if n.endswith("README.txt")][0]).decode()
        self.assertIn("Bad.pdf", readme)  # listed as missing


class SecretKeyFixTests(AppTestCase):
    def test_an_empty_key_file_is_replaced(self):
        path = os.path.join(self.tmp, "keys", ".secret_key")
        os.makedirs(os.path.dirname(path))
        open(path, "w").close()
        key = appmod.load_or_create_secret(path)
        self.assertRegex(key, r"^[0-9a-f]{64}$")
        self.assertEqual(appmod.load_or_create_secret(path), key)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        self.assertEqual([n for n in os.listdir(os.path.dirname(path)) if n.endswith(".tmp")], [])


class ZipCommandFixTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()
        self.new_year(2026)
        self.folder = os.path.join(self.app.config["UPLOAD_DIR"], "1")
        os.makedirs(self.folder, exist_ok=True)
        with open(os.path.join(self.folder, "old.pdf"), "wb") as fh:
            fh.write(b"%PDF-1.4\n" + b"x" * 5000)
        with self.db() as conn:
            year_id = conn.execute("SELECT id FROM years").fetchone()[0]
            conn.execute("INSERT INTO documents (user_id, year_id, kind, original_name, "
                         "stored_name, mime, size, format, file_size) VALUES (1, ?, 'other', "
                         "'Rent March.pdf', 'old.pdf', 'application/pdf', 5009, 'pdf', 5009)",
                         (year_id,))

    def run_with(self, during):
        real = appmod.write_receipt_zip

        def slow_write(*args):
            size = real(*args)
            during()
            return size

        appmod.write_receipt_zip = slow_write
        try:
            return self.app.test_cli_runner().invoke(args=["zip-receipts"])
        finally:
            appmod.write_receipt_zip = real

    def test_a_rename_during_the_command_is_kept(self):
        def rename():
            with self.db() as conn:
                conn.execute("UPDATE documents SET original_name = 'Rent April.pdf'")

        result = self.run_with(rename)
        self.assertIn("1 changed meanwhile", result.output)
        with self.db() as conn:
            row = conn.execute("SELECT original_name, stored_name FROM documents").fetchone()
        self.assertEqual(tuple(row), ("Rent April.pdf", "old.pdf"))
        self.assertEqual(os.listdir(self.folder), ["old.pdf"])  # no orphan ZIP
        again = self.app.test_cli_runner().invoke(args=["zip-receipts"])
        self.assertIn("Converted 1 receipt", again.output)

    def test_a_delete_during_the_command_leaves_no_orphan(self):
        def delete():
            with self.db() as conn:
                conn.execute("DELETE FROM documents")
            os.remove(os.path.join(self.folder, "old.pdf"))

        result = self.run_with(delete)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(os.listdir(self.folder), [])


class GuideTests(AppTestCase):
    def test_backup_script_tolerates_changing_uploads(self):
        guide = open(os.path.join(appmod.BASE_DIR, "setup_vps.md"), encoding="utf-8").read()
        tar = re.search(r"tar -C /srv/nomad-life.*?\n(.*?)\n", guide, re.S).group(0)
        for flag in ("--exclude='*.tmp'", "--warning=no-file-changed",
                     "--warning=no-file-removed", "|| [ $? -eq 1 ]"):
            self.assertIn(flag, tar)
        self.assertNotIn("150 MB", guide)
