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


def test_check_never_renders_text_in_this_process(monkeypatch):
    """Regression: Celery runs checks in the worker parent before forking; text rendered
    there made every forked child hang (production, 2026-10-26)."""

    def forbidden(*args, **kwargs):
        raise AssertionError("the check must render text in a subprocess")

    monkeypatch.setattr(pyvips.Image, "text", forbidden)

    assert watermark.check_watermark_font(None) == []


def fake_probe(result):
    return lambda: result


def test_check_fails_when_text_comes_out_tiny(monkeypatch):
    monkeypatch.setattr(
        watermark, "_probe", fake_probe({"width": 118, "height": 12, "same_as_sans": False})
    )

    assert [e.id for e in watermark.check_watermark_font(None)] == ["assets.E002"]


def test_check_fails_when_text_cannot_render(monkeypatch):
    def broken():
        raise RuntimeError("no pango")

    monkeypatch.setattr(watermark, "_probe", broken)

    assert [e.id for e in watermark.check_watermark_font(None)] == ["assets.E001"]


def test_check_warns_when_inter_falls_back_to_the_system_font(monkeypatch):
    monkeypatch.setattr(
        watermark, "_probe", fake_probe({"width": 340, "height": 29, "same_as_sans": True})
    )

    assert [e.id for e in watermark.check_watermark_font(None)] == ["assets.W001"]
