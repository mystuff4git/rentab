"""Взносы работодателя с оклада (docs/methodology.md, раздел 3.1.1).

Ожидаемые суммы посчитаны вручную по ставкам реестра 2026 года: база
2 979 000 ₽, МРОТ 27 093 ₽, МЗП 85 000 ₸, МРП 4 325 ₸.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rentab_core.models import (
    AusnProfile,
    Country,
    Jurisdiction,
    JurisdictionMode,
    KzForm,
    KzOurProfile,
    NpdProfile,
    OsnoProfile,
    SocialContributions,
    UsnProfile,
)
from rentab_core.tax.payroll import (
    RfTariff,
    contribution_country,
    employer_contribution_rate,
    employer_contributions,
    kz_contributions,
    rf_contributions,
    rf_tariff,
)
from tests.core.factories import d

BASE_LIMIT = d(2_979_000)
SME_THRESHOLD = d("1.5") * d(27_093)


def items(breakdown: object) -> dict[str, Decimal]:
    return {item.name: item.annual for item in breakdown.items}  # type: ignore[attr-defined]


def test_rf_standard_below_base() -> None:
    result = rf_contributions(d(100_000), RfTariff.STANDARD)
    assert items(result) == {
        "Взносы по единому тарифу": d(360_000),
        "Взносы на травматизм (НС и ПЗ)": d(2_400),
    }
    assert result.effective_rate == d("0.302")
    assert result.monthly_average == d(30_200)


def test_rf_standard_above_base() -> None:
    # 3 600 000 ₽ за год: 2 979 000 по 30%, остальное по 15,1%.
    result = rf_contributions(d(300_000), RfTariff.STANDARD)
    main = d("0.30") * BASE_LIMIT + d("0.151") * (d(3_600_000) - BASE_LIMIT)
    assert items(result)["Взносы по единому тарифу"] == main == d(987_471)
    assert result.annual_total == d(987_471) + d(7_200)
    assert result.effective_rate == d(994_671) / d(3_600_000)


def test_rf_sme_tariff() -> None:
    # 1,5 МРОТ = 40 639,5 ₽ по 30%, остальное по 15%.
    result = rf_contributions(d(100_000), RfTariff.SME)
    monthly = d("0.30") * SME_THRESHOLD + d("0.15") * (d(100_000) - SME_THRESHOLD)
    assert items(result)["Взносы по тарифу МСП"] == monthly * 12 == d("253151.1")
    assert result.nominal_rate == d("0.302")


def test_rf_sme_salary_below_threshold_pays_standard() -> None:
    assert (
        rf_contributions(d(30_000), RfTariff.SME).annual_total
        == rf_contributions(d(30_000), RfTariff.STANDARD).annual_total
    )


def test_rf_zero_ausn_and_npd() -> None:
    zero = rf_contributions(d(100_000), RfTariff.ZERO)
    assert items(zero) == {
        "Взносы по нулевому тарифу": d(0),
        "Взносы на травматизм (НС и ПЗ)": d(2_400),
    }
    ausn = rf_contributions(d(100_000), RfTariff.AUSN)
    assert ausn.annual_total == 0
    assert ausn.effective_rate == 0
    npd = rf_contributions(d(100_000), RfTariff.NONE)
    assert npd.items == ()
    assert npd.effective_rate == 0


@pytest.mark.parametrize(
    ("gross", "form", "expected"),
    [
        # ОПВ 30 000, ВОСМС 6 000; СО 5% × 270 000; ООСМС 3% × 300 000;
        # ОПВР 3,5% × 300 000; соцналог 6% × 264 000.
        (300_000, KzForm.TOO, {"СО": "13500", "ООСМС": "9000", "ОПВР": "10500", "СН": "15840"}),
        # СО — не более 7 МЗП (595 000), соцналог — 6% × (1 000 000 − 100 000 − 20 000).
        (
            1_000_000,
            KzForm.TOO,
            {"СО": "29750", "ООСМС": "30000", "ОПВР": "35000", "СН": "52800"},
        ),
        # Ниже МЗП: СО и ОПВР — от 1 МЗП, соцналог — от 14 МРП (60 550).
        (50_000, KzForm.TOO, {"СО": "4250", "ООСМС": "1500", "ОПВР": "2975", "СН": "3633"}),
        # ИП: соцналог — 1 МРП за работника.
        (300_000, KzForm.IP, {"СО": "13500", "ООСМС": "9000", "ОПВР": "10500", "СН": "4325"}),
    ],
)
def test_kz_contributions(gross: int, form: KzForm, expected: dict[str, str]) -> None:
    result = kz_contributions(d(gross), form)
    names = {
        "СО": "Социальные отчисления (СО)",
        "ООСМС": "Отчисления на ОСМС (ООСМС)",
        "ОПВР": "Пенсионные взносы работодателя (ОПВР)",
        "СН": "Социальный налог",
    }
    assert items(result) == {names[key]: d(value) * 12 for key, value in expected.items()}


def test_kz_effective_rate_without_caps_equals_nominal() -> None:
    result = kz_contributions(d(300_000), KzForm.TOO)
    assert result.effective_rate == result.nominal_rate == d("0.1628")
    assert kz_contributions(d(0), KzForm.IP).effective_rate == d("0.11")


def test_kz_caps_oosms_at_40_mzp() -> None:
    result = kz_contributions(d(5_000_000), KzForm.TOO)
    assert items(result)["Отчисления на ОСМС (ООСМС)"] == d("0.03") * 40 * d(85_000) * 12


@pytest.mark.parametrize(
    ("jurisdiction", "country", "expected"),
    [
        (None, None, "0.302"),
        (Jurisdiction(), None, "0.302"),
        (Jurisdiction(rf=UsnProfile(social_contributions=SocialContributions.ZERO)), None, "0.002"),
        (Jurisdiction(rf=UsnProfile(social_contributions=SocialContributions.SME)), None, "0.302"),
        (Jurisdiction(rf=OsnoProfile()), None, "0.302"),
        (Jurisdiction(rf=AusnProfile()), None, "0"),
        (Jurisdiction(rf=NpdProfile()), None, "0"),
        (Jurisdiction(mode=JurisdictionMode.KZ), None, "0.1628"),
        (Jurisdiction(mode=JurisdictionMode.KZ), Country.RF, "0.1628"),
        (
            Jurisdiction(mode=JurisdictionMode.KZ, kz=KzOurProfile(form=KzForm.IP)),
            None,
            "0.11",
        ),
        # Режим «Оба»: взносы — по стране трудоустройства сотрудника (L-06).
        (Jurisdiction(mode=JurisdictionMode.BOTH, rf=NpdProfile()), None, "0"),
        (Jurisdiction(mode=JurisdictionMode.BOTH, kz=KzOurProfile()), Country.RF, "0.302"),
        (Jurisdiction(mode=JurisdictionMode.BOTH, kz=KzOurProfile()), Country.KZ, "0.1628"),
    ],
)
def test_nominal_rate_without_salary(
    jurisdiction: Jurisdiction | None, country: Country | None, expected: str
) -> None:
    assert employer_contribution_rate(jurisdiction, country) == Decimal(expected)


def test_effective_rate_depends_on_salary() -> None:
    jurisdiction = Jurisdiction()
    low = employer_contribution_rate(jurisdiction, gross_salary=d(100_000))
    high = employer_contribution_rate(jurisdiction, gross_salary=d(1_000_000))
    assert low == d("0.302")
    assert high < low


def test_tariff_and_country_selection() -> None:
    assert rf_tariff(None) == RfTariff.STANDARD
    assert rf_tariff(Jurisdiction(rf=AusnProfile())) == RfTariff.AUSN
    sme = Jurisdiction(rf=OsnoProfile(social_contributions=SocialContributions.SME))
    assert rf_tariff(sme) == RfTariff.SME
    assert contribution_country(None, Country.KZ) == Country.RF
    assert contribution_country(Jurisdiction(mode=JurisdictionMode.BOTH), None) == Country.RF
    breakdown = employer_contributions(d(100_000), Jurisdiction(mode=JurisdictionMode.KZ))
    assert breakdown.country == Country.KZ


salaries = st.decimals(min_value=0, max_value=5_000_000, places=2, allow_nan=False)


@given(gross=salaries, extra=st.decimals(min_value=0, max_value=1_000_000, places=2))
def test_rf_contributions_grow_with_salary(gross: Decimal, extra: Decimal) -> None:
    for tariff in (RfTariff.STANDARD, RfTariff.SME):
        assert (
            rf_contributions(gross, tariff).annual_total
            <= rf_contributions(gross + extra, tariff).annual_total
        )


@given(gross=salaries)
def test_rf_sme_never_exceeds_standard(gross: Decimal) -> None:
    standard = rf_contributions(gross, RfTariff.STANDARD)
    sme = rf_contributions(gross, RfTariff.SME)
    assert sme.annual_total <= standard.annual_total
    assert d("0.153") <= standard.effective_rate <= d("0.302")


@given(gross=st.decimals(min_value=1, max_value=5_000_000, places=2))
def test_kz_rate_is_bounded(gross: Decimal) -> None:
    for form in KzForm:
        result = kz_contributions(gross, form)
        assert result.annual_total > 0
        # Выше номинала — только пока работает минимальная база СО: оклад за
        # вычетом ОПВ ниже МЗП, то есть оклад ниже МЗП / 0,9 ≈ 94 444 ₸. У ИП
        # сверх номинала ещё фиксированный соцналог 1 МРП (4 325 ₸) в месяц.
        fixed_part = d(4_325) / gross if form == KzForm.IP else d(0)
        if gross >= d(85_000) / d("0.9"):
            assert result.effective_rate <= result.nominal_rate + fixed_part + d("0.0001")
