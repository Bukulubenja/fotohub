"""Stream a gallery ZIP straight into object storage.

zipfile writes to a sink that cuts its output into multipart parts, so memory stays at about
one part however big the gallery is. Entries are STORED: JPEG and WebP don't compress further,
and storing keeps the build I/O-bound. zipfile switches to ZIP64 when sizes need it.
"""

import datetime
import zipfile
from dataclasses import dataclass
from pathlib import PurePath

from assets.storage import ObjectStorage


@dataclass(frozen=True)
class Entry:
    key: str
    name: str  # path inside the ZIP
    size: int


class PartSink:
    """Write-only file object for zipfile that uploads every `part_size` bytes as one part."""

    def __init__(self, store: ObjectStorage, key: str, upload_id: str, part_size: int) -> None:
        self.store, self.key, self.upload_id, self.part_size = store, key, upload_id, part_size
        self.buffer = bytearray()
        self.next_part = 1
        self.written = 0

    def write(self, data: bytes) -> int:
        self.buffer += data
        self.written += len(data)
        while len(self.buffer) >= self.part_size:
            self._send(bytes(self.buffer[: self.part_size]))
            del self.buffer[: self.part_size]
        return len(data)

    def tell(self) -> int:
        return self.written

    def flush(self) -> None:
        """zipfile flushes after each entry; parts go out only when full (S3 minimum 5 MiB)."""

    def finish(self) -> None:
        if self.buffer or self.next_part == 1:
            self._send(bytes(self.buffer))
            self.buffer.clear()

    def _send(self, data: bytes) -> None:
        self.store.upload_part(self.key, self.upload_id, self.next_part, data)
        self.next_part += 1


def unique_names(filenames: list[str]) -> list[str]:
    """Two "IMG_0001.jpg" from different cameras become "IMG_0001.jpg" and "IMG_0001 (2).jpg"."""
    used: set[str] = set()  # case-insensitive: Windows and macOS would overwrite otherwise
    result = []
    for filename in filenames:
        name = PurePath(filename.replace("\\", "/")).name or "photo.jpg"
        path, candidate, number = PurePath(name), name, 1
        while candidate.lower() in used:
            number += 1
            candidate = f"{path.stem} ({number}){path.suffix}"
        used.add(candidate.lower())
        result.append(candidate)
    return result


def write_archive(
    store: ObjectStorage, key: str, entries: list[Entry], part_size: int, now: datetime.datetime
) -> int:
    """Build the ZIP at `key`; returns its size. Aborts the upload on any failure."""
    upload_id = store.create_multipart(key, "application/zip")
    sink = PartSink(store, key, upload_id, part_size)
    try:
        with zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
            for entry in entries:
                info = zipfile.ZipInfo(entry.name, date_time=now.timetuple()[:6])
                info.compress_type = zipfile.ZIP_STORED
                info.file_size = entry.size  # lets zipfile pick ZIP64 up front for huge files
                with zf.open(info, "w") as out:
                    for chunk in store.iter_object(entry.key):
                        out.write(chunk)
        sink.finish()
        store.complete_multipart(key, upload_id)
    except BaseException:
        store.abort_multipart(key, upload_id)
        raise
    return sink.written
