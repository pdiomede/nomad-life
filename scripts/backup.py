"""Back up Nomad Life: the database, its secret key and every receipt, in one archive.

    .venv/bin/python scripts/backup.py --dest /home/paolo/backup/nomadlife --keep 14

Writes <dest>/nomadlife-YYYY-MM-DD_HHMMSS.tar.gz (mode 600) holding nomad.db, .secret_key
(when the app created one) and uploads/, then deletes all but the newest --keep archives of
this kind; other files in <dest> are left alone. The paths come from config.env, read the same
way as the app reads them.

Safe while the app runs: the database is copied with SQLite's online backup, before the
receipts, so a receipt uploaded meanwhile is at worst an extra file; half written receipts
(*.tmp) are skipped and receipts deleted meanwhile are ignored. The archive is written under a
temporary name and renamed when complete, so a failed run never leaves a partial backup.

To restore: stop the app, put nomad.db and .secret_key back in the database folder and
uploads/ in UPLOAD_DIR (owned by the app's user, mode 700), start the app.
"""
import argparse
import os
import pathlib
import re
import sqlite3
import sys
import tarfile
import tempfile
import time

from dotenv import dotenv_values

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVE = re.compile(r"^nomadlife-\d{4}-\d{2}-\d{2}_\d{6}\.tar\.gz$")


def setting(config, key, default):
    """config.env wins over the environment, as in the app (load_dotenv(override=True))."""
    value = config.get(key)
    if value is None:
        value = os.environ.get(key, default)
    return value


def resolve(path):
    """Like app._abs: "~" is the home folder and relative paths start at the app folder."""
    path = os.path.expanduser(path)
    return path if os.path.isabs(path) else os.path.join(ROOT, path)


def copy_database(src, dst):
    """A consistent copy of a database that may be in use, without ever creating or
    changing the original (read only)."""
    source = sqlite3.connect(pathlib.Path(src).as_uri() + "?mode=ro", uri=True)
    try:
        target = sqlite3.connect(dst)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()


def add_receipts(tar, upload_dir):
    """Add every receipt under uploads/, skipping temporary files and files that disappear
    while the backup runs. Size and contents come from the same open file, so a receipt
    replaced meanwhile is stored whole (old or new version)."""
    count = 0
    for folder, dirs, files in os.walk(upload_dir):
        dirs.sort()
        for name in sorted(files):
            if name.endswith(".tmp"):
                continue
            path = os.path.join(folder, name)
            arcname = os.path.join("uploads", os.path.relpath(path, upload_dir))
            try:
                fh = open(path, "rb")
            except FileNotFoundError:
                continue
            with fh:
                info = tar.gettarinfo(arcname=arcname, fileobj=fh)
                tar.addfile(info, fh)
            count += 1
    return count


def prune(dest, keep):
    """Delete all but the newest `keep` archives made by this script."""
    archives = sorted(name for name in os.listdir(dest) if ARCHIVE.match(name))
    removed = archives[:-keep]
    for name in removed:
        os.remove(os.path.join(dest, name))
    return removed


def main(argv=None):
    parser = argparse.ArgumentParser(description="Back up the Nomad Life database and receipts.")
    parser.add_argument("--dest", required=True, help="folder that holds the backups")
    parser.add_argument("--keep", type=int, default=8, help="how many backups to keep (default 8)")
    parser.add_argument("--config", default=os.path.join(ROOT, "config.env"),
                        help="the app's config.env (default: the one next to app.py)")
    args = parser.parse_args(argv)
    if args.keep < 1:
        parser.error("--keep must be at least 1")

    os.umask(0o077)
    config = dotenv_values(args.config) if os.path.exists(args.config) else {}
    db_path = resolve(setting(config, "DATABASE_PATH", "data/nomad.db"))
    upload_dir = resolve(setting(config, "UPLOAD_DIR", "uploads"))
    secret_key = os.path.join(os.path.dirname(db_path), ".secret_key")
    if not os.path.isfile(db_path):
        print(f"backup: no database at {db_path}", file=sys.stderr)
        return 1

    dest = os.path.abspath(os.path.expanduser(args.dest))
    os.makedirs(dest, exist_ok=True)
    name = f"nomadlife-{time.strftime('%Y-%m-%d_%H%M%S')}.tar.gz"
    fd, partial = tempfile.mkstemp(dir=dest, prefix=".nomadlife-", suffix=".partial")
    os.close(fd)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            db_copy = os.path.join(tmp, "nomad.db")
            copy_database(db_path, db_copy)
            with tarfile.open(partial, "w:gz") as tar:
                tar.add(db_copy, arcname="nomad.db")
                if os.path.isfile(secret_key):
                    tar.add(secret_key, arcname=".secret_key")
                receipts = add_receipts(tar, upload_dir) if os.path.isdir(upload_dir) else 0
        os.chmod(partial, 0o600)
        os.replace(partial, os.path.join(dest, name))
    finally:
        if os.path.exists(partial):
            os.remove(partial)

    removed = prune(dest, args.keep)
    size = os.path.getsize(os.path.join(dest, name))
    print(f"backup: {os.path.join(dest, name)} ({size} bytes, {receipts} receipt files)"
          + (f", removed {len(removed)} old" if removed else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
