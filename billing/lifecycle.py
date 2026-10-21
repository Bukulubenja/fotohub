"""Subscription lifecycle (spec FR-29, plan §3.5). Derived from `paid_until`, never stored,
so there's no job that has to run on time for the state to be right.

    active ── D-3 ──> due ── D+0 ──> grace ── D+7 ──> lapsed ── D+37 ──> galleries hidden

- due:    a reminder goes out; everything still works.
- grace:  uploads stop; customer galleries stay live.
- lapsed: galleries stay viewable for a while, then are hidden. Photos are never deleted
          automatically; that needs several warnings and a separate, deliberate process.
"""

import datetime
from dataclasses import dataclass
from enum import StrEnum

from django.conf import settings


class State(StrEnum):
    ACTIVE = "active"
    DUE = "due"
    GRACE = "grace"
    LAPSED = "lapsed"


@dataclass(frozen=True)
class Standing:
    state: State
    can_upload: bool
    galleries_live: bool


def _days(name: str) -> datetime.timedelta:
    return datetime.timedelta(days=settings.BILLING[name])


def standing(paid_until: datetime.datetime, now: datetime.datetime) -> Standing:
    grace_ends = paid_until + _days("GRACE_DAYS")
    if now < paid_until - _days("DUE_DAYS"):
        state = State.ACTIVE
    elif now < paid_until:
        state = State.DUE
    elif now < grace_ends:
        state = State.GRACE
    else:
        state = State.LAPSED
    return Standing(
        state=state,
        can_upload=state in (State.ACTIVE, State.DUE),
        galleries_live=now < grace_ends + _days("LAPSED_VISIBLE_DAYS"),
    )


def extended(paid_until: datetime.datetime, now: datetime.datetime, days: int) -> datetime.datetime:
    """New paid-until after paying for `days`: from today if lapsed, else from the old end."""
    return max(paid_until, now) + datetime.timedelta(days=days)
