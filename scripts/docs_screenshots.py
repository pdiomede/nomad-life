"""Make the screenshots of the documentation page (static/img/docs/*.webp and shots.json).

Builds a scratch copy of the app with a demo account (a 2026 year based in Lisbon, a few
movements, receipts and a support ticket), serves it on a free local port, and runs
scripts/docs_screenshots.mjs, which drives the installed Google Chrome headless to take each
screenshot in the light and the dark theme. Needs Node 22 or newer and Google Chrome; nothing
is installed. Your own database and config.env are never touched.

    .venv/bin/python scripts/docs_screenshots.py            # all screenshots
    .venv/bin/python scripts/docs_screenshots.py map        # only the ones named

Run it again after changing a page shown in the documentation.
"""
import io
import json
import logging
import os
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, "static", "img", "docs")
USER, ADMIN = "alex@example.com", "support@example.com"


def pdf(title):
    """A small one page PDF receipt."""
    from fpdf import FPDF
    doc = FPDF()
    doc.add_page()
    doc.set_font("Helvetica", size=14)
    doc.cell(0, 10, title)
    return io.BytesIO(bytes(doc.output()))


def add_demo_passkeys(db_path, stop):
    """Two sample passkeys for the Two-factor card, added once the browser has signed in: an
    account with a passkey asks for it after the password, which headless Chrome cannot give.
    Display only (the keys are not real); they never sign anything."""
    conn = sqlite3.connect(db_path)
    try:
        seeded = conn.execute("SELECT COUNT(*) FROM user_sessions s JOIN users u ON u.id = "
                              "s.user_id WHERE u.email = ?", (USER,)).fetchone()[0]
        while not stop.wait(0.2):
            now = conn.execute("SELECT COUNT(*) FROM user_sessions s JOIN users u ON u.id = "
                               "s.user_id WHERE u.email = ?", (USER,)).fetchone()[0]
            if now > seeded:  # the browser's own session
                conn.executemany(
                    "INSERT INTO passkeys (user_id, credential_id, public_key, name, created_at, "
                    "last_used_at) SELECT id, ?, x'00', ?, datetime('now', ?), ? FROM users "
                    "WHERE email = ?",
                    [("demo-mac", "MacBook Pro (Safari)", "-12 days", "2026-10-01 09:00:00", USER),
                     ("demo-phone", "iPhone (Safari)", "-3 days", None, USER)])
                conn.commit()
                return
    finally:
        conn.close()


def seed(app, tmp, password):
    import app as appmod
    with app.app_context():
        hashed = appmod.hash_password(password)
    conn = sqlite3.connect(app.config["DATABASE_PATH"])
    for email, first, last, plan in ((USER, "Alex", "Rivera", "pro"),
                                     (ADMIN, "Nomad Life", "Support", "free")):
        conn.execute("INSERT INTO users (email, password_hash, first_name, last_name, plan, "
                     "verified_at) VALUES (?, ?, ?, ?, ?, datetime('now'))",
                     (email, hashed, first, last, plan))
    conn.commit()
    conn.close()

    user, admin = app.test_client(), app.test_client()
    for client, email in ((user, USER), (admin, ADMIN)):
        client.post("/login", data={"email": email, "password": password})
    user.post("/year/new", data={"year": "2026", "base_city": "Lisbon", "base_country": "Portugal"})
    user.post("/year/2026/base", data={"action": "upload", "kind": "rental_contract",
                                       "file": (pdf("Rental contract, Lisbon"),
                                                "rental-contract-lisbon.pdf")},
              content_type="multipart/form-data")
    stays = [("Spain", "Madrid", "2026-03-03", "2026-03-06", "Conference week"),
             ("Italy", "Rome", "2026-03-06", "2026-03-08", ""),
             ("Japan", "Tokyo", "2026-05-10", "2026-05-24", "Visa free 90 days"),
             ("France", "Paris", "2026-07-01", "2026-07-05", ""),
             ("Thailand", "Bangkok", "2026-11-13", "2026-11-20", "Planned")]
    for country, city, start, end, notes in stays:
        user.post("/year/2026/movements/new", data={
            "country": country, "city": city, "start_date": start, "end_date": end,
            "notes": notes, "kind": "accommodation"})
    user.post("/movements/1", data={"action": "upload", "kind": "accommodation",
                                    "file": (pdf("Hotel Madrid"), "hotel-madrid.pdf")},
              content_type="multipart/form-data")
    user.post("/movements/1", data={"action": "upload", "kind": "flight",
                                    "file": (pdf("Flight LIS to MAD"), "flight-lis-mad.pdf")},
              content_type="multipart/form-data")
    user.post("/support/new", data={
        "kind": "question", "subject": "Does a travel day count twice?",
        "body": "I flew from Madrid to Rome on 6 March. Which country gets that day?"})
    admin.post("/admin/support/2026-1", data={
        "action": "reply", "body": "Only once: a shared day counts for the stay that started "
                                   "later, so 6 March goes to Italy. The Spain row shows "
                                   "\"3 counted\" for that reason."})
    user.post("/support/2026-1", data={"action": "reply", "body": "Clear, thank you!"})
    # A file for the upload screenshot, chosen in the browser but never sent.
    sample = os.path.join(tmp, "flight-lis-bcn.pdf")
    with open(sample, "wb") as fh:
        fh.write(pdf("Flight LIS to BCN").getvalue())
    return sample


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main(only):
    from werkzeug.serving import make_server
    import app as appmod
    from PIL import Image

    with tempfile.TemporaryDirectory() as tmp:
        port = free_port()
        password = secrets.token_urlsafe(12)
        app = appmod.create_app({
            "DATABASE_PATH": os.path.join(tmp, "demo.db"), "UPLOAD_DIR": os.path.join(tmp, "up"),
            "SECRET_KEY": secrets.token_hex(16), "WTF_CSRF_ENABLED": False, "PWNED_CHECK": False,
            "GMAIL_USER": "", "GMAIL_APP_PASSWORD": "", "ADMIN_EMAILS": frozenset({ADMIN}),
            "ADMIN_REQUIRE_2FA": False, "APP_BASE_URL": f"http://127.0.0.1:{port}"})
        app.logger.disabled = True
        logging.getLogger("werkzeug").setLevel(logging.ERROR)  # no request log
        sample = seed(app, tmp, password)
        server = make_server("127.0.0.1", port, app, threaded=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        stop = threading.Event()
        threading.Thread(target=add_demo_passkeys, args=(app.config["DATABASE_PATH"], stop),
                         daemon=True).start()
        os.makedirs(OUT, exist_ok=True)
        try:
            subprocess.run(["node", os.path.join(ROOT, "scripts", "docs_screenshots.mjs"),
                            json.dumps({"base": f"http://127.0.0.1:{port}", "email": USER,
                                        "password": password, "out": OUT, "sample": sample,
                                        "only": only})], check=True)
        finally:
            stop.set()
            server.shutdown()

    # Sizes in CSS pixels (the shots are taken at twice the resolution) for width and height.
    sizes = {}
    for name in sorted(os.listdir(OUT)):
        if name.endswith("-light.webp"):
            with Image.open(os.path.join(OUT, name)) as im:
                sizes[name.removesuffix("-light.webp")] = [im.width // 2, im.height // 2]
    with open(os.path.join(OUT, "shots.json"), "w", encoding="utf-8") as fh:
        json.dump(sizes, fh, indent=1, sort_keys=True)
        fh.write("\n")
    print(f"{len(sizes)} screenshots in {os.path.relpath(OUT, ROOT)}")


if __name__ == "__main__":
    main(sys.argv[1:])
