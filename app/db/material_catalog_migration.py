"""Additive local source inventory; no tasks or production policies are rewritten."""
import hashlib
import sqlite3

VERSION = "20260915_06_material_catalog"
NAME = "本地素材与目录预览"
STATEMENTS = (
    """CREATE TABLE material_scans (
        id TEXT PRIMARY KEY, directory TEXT NOT NULL, manifest_json TEXT NOT NULL,
        manifest_sha256 TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL
    )""",
    "CREATE INDEX idx_material_scan_expiry ON material_scans(expires_at)",
    "CREATE TRIGGER immutable_material_scan BEFORE UPDATE ON material_scans BEGIN SELECT RAISE(ABORT,'Material scan is immutable'); END",
    """CREATE TABLE source_materials (
        id TEXT PRIMARY KEY, source_key TEXT NOT NULL UNIQUE,
        source_path TEXT NOT NULL, file_name TEXT NOT NULL, size_bytes INTEGER NOT NULL CHECK(size_bytes>0),
        source_json TEXT NOT NULL, identity_sha256 TEXT NOT NULL, created_at TEXT NOT NULL
    )""",
    "CREATE INDEX idx_source_material_created ON source_materials(created_at,id)",
    "CREATE TRIGGER immutable_source_material BEFORE UPDATE ON source_materials BEGIN SELECT RAISE(ABORT,'Material version is immutable'); END",
    """CREATE TABLE material_registrations (
        id TEXT PRIMARY KEY, scan_id TEXT NOT NULL REFERENCES material_scans(id),
        request_key TEXT NOT NULL UNIQUE, request_sha256 TEXT NOT NULL,
        material_ids_json TEXT NOT NULL, created_at TEXT NOT NULL
    )""",
    "CREATE INDEX idx_material_registration_scan ON material_registrations(scan_id)",
    "CREATE TRIGGER immutable_material_registration BEFORE UPDATE ON material_registrations BEGIN SELECT RAISE(ABORT,'Material registration is immutable'); END",
)
CHECKSUM = hashlib.sha256("\n".join(STATEMENTS).encode()).hexdigest()


def needs_migration(path):
    if not path.is_file() or not path.stat().st_size:
        return False
    with sqlite3.connect(path.resolve().as_uri()+"?mode=ro", uri=True) as c:
        return not c.execute("SELECT 1 FROM sqlite_master WHERE name='source_materials'").fetchone()


def apply(connection):
    for statement in STATEMENTS:
        connection.execute(statement)


def verify(connection):
    names = ("material_scans", "idx_material_scan_expiry", "immutable_material_scan",
             "source_materials", "idx_source_material_created", "immutable_source_material",
             "material_registrations", "idx_material_registration_scan", "immutable_material_registration")
    for name, statement in zip(names, STATEMENTS, strict=True):
        row = connection.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone()
        if not row or row[0] != statement:
            raise ValueError("素材登记表或证据约束不一致")
