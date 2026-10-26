"""Preview watermark: keeps the image's shape and transparency, and the deploy check."""

import pytest
import pyvips

from assets import watermark


def solid(bands: int) -> pyvips.Image:
    image = (pyvips.Image.black(600, 400, bands=bands) + 128).cast("uchar")
    return image.copy(interpretation="srgb")


@pytest.mark.parametrize("bands", [3, 4])
def test_keeps_size_bands_and_alpha(bands):
    image = solid(bands)

    marked = watermark.apply(image, "\u00a9 Lens & Light")

    assert (marked.width, marked.height, marked.bands) == (600, 400, bands)
    assert marked.format == "uchar"
    if bands == 4:
        assert marked[3].min() == marked[3].max() == 128  # alpha untouched
    assert marked[0].max() > 128  # the light text is there


def test_blank_label_leaves_the_image_alone():
    image = solid(3)

    assert watermark.apply(image, "  ") is image


def test_check_passes_with_the_shipped_font():
    assert watermark.check_watermark_font(None) == []


def test_check_fails_when_text_comes_out_tiny(monkeypatch):
    monkeypatch.setattr(watermark, "_text_mask", lambda label, size: pyvips.Image.black(60, 8))

    assert [e.id for e in watermark.check_watermark_font(None)] == ["assets.E002"]


def test_check_fails_when_text_cannot_render(monkeypatch):
    def broken(label, size):
        raise pyvips.Error("no pango")

    monkeypatch.setattr(watermark, "_text_mask", broken)

    assert [e.id for e in watermark.check_watermark_font(None)] == ["assets.E001"]


def test_check_warns_when_inter_falls_back_to_the_system_font(monkeypatch):
    def system_sans(label, size):
        return pyvips.Image.text(label, font=f"sans {size}", dpi=72)

    monkeypatch.setattr(watermark, "_text_mask", system_sans)

    assert [e.id for e in watermark.check_watermark_font(None)] == ["assets.W001"]
