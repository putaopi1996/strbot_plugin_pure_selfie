"""Manage uploaded reference images: persist, sync, and load.

This module is intentionally free of AstrBot dependencies for easy unit testing.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

from .image_format import guess_image_mime_and_ext_strict

logger = logging.getLogger(__name__)

SUPPORTED_FORMATS: tuple[str, ...] = ("image/jpeg", "image/png", "image/webp")
MAX_FILE_SIZE: int = 10 * 1024 * 1024  # 10 MB


@dataclass
class SyncResult:
    """Result of a sync operation."""

    persisted: int = 0  # newly written files
    skipped: int = 0  # already existed on disk
    orphans_removed: int = 0  # orphan files cleaned
    errors: int = 0  # entries that failed validation
    total_files: int = 0  # files in directory after sync
    total_bytes: int = 0  # total bytes on disk after sync


class UploadedRefsManager:
    """管理 uploaded_refs 目录的同步、持久化、读取。"""

    SUPPORTED_FORMATS = SUPPORTED_FORMATS
    MAX_FILE_SIZE = MAX_FILE_SIZE

    def __init__(self, refs_dir: Path) -> None:
        self.refs_dir = refs_dir
        self.refs_dir.mkdir(parents=True, exist_ok=True)

    def content_hash(self, data: bytes) -> str:
        """SHA-256 hex digest of file content."""
        return hashlib.sha256(data).hexdigest()

    def persist_image(self, data: bytes) -> Path | None:
        """Validate format & size, write to refs_dir/{hash}.{ext}.

        Returns the path of the written file, or None if validation fails.
        """
        # Size check
        if len(data) > self.MAX_FILE_SIZE:
            logger.warning(
                "Image rejected: size %d bytes exceeds limit %d",
                len(data),
                self.MAX_FILE_SIZE,
            )
            return None

        # Format validation via magic bytes
        result = guess_image_mime_and_ext_strict(data)
        if result is None:
            logger.warning("Image rejected: unrecognized format (magic bytes)")
            return None

        mime, ext = result
        if mime not in self.SUPPORTED_FORMATS:
            logger.warning("Image rejected: unsupported format %s", mime)
            return None

        # Content-hash filename
        hex_hash = self.content_hash(data)
        filename = f"{hex_hash}.{ext}"
        filepath = self.refs_dir / filename

        # Write (idempotent — skip if already exists with same name)
        if not filepath.exists():
            filepath.write_bytes(data)

        return filepath

    def sync(self, config_entries: list[dict]) -> SyncResult:
        """Full sync: persist new entries from config, remove orphans.

        Config entries format: [{"name": "...", "data": "<base64>", "type": "..."}]
        Only magic bytes are trusted for format detection.
        """
        result = SyncResult()
        valid_hashes: set[str] = set()

        for entry in config_entries:
            # Decode base64 data
            raw_data = entry.get("data", "")
            if not raw_data:
                result.errors += 1
                logger.warning("Sync: skipping entry with empty data (name=%s)", entry.get("name", "?"))
                continue

            try:
                data = base64.b64decode(raw_data)
            except Exception:
                result.errors += 1
                logger.warning(
                    "Sync: base64 decode failed for entry (name=%s)",
                    entry.get("name", "?"),
                )
                continue

            # Size check
            if len(data) > self.MAX_FILE_SIZE:
                result.errors += 1
                logger.warning(
                    "Sync: image too large (%d bytes) for entry (name=%s)",
                    len(data),
                    entry.get("name", "?"),
                )
                continue

            # Format validation via magic bytes
            fmt_result = guess_image_mime_and_ext_strict(data)
            if fmt_result is None:
                result.errors += 1
                logger.warning(
                    "Sync: unrecognized format for entry (name=%s)",
                    entry.get("name", "?"),
                )
                continue

            mime, ext = fmt_result
            if mime not in self.SUPPORTED_FORMATS:
                result.errors += 1
                logger.warning(
                    "Sync: unsupported format %s for entry (name=%s)",
                    mime,
                    entry.get("name", "?"),
                )
                continue

            hex_hash = self.content_hash(data)
            valid_hashes.add(hex_hash)

            filename = f"{hex_hash}.{ext}"
            filepath = self.refs_dir / filename

            if filepath.exists():
                result.skipped += 1
            else:
                filepath.write_bytes(data)
                result.persisted += 1

        # Remove orphan files whose hash is not in the valid set
        for f in self.refs_dir.iterdir():
            if not f.is_file():
                continue
            stem = f.stem
            if stem not in valid_hashes:
                f.unlink()
                result.orphans_removed += 1

        # Compute totals after sync
        for f in self.refs_dir.iterdir():
            if f.is_file():
                result.total_files += 1
                result.total_bytes += f.stat().st_size

        return result

    def list_reference_files(self) -> list[Path]:
        """List all valid image files in refs_dir sorted by name."""
        files = [
            f
            for f in self.refs_dir.iterdir()
            if f.is_file() and f.suffix.lstrip(".") in ("jpg", "png", "webp")
        ]
        return sorted(files, key=lambda p: p.name)

    def load_reference_bytes(self) -> list[bytes]:
        """Read all reference image files as bytes."""
        return [f.read_bytes() for f in self.list_reference_files()]
