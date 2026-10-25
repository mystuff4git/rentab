"""Коммерческое предложение (КП): модель документа по смете.

Одна модель — для DOCX и PDF (`export.docx`, `export.pdf`): что написано и
какие суммы, без оформления. Суммы — без НДС, до копейки («половина вверх»):

    Стоимость услуг без НДС  = R,      округлённая до копейки
    НДС                      = v × R,  округлённый до копейки
    Возмещаемые расходы      = Σ D_i,  каждый округлён до копейки
    Итого к оплате           = сумма трёх строк выше

Цена этапа фикса — доля R по теневой стоимости (`StageResult.revenue`, L-17),
сумма исполнителя почасовой модели — ставка × часы без НДС. Копеечный остаток
округления получает строка с наибольшим остатком, поэтому строки в сумме дают
итог ровно (docs/methodology.md, раздел 20).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from decimal import ROUND_FLOOR, Decimal
from enum import StrEnum

from rentab_core.documents import Letterhead, OfferTerms
from rentab_core.formatting import KOPECK, money, rate_text
from rentab_core.labels import employee_title
from rentab_core.models import (
    CappedPricing,
    CoreModel,
    FixedPricing,
    Scenario,
    VatMode,
)
from rentab_core.money import CURRENCY_SYMBOLS, ONE, ZERO, decimal_sum, round_to
from rentab_core.results import Estimate, EstimateLine

TITLE = "Коммерческое предложение"
DATE_FORMAT = "%d.%m.%Y"
UNTITLED_PROJECT = "Без названия"
SINGLE_SERVICE = "Услуги по проекту"
NO_VAT = "Без НДС"
DISBURSEMENTS_TITLE = "Возмещаемые расходы (пошлины и иные платежи)"
DISBURSEMENTS_TOTAL = "Итого возмещаемых расходов"
SERVICES_NET = "Стоимость услуг без НДС"
SERVICES_GROSS = "Стоимость услуг с НДС"
SHADOW_NET = "Стоимость по ставкам"
WRITE_OFF = "Скидка до предельной стоимости"
TOTAL = "Итого к оплате"

PRICE_HOURLY = "почасовая"
PRICE_CAPPED = "почасовая с предельной стоимостью"
PRICE_FIXED = "фиксированная"
NOTE_HOURLY = (
    "Стоимость рассчитана по плановым часам; оплачиваются фактически затраченные часы "
    "по указанным ставкам."
)
NOTE_CAPPED = (
    "Оплачиваются фактически затраченные часы по указанным ставкам, но не более {cap} без НДС."
)
NOTE_FIXED = "Стоимость работ фиксированная и не зависит от фактически затраченного времени."
NOTE_DISBURSEMENTS = (
    "Возмещаемые расходы (пошлины) оплачиваются по суммам, действующим на дату оплаты, "
    "и в стоимость услуг не входят."
)
NOTE_PAYMENT = "Порядок оплаты: {terms}."
NOTE_EXECUTION = "Срок выполнения: {terms}."
NOTE_VALIDITY = "Предложение действительно до {date}."
KOPECKS = Decimal(100)


class PriceKind(StrEnum):
    """Как в КП показана стоимость работ."""

    HOURLY = "hourly"  # этапы с исполнителями, часами и ставками
    CAPPED = "capped"  # то же и скидка до потолка
    FIXED = "fixed"  # этапы с ценой, без часов и ставок


class OfferLine(CoreModel):
    """Исполнитель этапа почасовой модели.

    Attributes:
        title: «Имя (роль)».
        hours: Часы.
        rate: Ставка без НДС, до копейки.
        amount: Сумма без НДС, до копейки (с распределённым остатком).
    """

    title: str
    hours: Decimal
    rate: Decimal
    amount: Decimal


class OfferStage(CoreModel):
    """Этап КП: название, описание результата и стоимость без НДС до копейки."""

    number: int
    name: str
    description: str = ""
    amount: Decimal
    hours: Decimal = ZERO
    lines: tuple[OfferLine, ...] = ()


class OfferAmount(CoreModel):
    """Строка суммы (расход, итог): подпись и сумма до копейки."""

    label: str
    amount: Decimal


class OfferDocument(CoreModel):
    """Содержимое КП: шапка, работы, суммы и условия. Суммы — до копейки, без НДС.

    Attributes:
        firm_name: Название фирмы.
        letterhead: Реквизиты, подписант и логотип.
        number: Номер КП; пусто — без номера.
        issue_date: Дата документа.
        valid_until: Дата, до которой действует предложение.
        client: Заказчик; пусто — строки нет.
        project_name: Проект.
        description: Задача (описание проекта); пусто — раздела нет.
        symbol: Знак валюты проекта.
        price_kind: Как показана стоимость работ.
        price_terms: Строка «Цена»: модель цены и НДС.
        stages: Этапы со стоимостью; суммы этапов = `services_net`.
        shadow_net: Стоимость по ставкам (почасовая модель и с потолком).
        write_off: Скидка до потолка; 0 — потолок не сработал.
        services_net: Стоимость услуг без НДС.
        vat_rate: Ставка НДС; 0 — «Без НДС».
        vat: НДС.
        disbursements: Возмещаемые расходы по позициям.
        disbursements_total: Итог возмещаемых расходов.
        total: Итого к оплате = услуги + НДС + возмещаемые расходы.
        conditions: Условия по порядку.
    """

    firm_name: str
    letterhead: Letterhead
    number: str
    issue_date: dt.date
    valid_until: dt.date
    client: str
    project_name: str
    description: str
    symbol: str
    price_kind: PriceKind
    price_terms: str
    stages: tuple[OfferStage, ...]
    shadow_net: Decimal
    write_off: Decimal
    services_net: Decimal
    vat_rate: Decimal
    vat: Decimal
    disbursements: tuple[OfferAmount, ...]
    disbursements_total: Decimal
    total: Decimal
    conditions: tuple[str, ...]

    @property
    def services_gross(self) -> Decimal:
        """Стоимость услуг с НДС."""
        return self.services_net + self.vat

    @property
    def vat_label(self) -> str:
        """Подпись строки НДС: «НДС 5%»."""
        return f"НДС {rate_text(self.vat_rate)}%"

    @property
    def subtitle(self) -> str:
        """«№ 7 от 25.10.2026» или «от 25.10.2026»."""
        date = self.issue_date.strftime(DATE_FORMAT)
        return f"№ {self.number} от {date}" if self.number else f"от {date}"

    @property
    def signature(self) -> str:
        """«ФИО, должность» подписанта; пусто — подписи нет."""
        parts = (self.letterhead.signatory_name, self.letterhead.signatory_position)
        return ", ".join(part for part in parts if part)

    @property
    def footer(self) -> str:
        """Нижний колонтитул: фирма и КП с номером и датой."""
        parts = [self.firm_name, f"{TITLE} {self.subtitle}"]
        return " · ".join(part for part in parts if part)


# ---------------------------------------------------------------------------
# Округление
# ---------------------------------------------------------------------------
def allocate(total: Decimal, parts: Sequence[Decimal]) -> list[Decimal]:
    """Делит сумму до копейки на части пропорционально точным долям.

    Каждая часть — точная доля, округлённая вниз до копейки; недостающие
    копейки получают части с наибольшим остатком (при равенстве — первые).
    Части неотрицательны, их сумма равна `total` ровно.

    Args:
        total: Итог до копейки — обычно точная сумма частей, округлённая.
        parts: Точные неотрицательные части.
    """
    if not parts:
        return []
    exact = decimal_sum(parts)
    scale = total / exact if exact > 0 else ZERO
    shares = [part * scale for part in parts]
    floors = [share.quantize(KOPECK, rounding=ROUND_FLOOR) for share in shares]
    missing = int(((total - decimal_sum(floors)) * KOPECKS).to_integral_value())
    order = sorted(range(len(parts)), key=lambda i: shares[i] - floors[i], reverse=True)
    for index in order[:missing]:
        floors[index] += KOPECK
    return floors


def _kopeck(value: Decimal) -> Decimal:
    return round_to(value, KOPECK)


# ---------------------------------------------------------------------------
# Построение
# ---------------------------------------------------------------------------
def _net_factor(estimate: Estimate) -> Decimal:
    """Множитель «цена в режиме НДС проекта → без НДС» для ставок."""
    if estimate.vat_mode == VatMode.INCLUDED:
        return ONE / (ONE + estimate.tax.terms.vat_rate)
    return ONE


def _price_kind(estimate: Estimate) -> PriceKind:
    if isinstance(estimate.pricing, FixedPricing):
        return PriceKind.FIXED
    if isinstance(estimate.pricing, CappedPricing):
        return PriceKind.CAPPED
    return PriceKind.HOURLY


def _price_terms(kind: PriceKind, vat_rate: Decimal) -> str:
    name = {
        PriceKind.HOURLY: PRICE_HOURLY,
        PriceKind.CAPPED: PRICE_CAPPED,
        PriceKind.FIXED: PRICE_FIXED,
    }[kind]
    if vat_rate > 0:
        return f"{name}, НДС {rate_text(vat_rate)}% начисляется сверху"
    return f"{name}, без НДС"


def _hourly_stages(
    estimate: Estimate, descriptions: dict[str, str], shadow_net: Decimal
) -> tuple[OfferStage, ...]:
    """Этапы почасовой модели: исполнители со ставками; суммы строк = стоимость по ставкам."""
    net = _net_factor(estimate)
    lines = list(estimate.lines)
    amounts = allocate(shadow_net, [line.shadow_value * net for line in lines])
    by_stage: dict[str, list[tuple[EstimateLine, Decimal]]] = {}
    for line, amount in zip(lines, amounts, strict=True):
        by_stage.setdefault(line.stage_name, []).append((line, amount))
    stages = []
    for number, stage in enumerate(estimate.stages, start=1):
        rows = by_stage.get(stage.stage_name, [])
        stages.append(
            OfferStage(
                number=number,
                name=stage.stage_name,
                description=descriptions.get(stage.stage_name, ""),
                amount=decimal_sum(amount for _, amount in rows),
                hours=stage.hours,
                lines=tuple(
                    OfferLine(
                        title=employee_title(line.employee_name, line.role),
                        hours=line.hours,
                        rate=_kopeck(line.billing_rate * net),
                        amount=amount,
                    )
                    for line, amount in rows
                ),
            )
        )
    return tuple(stages)


def _fixed_stages(
    estimate: Estimate, descriptions: dict[str, str], services_net: Decimal
) -> tuple[OfferStage, ...]:
    """Этапы фикса: доли цены по теневой стоимости (L-17); без этапов — одна строка."""
    if not estimate.stages:
        return (OfferStage(number=1, name=SINGLE_SERVICE, amount=services_net),)
    amounts = allocate(services_net, [stage.revenue for stage in estimate.stages])
    return tuple(
        OfferStage(
            number=number,
            name=stage.stage_name,
            description=descriptions.get(stage.stage_name, ""),
            amount=amount,
            hours=stage.hours,
        )
        for number, (stage, amount) in enumerate(
            zip(estimate.stages, amounts, strict=True), start=1
        )
    )


def _conditions(
    kind: PriceKind,
    estimate: Estimate,
    terms: OfferTerms,
    valid_until: dt.date,
    symbol: str,
    has_disbursements: bool,
) -> tuple[str, ...]:
    items: list[str] = []
    if kind == PriceKind.FIXED:
        items.append(NOTE_FIXED)
    elif kind == PriceKind.CAPPED:
        assert isinstance(estimate.pricing, CappedPricing)
        cap_net = estimate.pricing.cap * _net_factor(estimate)
        items.append(NOTE_CAPPED.format(cap=money(cap_net, symbol, KOPECK)))
    else:
        items.append(NOTE_HOURLY)
    if has_disbursements:
        items.append(NOTE_DISBURSEMENTS)
    if terms.payment_terms.strip():
        items.append(NOTE_PAYMENT.format(terms=terms.payment_terms.strip().rstrip(".")))
    if terms.execution_term.strip():
        items.append(NOTE_EXECUTION.format(terms=terms.execution_term.strip().rstrip(".")))
    items += [line.strip() for line in terms.extra_terms.splitlines() if line.strip()]
    items.append(NOTE_VALIDITY.format(date=valid_until.strftime(DATE_FORMAT)))
    return tuple(items)


def offer_document(
    scenario: Scenario,
    estimate: Estimate,
    *,
    issue_date: dt.date,
    letterhead: Letterhead | None = None,
    terms: OfferTerms | None = None,
) -> OfferDocument:
    """Содержимое КП по смете проекта.

    Args:
        scenario: Фирма и проект сметы: название фирмы и описания этапов.
        estimate: Смета (`calculate_estimate(scenario)`).
        issue_date: Дата документа; от неё отсчитывается срок действия.
        letterhead: Реквизиты и логотип; None — пустой бланк.
        terms: Условия КП; None — по умолчанию (срок действия 30 дней).
    """
    terms = terms or OfferTerms()
    symbol = CURRENCY_SYMBOLS[estimate.currency]
    kind = _price_kind(estimate)
    vat_rate = estimate.tax.terms.vat_rate
    descriptions = {stage.name: stage.description.strip() for stage in scenario.project.stages}

    services_net = _kopeck(estimate.revenue)
    shadow_net = services_net
    if kind == PriceKind.FIXED:
        stages = _fixed_stages(estimate, descriptions, services_net)
    else:
        shadow_net = _kopeck(estimate.shadow_value_net)
        stages = _hourly_stages(estimate, descriptions, shadow_net)
    vat = _kopeck(estimate.vat)
    disbursements = tuple(
        OfferAmount(label=item.name, amount=_kopeck(item.amount))
        for item in estimate.disbursements
        if item.billable and item.amount > 0
    )
    disbursements_total = decimal_sum(item.amount for item in disbursements)
    valid_until = issue_date + dt.timedelta(days=terms.validity_days)
    return OfferDocument(
        firm_name=scenario.firm.name.strip(),
        letterhead=letterhead or Letterhead(),
        number=terms.number.strip(),
        issue_date=issue_date,
        valid_until=valid_until,
        client=estimate.client.strip(),
        project_name=estimate.project_name.strip() or UNTITLED_PROJECT,
        description=estimate.description.strip(),
        symbol=symbol,
        price_kind=kind,
        price_terms=_price_terms(kind, vat_rate),
        stages=stages,
        shadow_net=shadow_net,
        write_off=shadow_net - services_net,
        services_net=services_net,
        vat_rate=vat_rate,
        vat=vat,
        disbursements=disbursements,
        disbursements_total=disbursements_total,
        total=services_net + vat + disbursements_total,
        conditions=_conditions(kind, estimate, terms, valid_until, symbol, bool(disbursements)),
    )
