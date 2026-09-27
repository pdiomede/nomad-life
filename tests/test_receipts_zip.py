"""1.2.12: receipts stored as one file ZIPs, big photos shrunk, zip-receipts, secret key race."""
import hashlib
import io
import os
import sqlite3
import threading
import zipfile

from PIL import Image

import app as appmod
from tests.helpers import AppTestCase


def photo(fmt="JPEG", size=(4000, 3000), exif=None, noise=True):
    """A photo like a phone takes: noisy (so it does not compress), optionally with EXIF."""
    img = (Image.effect_noise(size, 60).convert("RGB") if noise
           else Image.new("RGB", size, "white"))
    out = io.BytesIO()
    img.save(out, fmt, **({"exif": exif} if exif is not None else {}))
    return out.getvalue()


class ZipStorageTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()
        self.new_year(2026)

    def doc(self):
        with self.db() as conn:
            return conn.execute("SELECT * FROM documents ORDER BY id DESC").fetchone()

    def stored(self, row):
        return os.path.join(self.app.config["UPLOAD_DIR"], str(row["user_id"]), row["stored_name"])

    def test_pdf_is_stored_as_a_zip_in_the_users_folder(self):
        body = b"%PDF-1.7\n" + b"hello receipt " * 1000
        self.upload("/year/2026/base", "Flight to Bali.pdf", body)
        row = self.doc()
        self.assertTrue(row["stored_name"].endswith(".zip"))
        path = self.stored(row)
        self.assertEqual(os.path.dirname(path), os.path.join(self.app.config["UPLOAD_DIR"], "1"))
        self.assertEqual(row["size"], os.path.getsize(path))  # quotas count the ZIP on disk
        self.assertLess(row["size"], len(body))
        self.assertEqual((row["format"], row["file_size"], row["mime"]),
                         ("pdf", len(body), "application/pdf"))
        with zipfile.ZipFile(path) as zf:
            self.assertEqual(zf.namelist(), ["Flight to Bali.pdf"])
            self.assertEqual(zf.read("Flight to Bali.pdf"), body)

    def test_download_is_a_zip_with_the_receipt_inside(self):
        body = b"%PDF-1.7\n" + os.urandom(2000)
        self.upload("/year/2026/base", "Hotel.pdf", body)
        resp = self.client.get(f"/documents/{self.doc()['id']}")
        self.assertEqual(resp.mimetype, "application/zip")
        self.assertIn('attachment; filename=Hotel.zip', resp.headers["Content-Disposition"])
        self.assertEqual(resp.headers["Cache-Control"], "private, no-store")
        with zipfile.ZipFile(io.BytesIO(resp.data)) as zf:
            self.assertEqual(zf.read("Hotel.pdf"), body)
        html = self.client.get("/year/2026/base").get_data(as_text=True)
        self.assertIn("Download ZIP", html)
        doc_list = html.split('class="doc-list"', 1)[1].split("</ul>", 1)[0]
        self.assertNotIn('target="_blank"', doc_list)  # no preview: every link downloads

    def test_incompressible_files_are_stored_not_deflated(self):
        self.upload("/year/2026/base", "scan.pdf", b"%PDF-1.7\n" + os.urandom(50000))
        with zipfile.ZipFile(self.stored(self.doc())) as zf:
            self.assertEqual(zf.infolist()[0].compress_type, zipfile.ZIP_STORED)

    def test_big_photo_is_resized_to_a_jpeg_without_location(self):
        exif = Image.Exif()
        exif[0x0112] = 6  # rotate 90 degrees when shown
        exif[0x8825] = {1: "N", 2: (45.0, 0.0, 0.0)}  # GPS
        self.upload("/year/2026/base", "Dinner.jpeg", photo("JPEG", (4000, 3000),
                                                             exif=exif.tobytes()))
        row = self.doc()
        self.assertEqual((row["original_name"], row["format"], row["mime"]),
                         ("Dinner.jpg", "jpg", "image/jpeg"))
        with zipfile.ZipFile(self.stored(row)) as zf:
            data = zf.read("Dinner.jpg")
        with Image.open(io.BytesIO(data)) as img:
            self.assertEqual(img.format, "JPEG")
            self.assertEqual(img.size, (1732, 2309))  # upright, at most 4 megapixels
            self.assertNotIn(0x8825, img.getexif())
            self.assertNotIn(0x0112, img.getexif())

    def test_big_png_with_transparency_becomes_a_white_jpeg(self):
        img = Image.new("RGBA", (4000, 2000), (0, 0, 0, 0))
        out = io.BytesIO()
        img.save(out, "PNG")
        self.upload("/year/2026/base", "logo.png", out.getvalue())
        row = self.doc()
        self.assertEqual(row["original_name"], "logo.jpg")
        with zipfile.ZipFile(self.stored(row)) as zf, \
                Image.open(io.BytesIO(zf.read("logo.jpg"))) as shrunk:
            self.assertEqual(shrunk.size, (2828, 1414))
            self.assertGreater(shrunk.convert("L").getpixel((10, 10)), 240)

    def test_small_photos_are_kept_as_they_are(self):
        small = photo("PNG", (800, 600), noise=False)
        self.upload("/year/2026/base", "small.png", small)
        row = self.doc()
        self.assertEqual((row["original_name"], row["format"]), ("small.png", "png"))
        with zipfile.ZipFile(self.stored(row)) as zf:
            self.assertEqual(zf.read("small.png"), small)

    def test_unreadable_or_huge_images_keep_the_original(self):
        broken = b"\x89PNG\r\n\x1a\n" + os.urandom(2 * 1024 * 1024)
        with self.app.app_context():
            self.assertIsNone(appmod.shrink_photo(broken, "png"))
            old = appmod.PHOTO_MAX_PIXELS
            appmod.PHOTO_MAX_PIXELS = 1000
            try:
                self.assertIsNone(appmod.shrink_photo(photo("PNG", (3000, 3000), noise=False),
                                                      "png"))
            finally:
                appmod.PHOTO_MAX_PIXELS = old
        self.upload("/year/2026/base", "odd.png", broken)
        self.assertEqual(self.doc()["original_name"], "odd.png")

    def test_quota_counts_the_zip_so_a_big_photo_that_shrinks_fits(self):
        big = photo("JPEG", (4000, 3000))
        self.app.config["USER_QUOTA_BYTES"] = len(big) // 2  # the original would not fit
        resp = self.upload("/year/2026/base", "big.jpg", big)
        self.assertEqual(resp.status_code, 302)
        row = self.doc()
        self.assertIsNotNone(row)
        self.assertLess(row["size"], len(big) // 2)

    def test_rename_rewrites_the_name_inside(self):
        self.upload("/year/2026/base", "old.pdf", b"%PDF-1.7\n x")
        row = self.doc()
        self.client.post(f"/documents/{row['id']}/edit", data={"name": "Lease 2026",
                                                               "kind": "other"})
        row = self.doc()
        self.assertEqual(row["original_name"], "Lease 2026.pdf")
        with zipfile.ZipFile(self.stored(row)) as zf:
            self.assertEqual(zf.namelist(), ["Lease 2026.pdf"])
        self.assertEqual(row["size"], os.path.getsize(self.stored(row)))

    def test_delete_removes_the_zip(self):
        self.upload("/year/2026/base", "r.pdf", b"%PDF-1.7\n x")
        row = self.doc()
        path = self.stored(row)
        self.client.post(f"/documents/{row['id']}/delete", data={"confirm_delete": "2"})
        self.assertFalse(os.path.exists(path))

    def test_package_holds_the_receipts_not_zips(self):
        self.set_plan("pro")
        body = b"%PDF-1.7\n" + os.urandom(3000)
        self.upload("/year/2026/base", "Lease.pdf", body, "rental_contract")
        with zipfile.ZipFile(io.BytesIO(self.client.get("/year/2026/package").data)) as zf:
            names = [n for n in zf.namelist() if "/receipts/" in n]
            self.assertEqual(len(names), 1)
            self.assertTrue(names[0].endswith(".pdf"))
            self.assertEqual(zf.read(names[0]), body)
            manifest = zf.read([n for n in zf.namelist() if n.endswith("manifest.csv")][0])
            self.assertIn(hashlib.sha256(body).hexdigest().encode(), manifest)


class LegacyTests(AppTestCase):
    """Receipts stored before 1.2.12 as plain files."""

    def setUp(self):
        super().setUp()
        self.signup()
        self.new_year(2026)
        self.folder = os.path.join(self.app.config["UPLOAD_DIR"], "1")
        os.makedirs(self.folder, exist_ok=True)
        self.pdf = b"%PDF-1.4\n" + b"legacy " * 500
        self.jpg = photo("JPEG", (3000, 2000))
        with open(os.path.join(self.folder, "aaa.pdf"), "wb") as fh:
            fh.write(self.pdf)
        with open(os.path.join(self.folder, "bbb.jpg"), "wb") as fh:
            fh.write(self.jpg)
        with self.db() as conn:
            year_id = conn.execute("SELECT id FROM years").fetchone()[0]
            for name, stored, mime, size, fmt in (
                    ("Old lease.pdf", "aaa.pdf", "application/pdf", len(self.pdf), "pdf"),
                    ("Old photo.jpg", "bbb.jpg", "image/jpeg", len(self.jpg), "jpg")):
                conn.execute("INSERT INTO documents (user_id, year_id, kind, original_name, "
                             "stored_name, mime, size, format, file_size) VALUES "
                             "(1, ?, 'other', ?, ?, ?, ?, ?, ?)",
                             (year_id, name, stored, mime, size, fmt, size))

    def test_legacy_files_download_as_zip(self):
        resp = self.client.get("/documents/1")
        self.assertEqual(resp.mimetype, "application/zip")
        with zipfile.ZipFile(io.BytesIO(resp.data)) as zf:
            self.assertEqual(zf.read("Old lease.pdf"), self.pdf)

    def test_zip_receipts_converts_once(self):
        runner = self.app.test_cli_runner()
        result = runner.invoke(args=["zip-receipts"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Converted 2 receipts to ZIP", result.output)
        self.assertEqual(sorted(os.listdir(self.folder)),
                         sorted(r["stored_name"] for r in self.rows()))
        rows = {r["id"]: r for r in self.rows()}
        self.assertTrue(all(r["stored_name"].endswith(".zip") for r in rows.values()))
        self.assertEqual(rows[2]["original_name"], "Old photo.jpg")
        self.assertLess(rows[2]["size"], len(self.jpg))  # the photo was shrunk
        with zipfile.ZipFile(os.path.join(self.folder, rows[1]["stored_name"])) as zf:
            self.assertEqual(zf.read("Old lease.pdf"), self.pdf)
        again = runner.invoke(args=["zip-receipts"])
        self.assertIn("Converted 0 receipts", again.output)

    def test_zip_receipts_no_shrink_keeps_photos(self):
        self.app.test_cli_runner().invoke(args=["zip-receipts", "--no-shrink"])
        row = [r for r in self.rows() if r["id"] == 2][0]
        with zipfile.ZipFile(os.path.join(self.folder, row["stored_name"])) as zf:
            self.assertEqual(zf.read("Old photo.jpg"), self.jpg)

    def rows(self):
        with self.db() as conn:
            return conn.execute("SELECT * FROM documents ORDER BY id").fetchall()


class MigrationAndSecretTests(AppTestCase):
    def test_old_documents_get_format_and_file_size(self):
        path = os.path.join(self.tmp, "old.db")
        conn = sqlite3.connect(path)
        conn.executescript("""CREATE TABLE documents (id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL, year_id INTEGER NOT NULL, movement_id INTEGER,
            kind TEXT NOT NULL, original_name TEXT NOT NULL, stored_name TEXT NOT NULL,
            mime TEXT NOT NULL, size INTEGER NOT NULL,
            uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            INSERT INTO documents (user_id, year_id, kind, original_name, stored_name, mime, size)
            VALUES (1, 1, 'other', 'a.PDF', 'abc.PDF', 'application/pdf', 123);""")
        conn.commit()
        conn.close()
        appmod.create_app({"TESTING": True, "SECRET_KEY": "k", "DATABASE_PATH": path,
                           "UPLOAD_DIR": os.path.join(self.tmp, "up-old"), "PWNED_CHECK": False})
        conn = sqlite3.connect(path)
        self.assertEqual(conn.execute("SELECT format, file_size FROM documents").fetchone(),
                         ("pdf", 123))
        conn.close()

    def test_parallel_first_start_shares_one_secret(self):
        path = os.path.join(self.tmp, "race", ".secret_key")
        os.makedirs(os.path.dirname(path))
        keys, barrier = [], threading.Barrier(8)

        def start():
            barrier.wait()
            keys.append(appmod.load_or_create_secret(path))

        threads = [threading.Thread(target=start) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(set(keys)), 1)
        with open(path) as fh:
            self.assertEqual(fh.read(), keys[0])
