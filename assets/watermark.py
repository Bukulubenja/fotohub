"""Watermark for preview images (blueprint: previews carry a subtle watermark).

The photographer's name, tiled diagonally across the whole frame so cropping can't remove it,
in translucent white over a faint dark shadow so it reads on light and dark photos alike.
Only previews are marked; originals and paid downloads never are.
"""

import math
from html import escape
from pathlib import Path

import pyvips
from django.conf import settings
from django.core import checks

# Server images have no system fonts, so text is rendered from a font we ship: a static TTF,
# because libvips' FreeType can't read the woff2 web font (it silently falls back to sans).
FONT_FILE = Path(settings.BASE_DIR) / "assets/fonts/Inter-SemiBold-latin.ttf"
FONT = "Inter SemiBold"
ANGLE = -30
TEXT_OPACITY = 0.30
SHADOW_OPACITY = 0.14
SHADOW_OFFSET = 2


def _text_mask(label: str, size: int) -> pyvips.Image:
    """One band, 0-255: the label rendered at roughly `size` pixels tall."""
    return pyvips.Image.text(escape(label), font=f"{FONT} {size}", fontfile=str(FONT_FILE), dpi=72)


def _tiled_mask(label: str, width: int, height: int) -> pyvips.Image:
    size = max(12, round(width / 24))
    text = _text_mask(label, size)
    # Space the repeats a little more than one label apart, then tilt the whole tile.
    cell = text.embed(size, size * 2, text.width + size * 3, text.height + size * 4)
    cell = cell.rotate(ANGLE, background=[0])
    across = math.ceil(width / cell.width) + 1
    down = math.ceil(height / cell.height) + 1
    return cell.replicate(across, down).crop(0, 0, width, height)


def apply(image: pyvips.Image, label: str) -> pyvips.Image:
    if not label.strip():
        return image
    mask = _tiled_mask(label, image.width, image.height) / 255
    shadow = mask.embed(SHADOW_OFFSET, SHADOW_OFFSET, image.width, image.height)
    alpha = image[image.bands - 1] if image.hasalpha() else None
    colour = image[: image.bands - 1] if alpha is not None else image
    colour = colour * (1 - SHADOW_OPACITY * shadow)
    colour = colour * (1 - TEXT_OPACITY * mask) + 255 * TEXT_OPACITY * mask
    colour = colour.cast("uchar")
    return (
        colour.bandjoin(alpha)
        if alpha is not None
        else colour.copy(interpretation=image.interpretation)
    )


@checks.register()
def check_watermark_font(app_configs, **kwargs) -> list[checks.CheckMessage]:
    """Fail loudly (migrate runs checks on deploy) if previews would go out unmarked or garbled."""
    try:
        mask = _text_mask("Watermark check", 40)
    except pyvips.Error as exc:
        return [checks.Error(f"Watermark text can't be rendered: {exc}", id="assets.E001")]
    # At 40 px the check text inks about 340 x 29 in Inter; with no usable font it came out
    # 118 x 12 on the server image.
    if mask.height < 20 or mask.width < 200:
        return [
            checks.Error(
                f"Watermark text renders at {mask.width}x{mask.height}: the font isn't loading.",
                hint=f"Check that {FONT_FILE} exists and fontconfig is installed in the image.",
                id="assets.E002",
            )
        ]
    fallback = pyvips.Image.text("Watermark check", font="sans 40", dpi=72)
    if mask.write_to_memory() == fallback.write_to_memory():
        # Still a readable watermark, just not in Inter.
        return [
            checks.Warning(
                "Watermarks use the system sans font: Inter didn't load.",
                hint=f"On Linux, check that {FONT_FILE} exists and fontconfig is installed.",
                id="assets.W001",
            )
        ]
    return []
