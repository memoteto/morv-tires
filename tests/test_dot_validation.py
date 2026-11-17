"""DOT week validation regression tests; no database writes."""
from datetime import date
from unittest.mock import patch

import pytest

from morv.core import dot


@pytest.mark.parametrize("value,expected", [
    ("4526", "4526"),
    ("4626", "4626"),
    ("4523", "4523"),
    ("5323", "5323"),  # 2023 has a DOT week 53
    ("2020", "2020"),
    ("٢٣٢٤", "2324"),
])
def test_valid_dot_and_arabic_digits(value, expected):
    with patch("morv.core.dot.date", wraps=date) as mock_date:
        mock_date.today.return_value = date(2026, 11, 17)
        assert dot.parse_dot(value) == expected


@pytest.mark.parametrize("value", [
    "5426", "0026", "2045", "4527", "5325", "5320",
    "4726", "202024", "ab26",
])
def test_invalid_and_future_dot_rejected(value):
    with patch("morv.core.dot.date", wraps=date) as mock_date:
        mock_date.today.return_value = date(2026, 11, 17)
        with pytest.raises(dot.DotError):
            dot.parse_dot(value)


def test_current_week_accepted_only_after_its_start():
    with patch("morv.core.dot.date", wraps=date) as mock_date:
        mock_date.today.return_value = date(2026, 11, 14)
        with pytest.raises(dot.DotError):
            dot.parse_dot("4626")
        mock_date.today.return_value = date(2026, 11, 15)
        assert dot.parse_dot("4626") == "4626"


def test_week_53_starts_at_end_of_2023():
    with patch("morv.core.dot.date", wraps=date) as mock_date:
        mock_date.today.return_value = date(2023, 12, 30)
        with pytest.raises(dot.DotError):
            dot.parse_dot("5323")
        mock_date.today.return_value = date(2023, 12, 31)
        assert dot.parse_dot("5323") == "5323"


def test_existing_mfg_key_order_unchanged():
    with patch("morv.core.dot.date", wraps=date) as mock_date:
        mock_date.today.return_value = date(2026, 11, 17)
        assert dot.mfg_key("5223") < dot.mfg_key("0124")
        assert dot.mfg_key("4526") == 2645
