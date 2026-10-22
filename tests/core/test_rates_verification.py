"""Сводка сверки `docs/rates_verification.md` совпадает с реестрами и каталогами."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from rentab_core.money import format_number
from rentab_core.rates.verification import (
    Confidence,
    confidence,
    format_percent,
    main,
    verification_markdown,
)

DOC_PATH = Path(__file__).parents[2] / "docs" / "rates_verification.md"


def test_document_is_up_to_date(request: pytest.FixtureRequest) -> None:
    text = verification_markdown()
    if request.config.getoption("--update-snapshots"):
        DOC_PATH.write_text(text, encoding="utf-8")
        return
    assert DOC_PATH.read_text(encoding="utf-8") == text, (
        "docs/rates_verification.md устарел: "
        "uv run python -m rentab_core.rates.verification docs/rates_verification.md"
    )


def test_document_lists_every_key() -> None:
    text = verification_markdown()
    for key in ("`usn.min_tax`", "`social_tax`", "`ndfl.resident`", "МРП", "Казпатент"):
        assert key in text
    assert "| `usn.min_tax` | 1% |" in text
    assert "10 000 МРП = 43 250 000 ₸ в 2026 году" in text


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (Decimal(1234567), "1 234 567"),
        (Decimal("88359.38"), "88 359,38"),
        (Decimal("0.50"), "0,5"),
        (Decimal(-1500), "-1 500"),
        (Decimal(100), "100"),
    ],
)
def test_format_number(value: Decimal, text: str) -> None:
    assert format_number(value) == text


def test_format_percent_and_confidence() -> None:
    assert format_percent(Decimal("0.151")) == "15,1%"
    assert confidence(needs_review=False, has_source=True) == Confidence.HIGH
    assert confidence(needs_review=True, has_source=True) == Confidence.MEDIUM
    assert confidence(needs_review=True, has_source=False) == Confidence.LOW


def test_main_writes_file_or_stdout(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "doc.md"
    main([str(target)])
    assert target.read_text(encoding="utf-8") == verification_markdown()
    main([])
    assert capsys.readouterr().out == verification_markdown()
