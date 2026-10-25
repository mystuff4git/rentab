"""Примеры документов демо-фирмы для `docs/examples/`: КП в PDF и DOCX, смета XLSX.

Три типовых проекта демо-фирмы — почасовая модель, фикс «издержки + маржа» и
почасовая с потолком — с датой документа 25.10.2026. Перегенерировать:

    uv run python -m tests.export_examples docs/examples
    uv run python -m tests.export_examples docs/examples --png   # и PNG страниц PDF (pdftoppm)

Актуальность примеров проверяет `tests/core/test_examples.py`.
"""

from __future__ import annotations

import datetime as dt
import shutil
import subprocess
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path

from rentab_core.demo import demo_workspace
from rentab_core.documents import OfferTerms
from rentab_core.estimate import calculate_estimate
from rentab_core.export import estimate_xlsx, offer_document, offer_docx, offer_pdf
from rentab_core.labels import regime_label

ISSUE_DATE = dt.date(2026, 10, 25)
# Проект демо-фирмы (номер в демо) → имя файлов примера.
EXAMPLES: dict[int, str] = {
    1: "demo_trademark_hourly",
    2: "demo_invention_fixed",
    3: "demo_office_action_capped",
}
PNG_RESOLUTION_DPI = 110


def documents() -> Iterator[tuple[str, bytes]]:
    """Имя файла и содержимое каждого примера."""
    workspace = demo_workspace()
    firm = workspace.firm
    for index, stem in EXAMPLES.items():
        record = workspace.projects[index]
        scenario = record.scenario(firm)
        estimate = calculate_estimate(scenario)
        offer = offer_document(
            scenario,
            estimate,
            issue_date=ISSUE_DATE,
            letterhead=workspace.letterhead,
            terms=record.offer,
        )
        profile = firm.jurisdiction.tax_profile(firm.jurisdiction.calc_country())
        yield f"{stem}.pdf", offer_pdf(offer)
        yield f"{stem}.docx", offer_docx(offer)
        yield (
            f"{stem}.xlsx",
            estimate_xlsx(
                estimate,
                scenario=scenario,
                firm_name=firm.name,
                issue_date=ISSUE_DATE,
                regime_label=regime_label(profile),
                validity_days=(record.offer or OfferTerms()).validity_days,
            ),
        )


def main(argv: Sequence[str]) -> None:
    """Пишет примеры в каталог из аргументов; с `--png` — и страницы PDF в PNG."""
    target = Path(argv[0])
    target.mkdir(parents=True, exist_ok=True)
    for name, data in documents():
        (target / name).write_bytes(data)
        print(f"{target / name}: {len(data):,} байт".replace(",", " "))
    if "--png" in argv:
        pdftoppm = shutil.which("pdftoppm")
        if pdftoppm is None:
            raise SystemExit("pdftoppm (poppler-utils) не найден: PNG страниц не созданы")
        for stem in EXAMPLES.values():
            subprocess.run(
                [
                    pdftoppm,
                    "-png",
                    "-r",
                    str(PNG_RESOLUTION_DPI),
                    str(target / f"{stem}.pdf"),
                    str(target / stem),
                ],
                check=True,
            )


if __name__ == "__main__":
    main(sys.argv[1:])
