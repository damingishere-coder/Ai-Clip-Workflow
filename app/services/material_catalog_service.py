"""Local folder capabilities never extend the global media-serving allowlist.

Scanning reads directory metadata only. Confirmation rechecks selected file
identities; subsequent import must recheck them again and verify content hashes.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
from uuid import uuid4

from app.db.database import get_connection
from app.models.material import MaterialRegistration
from app.services.storage_service import VIDEO_EXTENSIONS

MAX_ENTRIES = 1000
MAX_VIDEOS = 200


class MaterialError(ValueError):
    def __init__(self, message, status_code=409):
        super().__init__(message)
        self.status_code = status_code


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _stamp(path):
    info = path.stat(follow_symlinks=False)
    return {"size": info.st_size, "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns,
            "device": info.st_dev, "inode": info.st_ino}


def _check_local_path(raw):
    raw = str(raw).strip()
    if (not raw or raw.startswith(("\\\\", "//")) or any(ord(c) < 32 for c in raw)
            or ".." in raw.replace("\\", "/").split("/")):
        raise MaterialError("请选择本机目录；不支持网络路径、设备路径或 .. 跳转", 400)
    path = Path(raw)
    if not path.is_absolute():
        raise MaterialError("请填写本机目录的完整路径", 400)
    if os.name == "nt":
        import ctypes
        if ctypes.windll.kernel32.GetDriveTypeW(str(path.anchor)) == 4:
            raise MaterialError("第一版仅支持本机磁盘，不支持映射的网络盘", 400)
    # Reject junctions/reparse points as well as symlinks, including ancestors.
    try:
        for part in (path, *path.parents):
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise MaterialError("目录或文件包含链接/重解析点，请选择实际本机目录", 400)
        return path.resolve(strict=True)
    except OSError as exc:
        raise MaterialError("目录或文件不可访问，请确认本机路径仍然存在", 400) from exc


def directory_identity(raw):
    path = _check_local_path(raw)
    if not path.is_dir():
        raise MaterialError("选择的路径不是目录", 400)
    info = _stamp(path)
    return path, {"path": str(path), "device": info["device"], "inode": info["inode"]}


def _source(path, directory):
    resolved = _check_local_path(path)
    if resolved.parent != directory or not resolved.is_file() or resolved.suffix.lower() not in VIDEO_EXTENSIONS:
        raise MaterialError("素材已离开所选目录或不再是视频文件")
    stamp = _stamp(resolved)
    if stamp["size"] <= 0:
        raise MaterialError("视频为空文件")
    return {"schema": "local-source-v1", "path": str(resolved), "path_key": os.path.normcase(str(resolved)),
            "file_name": resolved.name, "directory": str(directory), "stamp": stamp}


def validate_source(source):
    """Used again by import jobs; registration never grants a mutable path."""
    path, identity = directory_identity(source["directory"])
    if source.get("directory_identity") != identity:
        raise MaterialError("素材目录已替换，请重新扫描")
    current = _source(Path(source["path"]), path)
    current["directory_identity"] = identity
    if digest(current) != digest(source):
        raise MaterialError("素材文件已变化，请重新扫描登记")
    return Path(current["path"])


def scan_directory(raw):
    directory, identity = directory_identity(raw)
    entries, excluded = [], []
    try:
        with os.scandir(directory) as listing:
            for number, entry in enumerate(listing, start=1):
                if number > MAX_ENTRIES:
                    raise MaterialError(f"目录超过 {MAX_ENTRIES} 项，请选择更小的素材文件夹", 400)
                path = Path(entry.path)
                if path.suffix.lower() not in VIDEO_EXTENSIONS or not entry.is_file(follow_symlinks=False):
                    excluded.append({"file_name": entry.name, "reason": "子目录或不支持的文件；不递归扫描"})
                    continue
                try:
                    source = _source(path, directory)
                    source["directory_identity"] = identity
                    key = digest(source)
                    entries.append({"source_key": key, "source": source})
                    if len(entries) > MAX_VIDEOS:
                        raise MaterialError(f"单次最多 {MAX_VIDEOS} 个视频，请按集数拆分文件夹", 400)
                except MaterialError as exc:
                    if len(entries) > MAX_VIDEOS:
                        raise
                    excluded.append({"file_name": entry.name, "reason": str(exc)})
    except OSError as exc:
        raise MaterialError("扫描期间目录不可访问，请重试", 400) from exc
    entries.sort(key=lambda item: (item["source"]["file_name"].casefold(), item["source_key"]))
    excluded.sort(key=lambda item: item["file_name"].casefold())
    manifest = {"schema": "material-scan-v1", "directory": identity, "entries": entries, "excluded": excluded}
    now = datetime.now(timezone.utc)
    result = {"id": uuid4().hex, "manifest_sha256": digest(manifest), "manifest": manifest,
              "created_at": now.isoformat(), "expires_at": (now+timedelta(minutes=30)).isoformat()}
    with get_connection() as c:
        c.execute("INSERT INTO material_scans(id,directory,manifest_json,manifest_sha256,created_at,expires_at) VALUES(?,?,?,?,?,?)",
                  (result["id"], str(directory), canonical(manifest), result["manifest_sha256"], result["created_at"], result["expires_at"]))
        c.commit()
    return result


def _read_material(c, material_id):
    row = c.execute("SELECT * FROM source_materials WHERE id=?", (material_id,)).fetchone()
    if not row:
        raise MaterialError("素材不存在", 404)
    item = dict(row)
    source = json.loads(item.pop("source_json"))
    if (digest(source) != item["identity_sha256"] or item["source_key"] != item["identity_sha256"]
            or source["path"] != item["source_path"] or source["file_name"] != item["file_name"]
            or source["stamp"]["size"] != item["size_bytes"]):
        raise MaterialError("素材版本证据不一致，不能继续导入")
    return {**item, "source": source}


def get_material(material_id):
    with get_connection() as c:
        return _read_material(c, material_id)


def list_materials(limit=50, offset=0):
    with get_connection() as c:
        total = c.execute("SELECT count(*) FROM source_materials").fetchone()[0]
        ids = c.execute("SELECT id FROM source_materials ORDER BY created_at DESC,id LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        return {"total": total, "materials": [_read_material(c, r[0]) for r in ids], "limit": limit, "offset": offset}


def register_materials(payload: MaterialRegistration):
    if not payload.confirmed:
        raise MaterialError("请先核对目录与素材清单并确认登记", 400)
    request = payload.model_dump(mode="json")
    request["source_keys"] = sorted(request["source_keys"])
    request_hash = digest(request)
    with get_connection() as c:
        c.execute("BEGIN IMMEDIATE")
        existing = c.execute("SELECT * FROM material_registrations WHERE request_key=?", (str(payload.request_key),)).fetchone()
        if existing:
            if existing["request_sha256"] != request_hash:
                raise MaterialError("请求编号已用于另一份登记，请重新核对")
            return {"id": existing["id"], "material_ids": json.loads(existing["material_ids_json"]), "reused": True}
        row = c.execute("SELECT * FROM material_scans WHERE id=?", (payload.scan_id,)).fetchone()
        if not row:
            raise MaterialError("目录预览不存在，请重新扫描", 404)
        manifest = json.loads(row["manifest_json"])
        if digest(manifest) != row["manifest_sha256"] or payload.manifest_sha256 != row["manifest_sha256"]:
            raise MaterialError("预览清单校验失败，请重新扫描")
        if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
            raise MaterialError("目录预览已超过 30 分钟，请重新扫描")
        entries = {item["source_key"]: item["source"] for item in manifest["entries"]}
        if not set(payload.source_keys).issubset(entries):
            raise MaterialError("选择中包含不属于此预览的文件")
        material_ids, now = [], datetime.now(timezone.utc).isoformat()
        for key in request["source_keys"]:
            source = entries[key]
            if digest(source) != key:
                raise MaterialError("素材版本校验失败")
            validate_source(source)
            item = c.execute("SELECT id FROM source_materials WHERE source_key=?", (key,)).fetchone()
            if item:
                material_id = item[0]
                _read_material(c, material_id)
            else:
                material_id = uuid4().hex
                c.execute("INSERT INTO source_materials(id,source_key,source_path,file_name,size_bytes,source_json,identity_sha256,created_at) VALUES(?,?,?,?,?,?,?,?)",
                          (material_id, key, source["path"], source["file_name"], source["stamp"]["size"], canonical(source), key, now))
            material_ids.append(material_id)
        registration_id = uuid4().hex
        c.execute("INSERT INTO material_registrations(id,scan_id,request_key,request_sha256,material_ids_json,created_at) VALUES(?,?,?,?,?,?)",
                  (registration_id, payload.scan_id, str(payload.request_key), request_hash, canonical(material_ids), now))
        c.commit()
        return {"id": registration_id, "material_ids": material_ids, "reused": False}
