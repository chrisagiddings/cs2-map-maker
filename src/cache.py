"""Download cache under data/raw/, keyed by a hash of the request.

Layout: data/raw/<source>/<hash>.<ext> plus <hash>.json describing the request
(URL, params, timestamp, byte count) so a human can tell what a blob is.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"

# bytes and files written by every Cache in this process (the benchmark reports them)
SESSION = {"bytes": 0, "files": 0}


def reset_session_stats() -> None:
    SESSION["bytes"] = 0
    SESSION["files"] = 0


def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def key_for(*parts) -> str:
    """Stable hash for any JSON-serialisable description of a request."""
    return hashlib.sha1(_canon(parts).encode()).hexdigest()[:16]


class Cache:
    def __init__(self, root: Path = RAW_DIR):
        self.root = Path(root)

    def path(self, source: str, key: str, ext: str) -> Path:
        d = self.root / source
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{key}.{ext}"

    def get(self, source: str, key: str, ext: str) -> Path | None:
        p = self.path(source, key, ext)
        return p if p.exists() and p.stat().st_size > 0 else None

    def put(self, source: str, key: str, ext: str, data: bytes, meta: dict) -> Path:
        p = self.path(source, key, ext)
        tmp = p.with_suffix(p.suffix + ".part")
        tmp.write_bytes(data)
        tmp.replace(p)
        SESSION["bytes"] += len(data)
        SESSION["files"] += 1
        meta = dict(meta, bytes=len(data), cached_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
        # sidecar must never collide with the blob itself (a .json blob would be overwritten)
        p.with_name(f"{key}.meta.json").write_text(json.dumps(meta, indent=1, default=str))
        return p
