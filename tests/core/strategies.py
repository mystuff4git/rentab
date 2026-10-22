"""Случайные сценарии для property-тестов аналитики (hypothesis).

Фирма — 1–3 сотрудника с окладом и взносами или суммой в месяц (в том числе
«из прибыли»), накладные; режим — любой режим РФ и РК; проект — 1–2 этапа,
любая модель цены и режим НДС, собственный расход.
"""

from __future__ import annotations

from decimal import Decimal

from hypothesis import strategies as st

from rentab_core.models import (
    Assignment,
    AusnProfile,
    CappedPricing,
    Country,
    Disbursement,
    DisbursementKind,
    Employee,
    ExchangeRates,
    Firm,
    FirmFacts,
    FixedPricing,
    HourlyPricing,
    Jurisdiction,
    JurisdictionMode,
    KzForm,
    KzOurProfile,
    KzVat,
    MonthlyCost,
    NpdClientType,
    NpdProfile,
    OsnoProfile,
    Overhead,
    Period,
    PricingModel,
    Project,
    Role,
    Salary,
    Scenario,
    Stage,
    UsnObject,
    UsnProfile,
    UsnVat,
    VatMode,
)
from rentab_core.money import Currency

NAMES = ("Е1", "Е2", "Е3")
RfProfile = UsnProfile | AusnProfile | OsnoProfile | NpdProfile
RUB_PER_KZT = Decimal("0.2")

RF_PROFILES: tuple[RfProfile, ...] = (
    *(UsnProfile(vat=vat) for vat in UsnVat),
    *(UsnProfile(tax_object=UsnObject.INCOME_MINUS_EXPENSES, vat=vat) for vat in UsnVat),
    AusnProfile(),
    NpdProfile(client_type=NpdClientType.INDIVIDUAL),
    NpdProfile(),
    OsnoProfile(),
)
KZ_PROFILES: tuple[KzOurProfile, ...] = tuple(
    KzOurProfile(form=form, vat=vat) for form in KzForm for vat in KzVat
)


def amounts(low: int, high: int) -> st.SearchStrategy[Decimal]:
    """Сумма с копейками в [low; high]."""
    return st.integers(low * 100, high * 100).map(lambda cents: Decimal(cents) / 100)


def fractions(low: str, high: str) -> st.SearchStrategy[Decimal]:
    """Доля с точностью 0,001 в [low; high]."""
    return st.integers(int(Decimal(low) * 1000), int(Decimal(high) * 1000)).map(
        lambda value: Decimal(value) / 1000
    )


@st.composite
def employees(draw: st.DrawFn, name: str) -> Employee:
    compensation = draw(
        st.one_of(
            st.builds(MonthlyCost, amount=amounts(10_000, 500_000)),
            st.builds(
                Salary,
                gross_salary=amounts(10_000, 300_000),
                employer_contribution_rate=fractions("0", "0.35"),
            ),
        )
    )
    return Employee(
        name=name,
        role=draw(st.sampled_from(Role)),
        compensation=compensation,
        currency=draw(st.sampled_from([Currency.RUB, Currency.RUB, Currency.KZT])),
        billing_rate=draw(amounts(0, 20_000)),
        contract_hours_per_month=draw(st.integers(100, 200).map(Decimal)),
        target_utilization=draw(fractions("0.3", "1")),
        paid_from_profit=draw(st.booleans()),
    )


pricing_models: st.SearchStrategy[PricingModel] = st.one_of(
    st.just(HourlyPricing()),
    st.builds(CappedPricing, cap=amounts(1_000, 1_000_000)),
    st.builds(FixedPricing, price=amounts(1_000, 1_000_000)),
    st.just(FixedPricing()),
)


@st.composite
def scenarios(draw: st.DrawFn, *, with_kz: bool = True) -> Scenario:
    """Случайный сценарий: фирма, режим, проект."""
    team = tuple(draw(employees(name)) for name in NAMES[: draw(st.integers(1, 3))])
    is_kz = with_kz and draw(st.integers(0, len(RF_PROFILES) + len(KZ_PROFILES) - 1)) >= len(
        RF_PROFILES
    )
    jurisdiction = (
        Jurisdiction(
            mode=JurisdictionMode.BOTH, rf=UsnProfile(), kz=draw(st.sampled_from(KZ_PROFILES))
        )
        if is_kz
        else Jurisdiction(rf=draw(st.sampled_from(RF_PROFILES)))
    )
    overheads = tuple(
        Overhead(name=f"Накладные {index}", amount=draw(amounts(0, 200_000)), period=Period.MONTHLY)
        for index in range(draw(st.integers(0, 2)))
    )
    names = [employee.name for employee in team]
    stages = tuple(
        Stage(
            name=f"Этап {index + 1}",
            assignments=tuple(
                Assignment(employee=draw(st.sampled_from(names)), hours=draw(amounts(1, 80)))
                for _ in range(draw(st.integers(1, 3)))
            ),
        )
        for index in range(draw(st.integers(1, 2)))
    )
    own = draw(st.none() | amounts(1, 50_000))
    project = Project(
        name="Случайный проект",
        pricing=draw(pricing_models),
        target_margin=draw(fractions("0", "0.6")),
        vat_mode=draw(st.sampled_from(VatMode)),
        stages=stages,
        disbursements=()
        if own is None
        else (Disbursement(name="Свой расход", kind=DisbursementKind.OWN, amount=own),),
        calc_country=Country.KZ if is_kz else None,
    )
    firm = Firm(
        jurisdiction=jurisdiction,
        team=team,
        overheads=overheads,
        exchange_rates=ExchangeRates(rub_per_kzt=RUB_PER_KZT),
        facts=FirmFacts(annual_income_rf=draw(st.none() | amounts(0, 600_000_000))),
    )
    return Scenario(firm=firm, project=project)
