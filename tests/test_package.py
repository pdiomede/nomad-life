"""Tests for the annual accountant package (package.py and the /year/<year>/package route)."""
import hashlib
import os
import shutil
import subprocess
import tracemalloc
import unicodedata
import unittest
import zipfile
from datetime import date
from unittest import mock

import app as appmod
import db
import package
from tests.helpers import AppTestCase


class PackageContentsTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()
        self.year = self.new_year(2026)
        # Nested side trip (Singapore inside Canggu) and a shared travel day (Tokyo -> Seoul).
        self.m1 = self.add_movement(2026, "Canggu", "Indonesia", "2026-02-01", "2026-03-31", "coworking")
        self.m2 = self.add_movement(2026, "Singapore", "SG", "2026-02-10", "2026-02-15")
        self.m3 = self.add_movement(2026, "Tokyo", "Japan", "2026-05-10", "2026-06-05")
        self.m4 = self.add_movement(2026, "Seoul", "south korea", "2026-06-05", "2026-06-12")
        self.lease = os.urandom(3000)
        self.ticket = os.urandom(5000)
        self.upload("/year/2026/base", "Lease 2026.pdf", self.lease, "rental_contract")
        self.upload(f"/movements/{self.m1}", "Flight to Bali.pdf", self.ticket, "flight")

    def stats(self):
        with self.app.app_context():
            year_row = db.query("SELECT * FROM years WHERE id = ?", (self.year["id"],), one=True)
            movements = db.query("SELECT * FROM movements WHERE year_id = ? ORDER BY start_date, "
                                 "end_date DESC, id", (self.year["id"],))
            return appmod.compute_stats(year_row, movements), movements

    def test_structure_and_order(self):
        _, zf = self.package(2026)
        self.assertIsNone(zf.testzip())
        root = "NomadLife-2026-accountant-package"
        names = zf.namelist()
        self.assertEqual(names[:4], [f"{root}/README.txt", f"{root}/summary.pdf",
                                     f"{root}/timeline.csv", f"{root}/country-totals.csv"])
        self.assertEqual(names[-1], f"{root}/manifest.csv")
        self.assertIn(f"{root}/receipts/base/rental-contract_Lease-2026.pdf", names)
        self.assertIn(f"{root}/receipts/01_2026-02-01_Canggu_Indonesia/flight-ticket_Flight-to-Bali.pdf", names)
        self.assertTrue(zf.read(f"{root}/summary.pdf").startswith(b"%PDF"))

    def test_totals_match_compute_stats(self):
        stats, movements = self.stats()
        _, zf = self.package(2026)
        totals = self.read_csv(zf, "country-totals.csv")
        self.assertEqual(sum(int(r["days"]) for r in totals), 365)
        self.assertEqual({r["country"]: int(r["days"]) for r in totals},
                         {r["country"]: r["days"] for r in stats["rows"]})
        self.assertEqual({r["country"]: int(r["so_far"]) for r in totals},
                         {r["country"]: r["so_far"] for r in stats["rows"]})
        timeline = self.read_csv(zf, "timeline.csv")
        self.assertEqual([int(r["counted_days"]) for r in timeline],
                         [stats["counted"].get(m["id"], 0) for m in movements])
        by_city = {r["city"]: r for r in timeline}
        self.assertEqual((by_city["Canggu"]["calendar_days"], by_city["Canggu"]["counted_days"]), ("59", "53"))
        self.assertEqual((by_city["Tokyo"]["calendar_days"], by_city["Tokyo"]["counted_days"]), ("27", "26"))
        self.assertEqual(by_city["Seoul"]["country"], "South Korea")
        self.assertEqual(by_city["Seoul"]["iso_code"], "KR")
        # Counted days add up to the days covered by at least one stay.
        covered = set()
        for m in movements:
            d, end = date.fromisoformat(m["start_date"]), date.fromisoformat(m["end_date"])
            while d <= end:
                covered.add(d)
                d = d.fromordinal(d.toordinal() + 1)
        self.assertEqual(sum(int(r["counted_days"]) for r in timeline), len(covered))

    def test_leap_year_adds_up_to_366(self):
        self.new_year(2028)
        self.add_movement(2028, "Rome", "Italy", "2028-02-28", "2028-03-01")
        _, zf = self.package(2028)
        totals = self.read_csv(zf, "country-totals.csv")
        self.assertEqual(sum(int(r["days"]) for r in totals), 366)
        self.assertEqual({r["country"]: int(r["days"]) for r in totals}["Italy"], 3)

    def test_receipts_are_byte_identical(self):
        _, zf = self.package(2026)
        manifest = {r["path"]: r for r in self.read_csv(zf, "manifest.csv")}
        root = self.root(zf)
        for path, original in [(f"{root}/receipts/base/rental-contract_Lease-2026.pdf", self.lease),
                               (f"{root}/receipts/01_2026-02-01_Canggu_Indonesia/flight-ticket_Flight-to-Bali.pdf", self.ticket)]:
            self.assertEqual(zf.read(path), original)
            self.assertEqual(manifest[path]["sha256"], hashlib.sha256(original).hexdigest())
            self.assertEqual(int(manifest[path]["size_bytes"]), len(original))
            self.assertEqual(manifest[path]["status"], "ok")
        # Every file in the ZIP (except the manifest itself) is listed with a matching checksum.
        for name in zf.namelist()[:-1]:
            self.assertEqual(manifest[name]["sha256"], hashlib.sha256(zf.read(name)).hexdigest())

    def test_receipts_grouped_by_stay(self):
        self.upload(f"/movements/{self.m3}", "Tokyo hotel.pdf", b"%PDF a", "accommodation")
        self.upload(f"/movements/{self.m1}", "Bali villa.pdf", b"%PDF b", "accommodation")
        self.upload(f"/movements/{self.m3}", "Tokyo train.pdf", b"%PDF c", "other")
        _, zf = self.package(2026)
        folders = [r["path"].split("/")[2] for r in self.read_csv(zf, "manifest.csv")
                   if "/receipts/" in r["path"]]
        self.assertEqual(folders, sorted(folders, key=lambda f: (f != "base", f)))

    def test_long_base_names_render(self):
        with self.db() as conn:
            conn.execute("UPDATE years SET base_city = ?, base_country = ? WHERE id = ?",
                         ("Santa Cruz de la Sierra and the surrounding metropolitan area near "
                          "the airport district", "Democratic Republic of the Congo", self.year["id"]))
        _, zf = self.package(2026)
        self.assertTrue(zf.read(f"{self.root(zf)}/summary.pdf").startswith(b"%PDF"))

    def test_notes_only_when_requested(self):
        _, zf = self.package(2026)
        rows = self.read_csv(zf, "timeline.csv")
        self.assertNotIn("notes", rows[0])
        self.assertNotIn(b"coworking", b"".join(zf.read(n) for n in zf.namelist()[:4]))
        _, zf = self.package(2026, notes=True)
        self.assertEqual(self.read_csv(zf, "timeline.csv")[0]["notes"], "coworking")

    def test_csv_formula_injection_is_neutralized(self):
        for city, notes in [("=HYPERLINK(\"http://evil\")", "+1+1"), ("@SUM(A1)", "-2+3"),
                            ("\tTab", "=cmd|' /C calc'!A0")]:
            self.add_movement(2026, city, "France", "2026-09-01", "2026-09-02", notes)
        _, zf = self.package(2026, notes=True)
        rows = self.read_csv(zf, "timeline.csv")
        for row in rows:
            for value in row.values():
                self.assertFalse(value[:1] in ("=", "+", "-", "@", "\t", "\r"), value)
        cities = {r["city"] for r in rows}
        self.assertIn("'=HYPERLINK(\"http://evil\")", cities)
        self.assertIn("'@SUM(A1)", cities)

    def test_missing_file_is_reported_not_fatal(self):
        with self.db() as conn:
            stored = conn.execute("SELECT stored_name FROM documents WHERE kind = 'flight'").fetchone()[0]
        os.remove(os.path.join(self.app.config["UPLOAD_DIR"], str(self.year["user_id"]), stored))
        _, zf = self.package(2026)
        self.assertIsNone(zf.testzip())
        root = self.root(zf)
        self.assertNotIn(f"{root}/receipts/01_2026-02-01_Canggu_Indonesia/flight-ticket_Flight-to-Bali.pdf", zf.namelist())
        missing = [r for r in self.read_csv(zf, "manifest.csv") if r["status"] == "missing"]
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]["sha256"], "")
        self.assertIn("Flight to Bali.pdf", zf.read(f"{root}/README.txt").decode("utf-8"))

    def test_response_headers(self):
        resp, _ = self.package(2026)
        self.assertEqual(resp.mimetype, "application/zip")
        self.assertEqual(resp.headers["Content-Disposition"],
                         'attachment; filename="NomadLife-2026-accountant-package.zip"')
        self.assertEqual(resp.headers["Cache-Control"], "private, no-store")
        self.assertEqual(resp.headers["X-Content-Type-Options"], "nosniff")

    def test_download_token_cookie(self):
        resp = self.client.get("/year/2026/package?dl=abc123XYZ9")
        self.assertIn("nl_download=abc123XYZ9", resp.headers.get("Set-Cookie", ""))
        resp = self.client.get("/year/2026/package?dl=<script>")
        self.assertNotIn("nl_download", resp.headers.get("Set-Cookie", ""))

    def test_package_is_not_stored(self):
        before = self.db().execute("SELECT count(*), coalesce(sum(size), 0) FROM documents").fetchone()
        files_before = sorted(os.listdir(os.path.join(self.app.config["UPLOAD_DIR"], str(self.year["user_id"]))))
        self.package(2026)
        after = self.db().execute("SELECT count(*), coalesce(sum(size), 0) FROM documents").fetchone()
        self.assertEqual(tuple(before), tuple(after))
        self.assertEqual(files_before, sorted(os.listdir(os.path.join(self.app.config["UPLOAD_DIR"], str(self.year["user_id"])))))

    def test_dashboard_shows_package_card(self):
        html = self.client.get("/year/2026").data.decode()
        self.assertIn("Accountant package", html)
        self.assertIn('action="/year/2026/package"', html)
        self.assertIn('name="notes"', html)


class PackageSafetyTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()
        self.year = self.new_year(2026)
        self.m1 = self.add_movement(2026, "Tokyo", "Japan", "2026-05-10", "2026-06-05")

    def test_unicode_and_hostile_names(self):
        names = ["領収書.pdf", "Réçu été.pdf", "../../etc/passwd.pdf", "..\\..\\evil.pdf",
                 "a/../../b.pdf", "CON.pdf", "con.pdf", "same.pdf", "SAME.pdf", "x\x00y\x1fz.pdf",
                 "file:name?*|<>.pdf", "   ", ".", "..", "éte.pdf"]
        for name in names:
            self.insert_document(self.year, name, b"%PDF-1.4 " + name.encode("utf-8", "replace"),
                                 movement_id=self.m1)
        _, zf = self.package(2026)
        root = self.root(zf)
        receipts = [n for n in zf.namelist() if "/receipts/" in n]
        self.assertEqual(len(receipts), len(names))
        seen = set()
        for name in zf.namelist():
            parts = name.split("/")
            self.assertEqual(parts[0], root)
            self.assertNotIn("..", parts)
            self.assertNotIn("\\", name)
            self.assertFalse(any(unicodedata.category(ch)[0] == "C" for ch in name), name)
            self.assertFalse(any(ch in name for ch in ':*?"<>|'), name)
            self.assertEqual(name, unicodedata.normalize("NFC", name))
            for part in parts:
                self.assertNotIn(part.split(".")[0].upper(), package.WINDOWS_RESERVED, name)
            self.assertNotIn(name.casefold(), seen, "duplicate entry")
            seen.add(name.casefold())
        joined = "\n".join(receipts)
        self.assertIn("領収書", joined)
        self.assertIn("Réçu-été", joined)
        # Extracting the archive keeps every file inside the target folder.
        target = os.path.join(self.tmp, "extract")
        zf.extractall(target)
        for dirpath, _, files in os.walk(target):
            for f in files:
                self.assertTrue(os.path.realpath(os.path.join(dirpath, f)).startswith(os.path.realpath(target) + os.sep))

    def test_long_names_keep_paths_short(self):
        # Windows cannot extract paths over 260 characters; leave room for the Downloads folder.
        movement = self.add_movement(2026, "Llanfairpwllgwyngyllgogerychwyrndrobwllllantysiliogogogoch",
                                     "South Georgia and the South Sandwich Islands", "2026-08-01", "2026-08-05")
        self.insert_document(self.year, "Booking confirmation for the guesthouse near the old "
                             "train station and the harbour.pdf", b"%PDF x", movement_id=movement,
                             kind="accommodation")
        _, zf = self.package(2026)
        self.assertLessEqual(max(len(n) for n in zf.namelist()), 180)

    def test_zip_is_readable_by_streaming_readers(self):
        # Entries written with a data descriptor must be deflated, or streaming readers such as
        # Java's ZipInputStream stop with "only DEFLATED entries can have EXT descriptor".
        self.insert_document(self.year, "photo.jpg", os.urandom(2048), movement_id=self.m1, ext="jpg")
        self.insert_document(self.year, "scan.pdf", b"%PDF " + os.urandom(2048), movement_id=self.m1)
        _, zf = self.package(2026)
        for info in zf.infolist():
            if info.flag_bits & 0x08:
                self.assertEqual(info.compress_type, zipfile.ZIP_DEFLATED, info.filename)
        self.assertIsNone(zf.testzip())

    def test_clean_segment_and_unique(self):
        self.assertEqual(package.clean_segment("CON"), "_CON")
        self.assertEqual(package.clean_segment("CON", whole=False), "CON")
        self.assertEqual(package.clean_segment(".."), "file")
        self.assertEqual(package.clean_segment("a / b \\ c"), "a-b-c")
        taken = set()
        self.assertEqual(package._unique("x.pdf", taken), "x.pdf")
        self.assertEqual(package._unique("X.pdf", taken), "X-2.pdf")
        self.assertEqual(package._unique("x.pdf", taken), "x-3.pdf")

    def test_fonts_cover_names(self):
        self.assertFalse(package._needs_cjk("Côte d'Ivoire, Türkiye, Réunion, Москва"))
        self.assertTrue(package._needs_cjk("領収書.pdf"))
        self.assertEqual(package.pdf_safe("領収書", cjk=True), "領収書")
        # Thai is in neither bundled font: it becomes "?" in the PDF (the CSV keeps the name).
        self.assertEqual(package.pdf_safe("\u0e01 ok", cjk=True), "? ok")

    def test_csv_safe(self):
        for raw in ["=1+1", "+1", "-1", "@a", "\tx", "\rx"]:
            self.assertTrue(package.csv_safe(raw).startswith("'"), raw)
        for raw in ["Lisbon", "2026-01-01", "12", "Côte d'Ivoire"]:
            self.assertEqual(package.csv_safe(raw), raw)


class PackageAccessTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()
        self.new_year(2026)

    def test_other_user_gets_404(self):
        other = self.app.test_client()
        self.signup("b@example.com", client=other)
        self.assertEqual(other.get("/year/2026/package").status_code, 404)

    def test_anonymous_redirects_to_sign_in(self):
        resp = self.app.test_client().get("/year/2026/package")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])

    def test_permission_hook(self):
        with mock.patch.object(appmod, "can_download_package", return_value=False):
            resp = self.client.get("/year/2026/package", follow_redirects=True)
        self.assertIn(b"not available on your plan", resp.data)
        self.assertNotEqual(resp.mimetype, "application/zip")


class PackageSummaryTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.signup()

    def summary(self, year):
        with self.app.test_request_context():
            year_row = db.query("SELECT * FROM years WHERE year = ?", (year,), one=True)
            return package.summary_text(appmod.build_package_data(year_row, False))

    def test_current_year_shows_so_far_and_projected(self):
        this_year = date.today().year
        self.new_year(this_year)
        text = self.summary(this_year)
        self.assertIn(f"So far (as of {date.today().isoformat()})", text)
        self.assertIn("Projected full year", text)
        self.assertNotIn("Final numbers", text)

    def test_past_year_shows_final_numbers_only(self):
        last_year = date.today().year - 1
        self.new_year(last_year)
        text = self.summary(last_year)
        self.assertIn(f"Final numbers for {last_year}", text)
        self.assertNotIn("So far", text)
        self.assertNotIn("Projected", text)

    def test_future_year_is_projected(self):
        next_year = date.today().year + 1
        self.new_year(next_year)
        self.assertIn("has not started yet", self.summary(next_year))

    def test_empty_year_package(self):
        self.new_year(2026)
        _, zf = self.package(2026)
        self.assertEqual(self.read_csv(zf, "timeline.csv"), [])
        self.assertEqual(len(self.read_csv(zf, "country-totals.csv")), 1)
        text = self.summary(2026)
        self.assertIn("No stays recorded", text)
        self.assertIn("No receipts uploaded", text)

    @unittest.skipUnless(shutil.which("pdftotext"), "pdftotext not installed")
    def test_pdf_text(self):
        self.new_year(2026)
        _, zf = self.package(2026)
        pdf = os.path.join(self.tmp, "summary.pdf")
        with open(pdf, "wb") as fh:
            fh.write(zf.read(f"{self.root(zf)}/summary.pdf"))
        text = subprocess.run(["pdftotext", pdf, "-"], capture_output=True, text=True).stdout
        self.assertIn("Accountant package 2026", text)
        self.assertIn("not tax advice", text)


class PackageMemoryTests(AppTestCase):
    def test_streaming_keeps_memory_bounded(self):
        self.signup()
        year = self.new_year(2026)
        movement = self.add_movement(2026, "Canggu", "Indonesia", "2026-02-01", "2026-03-31")
        for i in range(5):  # 50 MB of receipts
            self.insert_document(year, f"big-{i}.pdf", os.urandom(10 * 1024 * 1024), movement_id=movement)
        tracemalloc.start()
        # Building the PDF and CSV files is a fixed cost; what must not grow with the size of
        # the year is the memory used while the receipts stream out.
        resp = self.client.get("/year/2026/package", buffered=False)
        held, _ = tracemalloc.get_traced_memory()
        tracemalloc.reset_peak()
        total, biggest = 0, 0
        for chunk in resp.response:
            total += len(chunk)
            biggest = max(biggest, len(chunk))
        resp.close()
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        self.assertGreater(total, 50 * 1024 * 1024)
        self.assertLess(biggest, 1024 * 1024, "chunks should stay small")
        self.assertLess(peak - held, 2 * 1024 * 1024,
                        f"streaming 50 MB used {(peak - held) / 1e6:.2f} MB of extra memory")


if __name__ == "__main__":
    unittest.main()
