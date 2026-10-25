"""The photographer's brand colour, made safe for the customer gallery's buttons.

Photographers pick any colour. Navy vanishes on the dark theme, and yellow under white text
can't be read. So each theme gets the brand colour nudged until the button stands out from
the page (WCAG 3:1 for UI shapes), with whichever text colour reads on it (4.5:1 or more).
"""

import re

from django import template

register = template.Library()

DEFAULT = "#172033"
# Page backgrounds of the two themes; keep in step with --bg in galleries/public/base.html.
BACKGROUNDS = {"light": "#f7f5f1", "dark": "#121110"}
INKS = ("#ffffff", "#121110")
MIN_SHAPE_CONTRAST = 3.0
STEP = 0.05  # how far each nudge mixes the colour toward white or black
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def _hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{round(channel):02x}" for channel in rgb)


def _luminance(color: str) -> float:
    def linear(channel: int) -> float:
        value = channel / 255
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    r, g, b = (linear(channel) for channel in _rgb(color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    darker, lighter = sorted((_luminance(a), _luminance(b)))
    return (lighter + 0.05) / (darker + 0.05)


def _mix(color: str, target: str, amount: float) -> str:
    pairs = zip(_rgb(color), _rgb(target), strict=True)
    return _hex(tuple(c + (t - c) * amount for c, t in pairs))


@register.filter
def accent(color: str, theme: str) -> str:
    """The brand colour for one theme: lightened on dark pages, darkened on light ones."""
    color = color if isinstance(color, str) and HEX.match(color) else DEFAULT
    background = BACKGROUNDS[theme]
    target = "#ffffff" if theme == "dark" else "#000000"
    amount = 0.0
    adjusted = color.lower()
    while contrast(adjusted, background) < MIN_SHAPE_CONTRAST and amount < 1:
        amount = min(1.0, amount + STEP)
        adjusted = _mix(color, target, amount)
    return adjusted


@register.filter
def ink(color: str) -> str:
    """White or near-black, whichever reads better on `color`."""
    return max(INKS, key=lambda candidate: contrast(candidate, color))
