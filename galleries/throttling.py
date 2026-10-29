from django.http import HttpRequest
from rest_framework.throttling import SimpleRateThrottle


class MissThrottle(SimpleRateThrottle):
    """Counts only failed attempts (wrong passkeys, wrong passwords), never right ones, so
    visitors who know the secret can't be locked out by someone guessing, or use up each
    other's allowance behind one shared IP. Uses DRF's rate parsing and cache history, so the
    rate lives with the other throttles. Keyed by `ident`, or by the visitor's IP without one."""

    def __init__(self, ident: str = "") -> None:
        super().__init__()
        self.ident = ident

    def get_cache_key(self, request, view) -> str:
        return self.cache_format % {
            "scope": self.scope,
            "ident": self.ident or self.get_ident(request),
        }

    def _recent(self, request: HttpRequest) -> list[float]:
        self.key = self.get_cache_key(request, None)
        self.now = self.timer()
        return [t for t in self.cache.get(self.key, []) if t > self.now - self.duration]

    def blocked(self, request: HttpRequest) -> bool:
        return len(self._recent(request)) >= self.num_requests

    def record(self, request: HttpRequest) -> None:
        recent = self._recent(request)
        self.cache.set(self.key, [self.now, *recent], self.duration)


class GalleryPasswordMisses(MissThrottle):
    """Wrong passwords per gallery across all visitors (`gallery_unlock_global`); per-visitor
    attempts are capped separately by the `gallery_unlock` scope."""

    scope = "gallery_unlock_global"
