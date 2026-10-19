"""Phase 2 exit-gate drill: push a whole event through the real upload API and bucket.

Talks to FotoHub exactly like the dashboard does (session login, presigned PUTs, multipart
parts, resume, complete) and retries the same way, so the report shows what a photographer
on this connection would see. Standard library only; pyvips is needed just for --synthetic.

    # 1,000 of your own photos (cycled if the folder has fewer), real link speed
    python scripts/upload_drill.py --base-url http://127.0.0.1:8000 --email me@example.com \\
        --image-dir D:/events/wedding --photos 1000 --journal drill.jsonl

    # Synthetic photos, a simulated 1 Mbit/s uplink and 5% of transfers cut mid-file
    python scripts/upload_drill.py ... --synthetic --bandwidth-kbps 1000 --drop-rate 0.05

Stop it at any point (Ctrl+C, laptop sleep, power cut) and rerun with the same --journal:
finished photos are skipped and unfinished ones resume from the parts already in the bucket.
The password comes from FOTOHUB_PASSWORD or a prompt.
"""

from __future__ import annotations

import argparse
import getpass
import http.cookiejar
import json
import os
import random
import statistics
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

API_PREFIX = "/api/v1/"
CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".heif": "image/heif",
}
# Parts aren't signed with a type. Without one, urllib sends the body as a form post.
PART_HEADERS = {"Content-Type": "application/octet-stream"}
DEFAULT_RETRY_DELAYS = "1,3,10,30"  # seconds; same as the dashboard (queue.ts)
API_TIMEOUT_SECONDS = 60
PUT_TIMEOUT_SECONDS = 5 * 60  # an 8 MiB part on a slow 3G link, as in transport.ts
READ_BLOCK_BYTES = 64 * 1024
PROGRESS_EVERY = 25  # files
POLL_SECONDS = 5


# --- HTTP -------------------------------------------------------------------------------


class HttpError(Exception):
    def __init__(self, status: int, message: str, injected: bool = False) -> None:
        super().__init__(f"HTTP {status}: {message}" if status else message)
        self.status = status  # 0 = no HTTP answer (connection lost, timeout)
        self.injected = injected  # cut on purpose by --drop-rate


class InjectedDrop(ConnectionResetError):
    """A simulated connection loss part-way through a transfer (--drop-rate)."""


def is_retryable(error: Exception) -> bool:
    """Same rule as the dashboard: network trouble or a server hiccup, not a bad request."""
    status = getattr(error, "status", None)
    return status is not None and (status in (0, 408, 429) or status >= 500)


class Bandwidth:
    """A token bucket shared by all transfers, so --bandwidth-kbps caps the whole link."""

    def __init__(self, kbps: int | None) -> None:
        self.bytes_per_second = kbps * 1000 / 8 if kbps else None
        self.lock = threading.Lock()
        self.next_free = time.monotonic()

    def take(self, size: int) -> None:
        if not self.bytes_per_second:
            return
        with self.lock:
            now = time.monotonic()
            start = max(now, self.next_free)
            self.next_free = start + size / self.bytes_per_second
            wait = self.next_free - now
        time.sleep(wait)


class Body:
    """A file slice streamed to urllib, throttled, optionally cut off at `drop_at` bytes."""

    def __init__(self, path: Path, start: int, end: int, link: Bandwidth, drop_at: int | None):
        self.handle = path.open("rb")
        self.handle.seek(start)
        self.remaining = end - start
        self.sent = 0
        self.link = link
        self.drop_at = drop_at
        self.dropped = False

    def read(self, size: int = -1) -> bytes:
        size = READ_BLOCK_BYTES if size < 0 else min(size, READ_BLOCK_BYTES)
        size = min(size, self.remaining)
        if self.drop_at is not None and self.sent + size > self.drop_at:
            self.dropped = True
            raise InjectedDrop("simulated connection drop")
        chunk = self.handle.read(size)
        self.link.take(len(chunk))
        self.remaining -= len(chunk)
        self.sent += len(chunk)
        return chunk

    def close(self) -> None:
        self.handle.close()


class Api:
    """Session-authenticated JSON client for /api/v1/, CSRF included."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))

    def csrf_token(self) -> str:
        return next((c.value for c in self.cookies if c.name == "csrftoken"), "")

    def call(self, method: str, path: str, payload: dict | None = None) -> dict:
        body = json.dumps(payload).encode() if payload is not None else None
        headers = {"Accept": "application/json", "Referer": self.base_url + "/"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if method not in ("GET", "HEAD"):
            headers["X-CSRFToken"] = self.csrf_token()
        url = self.base_url + API_PREFIX + path
        request = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with self.opener.open(request, timeout=API_TIMEOUT_SECONDS) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise HttpError(exc.code, exc.read().decode(errors="replace")[:300]) from exc
        except OSError as exc:  # URLError, timeouts, resets
            raise HttpError(0, f"{method} {path}: {exc}") from exc
        return json.loads(raw) if raw else {}

    def login(self, email: str, password: str) -> None:
        self.call("GET", "auth/csrf/")
        self.call("POST", "auth/login/", {"email": email, "password": password})


def put(url: str, body: Body, length: int, headers: dict[str, str]) -> None:
    request = urllib.request.Request(
        url, data=body, method="PUT", headers={**headers, "Content-Length": str(length)}
    )
    try:
        with urllib.request.urlopen(request, timeout=PUT_TIMEOUT_SECONDS) as response:
            response.read()
    except urllib.error.HTTPError as exc:
        raise HttpError(exc.code, exc.read().decode(errors="replace")[:300]) from exc
    except OSError as exc:  # urllib wraps a failed send, InjectedDrop included, in URLError
        raise HttpError(0, f"PUT failed: {exc}", injected=body.dropped) from exc
    finally:
        body.close()


# --- Journal ----------------------------------------------------------------------------


class Journal:
    """Append-only JSON lines, so a rerun knows the gallery and each file's photo id."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.lock = threading.Lock()
        self.gallery: str | None = None
        self.photos: dict[str, str] = {}
        self.completed: set[str] = set()
        if path and path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                entry = json.loads(line)
                if "gallery" in entry:
                    self.gallery = entry["gallery"]
                elif entry.get("state") == "completed":
                    self.completed.add(entry["file"])
                elif entry.get("state") == "started":
                    self.photos[entry["file"]] = entry["photo"]
                elif entry.get("state") == "forgotten":
                    self.photos.pop(entry["file"], None)

    def write(self, **entry: str) -> None:
        if self.path is None:
            return
        with self.lock, self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")


# --- Uploading --------------------------------------------------------------------------


@dataclass(frozen=True)
class Item:
    name: str  # unique per drill run and stable across reruns; also the journal key
    path: Path
    size: int
    content_type: str


@dataclass
class FileResult:
    name: str
    size: int
    ok: bool = False
    skipped: bool = False
    seconds: float = 0.0
    bytes_sent: int = 0  # bytes of PUTs that succeeded; resumed parts aren't resent
    retries: int = 0
    drops: int = 0
    error: str = ""


@dataclass
class Uploader:
    api: Api
    gallery: str
    journal: Journal
    link: Bandwidth
    drop_rate: float
    retry_delays: list[float]
    rng: random.Random = field(default_factory=random.Random)
    rng_lock: threading.Lock = field(default_factory=threading.Lock)

    def drop_point(self, length: int) -> int | None:
        with self.rng_lock:
            if length > 1 and self.rng.random() < self.drop_rate:
                return self.rng.randrange(1, length)
        return None

    def with_retry(self, result: FileResult, step):
        for attempt in range(len(self.retry_delays) + 1):
            try:
                return step()
            except HttpError as exc:
                result.drops += exc.injected
                if not is_retryable(exc) or attempt == len(self.retry_delays):
                    raise
                result.retries += 1
                time.sleep(self.retry_delays[attempt])

    def target(self, item: Item, result: FileResult) -> dict | None:
        """Resume the journalled session if there is one; None if it already finished."""
        photo_id = self.journal.photos.get(item.name)
        if photo_id:
            try:
                return self.with_retry(result, lambda: self.api.call("GET", f"uploads/{photo_id}/"))
            except HttpError as exc:
                if exc.status not in (404, 409):
                    raise
                if exc.status == 409 and self.try_complete(photo_id):
                    return None
                self.journal.photos.pop(item.name, None)
                self.journal.write(file=item.name, state="forgotten")
        started = self.with_retry(
            result,
            lambda: self.api.call(
                "POST",
                "uploads/",
                {
                    "gallery": self.gallery,
                    "filename": item.name,
                    "content_type": item.content_type,
                    "size": item.size,
                },
            ),
        )
        self.journal.photos[item.name] = started["photo"]["id"]
        self.journal.write(file=item.name, photo=started["photo"]["id"], state="started")
        return started

    def try_complete(self, photo_id: str) -> bool:
        try:
            self.api.call("POST", f"uploads/{photo_id}/complete/")
            return True
        except HttpError:
            return False

    def send_single(self, item: Item, upload: dict, result: FileResult) -> None:
        def attempt() -> None:
            body = Body(item.path, 0, item.size, self.link, self.drop_point(item.size))
            put(upload["url"], body, item.size, upload["headers"])
            result.bytes_sent += item.size

        self.with_retry(result, attempt)

    def send_parts(self, item: Item, photo_id: str, part_size: int, result: FileResult) -> None:
        part_count = max(1, -(-item.size // part_size))

        def bounds(n: int) -> tuple[int, int]:
            return (n - 1) * part_size, min(n * part_size, item.size)

        stored = self.with_retry(result, lambda: self.api.call("GET", f"uploads/{photo_id}/parts/"))
        done = {
            p["PartNumber"]
            for p in stored["parts"]
            if p["Size"] == bounds(p["PartNumber"])[1] - bounds(p["PartNumber"])[0]
        }
        for number in (n for n in range(1, part_count + 1) if n not in done):
            start, end = bounds(number)

            def attempt(number: int = number, start: int = start, end: int = end) -> None:
                # Sign right before each try, so a long outage never leaves an expired URL.
                signed = self.api.call(
                    "POST", f"uploads/{photo_id}/sign-parts/", {"part_numbers": [number]}
                )
                body = Body(item.path, start, end, self.link, self.drop_point(end - start))
                put(signed["urls"][str(number)], body, end - start, PART_HEADERS)
                result.bytes_sent += end - start

            self.with_retry(result, attempt)

    def upload(self, item: Item) -> FileResult:
        result = FileResult(item.name, item.size)
        if item.name in self.journal.completed:
            result.ok = result.skipped = True
            return result
        began = time.monotonic()
        try:
            started = self.target(item, result)
            if started is not None:
                photo_id, upload = started["photo"]["id"], started["upload"]
                if upload["type"] == "single":
                    self.send_single(item, upload, result)
                else:
                    self.send_parts(item, photo_id, upload["part_size"], result)
                self.with_retry(
                    result, lambda: self.api.call("POST", f"uploads/{photo_id}/complete/")
                )
            self.journal.write(file=item.name, state="completed")
            result.ok = True
        except HttpError as exc:
            result.error = str(exc)
        result.seconds = time.monotonic() - began
        return result


# --- Inputs -----------------------------------------------------------------------------


def photos_from_dir(folder: Path) -> list[Path]:
    files = sorted(p for p in folder.rglob("*") if p.suffix.lower() in CONTENT_TYPES)
    if not files:
        raise SystemExit(f"No JPEG/PNG/WebP/HEIC files in {folder}")
    return files


def synthetic_photos(folder: Path, count: int, seed: int) -> list[Path]:
    """Camera-sized JPEGs that compress like real photos, plus one big enough for multipart."""
    try:
        import pyvips
    except ImportError as exc:
        raise SystemExit("--synthetic needs pyvips (pip install pyvips pyvips-binary)") from exc
    rng = random.Random(seed)
    paths = []
    for index in range(count):
        # ~45 MB: over the 32 MiB multipart threshold and under the 100 MB cap.
        large = index == 0
        width, height = (6000, 4000) if large else rng.choice([(6000, 4000), (4000, 6000)])
        sigma, blur, quality = (60, 0, 92) if large else (30, 2.0, 88)
        noise = pyvips.Image.gaussnoise(width, height, mean=128, sigma=sigma, seed=seed + index)
        image = noise.bandjoin([noise.rot180(), noise.fliphor()])
        if blur:
            image = image.gaussblur(blur)
        path = folder / f"synthetic-{index:02d}.jpg"
        image.cast("uchar").jpegsave(str(path), Q=quality)
        paths.append(path)
    return paths


def build_items(sources: list[Path], count: int) -> list[Item]:
    items = []
    for index in range(count):
        path = sources[index % len(sources)]
        suffix = path.suffix.lower()
        items.append(
            Item(f"drill-{index + 1:04d}{suffix}", path, path.stat().st_size, CONTENT_TYPES[suffix])
        )
    return items


# --- Run --------------------------------------------------------------------------------


def ensure_gallery(api: Api, journal: Journal, gallery: str | None) -> str:
    gallery = gallery or journal.gallery
    if gallery:
        api.call("GET", f"galleries/{gallery}/")  # 404 if it isn't this photographer's
    else:
        event = api.call(
            "POST",
            "events/",
            {
                "title": f"Upload drill {time.strftime('%Y-%m-%d %H:%M')}",
                "event_date": str(date.today()),
            },
        )
        gallery = api.call("POST", "galleries/", {"event": event["id"], "title": "Drill"})["id"]
    if journal.gallery != gallery:
        journal.gallery = gallery
        journal.write(gallery=gallery)
    return gallery


def check_quota(api: Api, items: list[Item], journal: Journal) -> None:
    usage = api.call("GET", "storage/usage/")
    needed = sum(
        i.size for i in items if i.name not in journal.completed and i.name not in journal.photos
    )
    free = usage["quota_bytes"] - usage["used_bytes"]
    if needed > free:
        raise SystemExit(
            f"Needs {mb(needed)} but only {mb(free)} of quota is free. Raise the photographer's "
            "quota in the admin, or upload fewer photos."
        )


def count(api: Api, gallery: str, status: str) -> int:
    return api.call("GET", f"photos/?gallery={gallery}&status={status}")["count"]


def wait_for_processing(api: Api, gallery: str, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        processing = count(api, gallery, "processing")
        if processing == 0 or time.monotonic() > deadline:
            break
        print(f"  waiting for processing: {processing} left", flush=True)
        time.sleep(POLL_SECONDS)
    failed = api.call("GET", f"photos/?gallery={gallery}&status=failed")["results"]
    return {
        "ready": count(api, gallery, "ready"),
        "processing": processing,
        "failed": count(api, gallery, "failed"),
        "failed_examples": [
            {"file": p["original_filename"], "reason": p["failure_reason"]} for p in failed[:10]
        ],
    }


def mb(size: float) -> str:
    return f"{size / 1_000_000:,.1f} MB"


def run_pass(
    uploader: Uploader, items: list[Item], concurrency: int, label: str
) -> list[FileResult]:
    results: list[FileResult] = []
    began = time.monotonic()
    sent = 0
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(uploader.upload, item) for item in items]
        try:
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                sent += result.bytes_sent
                if len(results) % PROGRESS_EVERY == 0 or len(results) == len(items):
                    elapsed = time.monotonic() - began
                    rate = sent * 8 / elapsed / 1000 if elapsed else 0
                    failed = sum(not r.ok for r in results)
                    print(
                        f"  {label}: {len(results)}/{len(items)} files, {mb(sent)} sent, "
                        f"{rate:,.0f} kbit/s, {failed} failed",
                        flush=True,
                    )
        except KeyboardInterrupt:
            pool.shutdown(wait=False, cancel_futures=True)
            print("\nStopped. Rerun with the same --journal to resume.", file=sys.stderr)
            raise SystemExit(130) from None
    return results


def summarize(
    first: list[FileResult], retried: list[FileResult], final: list[FileResult], seconds: float
) -> dict:
    uploaded = [r for r in final if r.ok and not r.skipped]
    durations = sorted(r.seconds for r in uploaded) or [0.0]
    sent = sum(r.bytes_sent for r in first + retried)
    return {
        "files": len(final),
        "uploaded": len(uploaded),
        "skipped_already_done": sum(r.skipped for r in final),
        "failed_after_auto_retries": sum(not r.ok for r in first),
        "failed_after_retry_pass": sum(not r.ok for r in final),
        "retries": sum(r.retries for r in first + retried),
        "injected_drops": sum(r.drops for r in first + retried),
        "bytes_sent": sent,
        "wall_seconds": round(seconds, 1),
        "throughput_kbps": round(sent * 8 / seconds / 1000, 1) if seconds else 0,
        "seconds_per_file_p50": round(statistics.median(durations), 1),
        "seconds_per_file_p95": round(durations[int(0.95 * (len(durations) - 1))], 1),
        "errors": [{"file": r.name, "error": r.error} for r in final if not r.ok][:20],
    }


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base-url", required=True, help="e.g. http://127.0.0.1:8000")
    parser.add_argument("--email", required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--image-dir", type=Path, help="Folder of real photos (cycled as needed)")
    source.add_argument("--synthetic", action="store_true", help="Generate photos with pyvips")
    parser.add_argument("--photos", type=int, default=1000)
    parser.add_argument("--concurrency", type=int, default=3, help="Files at once (dashboard: 3)")
    parser.add_argument("--gallery", help="Upload into this gallery id instead of a new one")
    parser.add_argument("--journal", type=Path, help="Resume file; reuse it to continue a run")
    parser.add_argument("--report", type=Path, help="Write the summary here as JSON")
    parser.add_argument("--bandwidth-kbps", type=int, help="Cap the whole link, e.g. 1000")
    parser.add_argument(
        "--drop-rate", type=float, default=0.0, help="Share of transfers cut mid-way"
    )
    parser.add_argument(
        "--retry-delays", default=DEFAULT_RETRY_DELAYS, help="Seconds, comma-separated"
    )
    parser.add_argument("--no-retry-pass", action="store_true", help="Skip the 'retry failed' pass")
    parser.add_argument("--processing-timeout", type=float, default=1800, help="Seconds to wait")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args(argv)
    if not 0 <= args.drop_rate < 1:
        parser.error("--drop-rate must be in [0, 1)")
    if args.photos < 1 or args.concurrency < 1:
        parser.error("--photos and --concurrency must be at least 1")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    password = os.environ.get("FOTOHUB_PASSWORD") or getpass.getpass(f"Password for {args.email}: ")
    journal = Journal(args.journal)
    api = Api(args.base_url)
    api.login(args.email, password)

    with tempfile.TemporaryDirectory(prefix="fotohub-drill-") as scratch:
        if args.synthetic:
            print("Generating synthetic photos...", flush=True)
            sources = synthetic_photos(Path(scratch), min(args.photos, 8), args.seed)
        else:
            sources = photos_from_dir(args.image_dir)
        items = build_items(sources, args.photos)
        gallery = ensure_gallery(api, journal, args.gallery)
        check_quota(api, items, journal)
        total = sum(i.size for i in items)
        print(f"Uploading {len(items)} photos ({mb(total)}) into gallery {gallery}", flush=True)

        uploader = Uploader(
            api,
            gallery,
            journal,
            Bandwidth(args.bandwidth_kbps),
            args.drop_rate,
            [float(s) for s in args.retry_delays.split(",") if s.strip()],
            random.Random(args.seed),
        )
        began = time.monotonic()
        first = run_pass(uploader, items, args.concurrency, "upload")
        retried: list[FileResult] = []
        failed_names = {r.name for r in first if not r.ok}
        if failed_names and not args.no_retry_pass:
            # What the photographer does next: press "retry failed" once.
            print(f"Retrying {len(failed_names)} failed files, as a photographer would", flush=True)
            failed = [i for i in items if i.name in failed_names]
            retried = run_pass(uploader, failed, args.concurrency, "retry")
        by_name = {r.name: r for r in retried}
        final = [by_name.get(r.name, r) for r in first]
        seconds = time.monotonic() - began

    summary = summarize(first, retried, final, seconds)
    summary["gallery"] = gallery
    summary["processing"] = wait_for_processing(api, gallery, args.processing_timeout)
    print(json.dumps(summary, indent=2))
    if args.report:
        args.report.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    processing = summary["processing"]
    passed = (
        summary["failed_after_retry_pass"] == 0
        and processing["failed"] == 0
        and processing["processing"] == 0
    )
    print("PASS" if passed else "FAIL", flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
