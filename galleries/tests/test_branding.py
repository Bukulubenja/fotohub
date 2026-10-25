"""Brand colours stay usable as button colours in both gallery themes (WCAG contrast)."""

import pytest
from django.template import Context, Template

from galleries.templatetags.branding import BACKGROUNDS, DEFAULT, accent, contrast, ink

BRANDS = ["#172033", "#f5c518", "#ffffff", "#000000", "#e11d48", "#22d3ee"]


@pytest.mark.parametrize("brand", BRANDS)
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_button_always_stands_out_from_the_page(brand, theme):
    assert contrast(accent(brand, theme), BACKGROUNDS[theme]) >= 3


@pytest.mark.parametrize("brand", BRANDS)
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_button_text_is_readable(brand, theme):
    color = accent(brand, theme)

    assert contrast(ink(color), color) >= 4.5


def test_a_colour_that_already_works_is_left_alone():
    assert accent("#172033", "light") == "#172033"


def test_navy_is_lightened_for_the_dark_theme():
    assert accent("#172033", "dark") != "#172033"


def test_light_brand_gets_dark_text():
    assert ink("#f5c518") == "#121110"
    assert ink("#172033") == "#ffffff"


@pytest.mark.parametrize("bad", ["", None, "red", "#fff", "#12345g", "#123456; color: red"])
def test_anything_but_a_hex_colour_falls_back_to_the_default(bad):
    assert accent(bad, "light") == accent(DEFAULT, "light")


def test_base_template_writes_both_themes():
    html = Template(
        '{% load branding %}{{ c|accent:"light" }} {{ c|accent:"dark" }} {{ c|accent:"dark"|ink }}'
    ).render(Context({"c": "#172033"}))

    light, dark, text = html.split()
    assert light == "#172033"
    assert contrast(dark, BACKGROUNDS["dark"]) >= 3
    assert text in ("#ffffff", "#121110")
