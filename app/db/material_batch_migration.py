"""Task-bound batch import; additive and independent of the task state machine."""
import hashlib
import sqlite3

VERSION = "20260915_07_material_batches"
NAME = "素材批次与导入证据"
STATEMENTS = (
    """CREATE TABLE material_batches (
        id TEXT PRIMARY KEY, request_key TEXT NOT NULL UNIQUE, request_sha256 TEXT NOT NULL,
        config_json TEXT NOT NULL, config_sha256 TEXT NOT NULL, created_at TEXT NOT NULL
    )""",
    "CREATE TRIGGER immutable_material_batch BEFORE UPDATE ON material_batches BEGIN SELECT RAISE(ABORT,'Material batch is immutable'); END",
    """CREATE TABLE material_batch_items (
        id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES material_batches(id),
        material_id TEXT NOT NULL REFERENCES source_materials(id), task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id),
        job_id TEXT NOT NULL UNIQUE REFERENCES workflow_jobs(id), is_repeat INTEGER NOT NULL CHECK(is_repeat IN (0,1)),
        generation_json TEXT NOT NULL, generation_sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
        UNIQUE(batch_id, material_id)
    )""",
    "CREATE UNIQUE INDEX idx_material_first_production ON material_batch_items(material_id) WHERE is_repeat=0",
    "CREATE INDEX idx_material_batch_items ON material_batch_items(batch_id,id)",
    "CREATE TRIGGER immutable_material_batch_item BEFORE UPDATE ON material_batch_items BEGIN SELECT RAISE(ABORT,'Batch item is immutable'); END",
    """CREATE TABLE material_imports (
        item_id TEXT PRIMARY KEY REFERENCES material_batch_items(id),
        source_sha256 TEXT NOT NULL, stored_path TEXT NOT NULL, size_bytes INTEGER NOT NULL CHECK(size_bytes>0),
        evidence_json TEXT NOT NULL, evidence_sha256 TEXT NOT NULL, created_at TEXT NOT NULL
    )""",
    "CREATE TRIGGER immutable_material_import BEFORE UPDATE ON material_imports BEGIN SELECT RAISE(ABORT,'Material import is immutable'); END",
)
CHECKSUM = hashlib.sha256("\n".join(STATEMENTS).encode()).hexdigest()


def needs_migration(path):
    if not path.is_file() or not path.stat().st_size:
        return False
    with sqlite3.connect(path.resolve().as_uri()+"?mode=ro", uri=True) as c:
        return not c.execute("SELECT 1 FROM sqlite_master WHERE name='material_batches'").fetchone()


def apply(connection):
    for statement in STATEMENTS:
        connection.execute(statement)


def verify(connection):
    names = ("material_batches", "immutable_material_batch", "material_batch_items",
             "idx_material_first_production", "idx_material_batch_items", "immutable_material_batch_item",
             "material_imports", "immutable_material_import")
    for name, statement in zip(names, STATEMENTS, strict=True):
        row = connection.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone()
        if not row or row[0] != statement:
            raise ValueError("批次或导入证据约束不一致")
