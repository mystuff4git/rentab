"""Файл профиля: выгрузка, загрузка, ошибки, пошлины и схема (`rentab_core.io`)."""

from __future__ import annotations

import base64
import datetime as dt
import json
import runpy
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from rentab_core import __version__
from rentab_core.demo import TM_APPLICATION, TM_EXAMINATION, demo_workspace
from rentab_core.documents import Letterhead, OfferTerms, read_logo
from rentab_core.duties import ApplicantCategory, Quantity, load_catalog
from rentab_core.errors import ProfileImportError
from rentab_core.estimate import calculate_estimate
from rentab_core.fx import RateSource, RatesQuote
from rentab_core.io import (
    FORMAT_ID,
    MAX_PROFILE_BYTES,
    SCHEMA_VERSION,
    DutySelection,
    NoteLevel,
    ProfileFile,
    ProjectRecord,
    Workspace,
    dumps,
    loads,
)
from rentab_core.io.files import _error_message
from rentab_core.io.migrations import MIGRATIONS, RawProfile, info, upgrade
from rentab_core.io.schema import json_schema_text, main
from rentab_core.models import (
    ActualHours,
    Assignment,
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
    LegalForm,
    MonthlyCost,
    Overhead,
    Period,
    PricingModel,
    Project,
    ProjectActuals,
    Role,
    Salary,
    SocialContributions,
    Stage,
    UsnObject,
    UsnProfile,
    UsnVat,
    VatMode,
)
from rentab_core.money import Currency, round_to
from tests.core.factories import actuals, d, image_bytes, stage

SCHEMA_PATH = Path(__file__).parents[2] / "docs" / "profile.schema.json"
EXPORTED_AT = dt.datetime(2026, 10, 22, 15, 0, tzinfo=dt.UTC)


def raw_file(workspace: Workspace | None = None) -> RawProfile:
    """Словарь JSON файла профиля."""
    result: RawProfile = json.loads(dumps(workspace or demo_workspace()))
    return result


def encode(raw: Any) -> bytes:
    return json.dumps(raw, ensure_ascii=False).encode("utf-8")


# ---------------------------------------------------------------------------
# Выгрузка и загрузка без потерь
# ---------------------------------------------------------------------------
def test_demo_round_trip() -> None:
    workspace = demo_workspace()
    result = loads(dumps(workspace, exported_at=EXPORTED_AT))
    assert result.workspace == workspace
    assert result.notes == ()
    assert result.source_version == SCHEMA_VERSION
    assert not result.from_v03


def test_file_envelope_and_number_format() -> None:
    data = dumps(demo_workspace(), exported_at=EXPORTED_AT)
    text = data.decode("utf-8")
    raw = json.loads(text)
    assert raw["format"] == FORMAT_ID
    assert raw["schema_version"] == 1
    assert raw["exported_at"] == "2026-10-22T15:00:00Z"
    assert list(raw)[:4] == ["format", "schema_version", "exported_at", "app_version"]
    # Суммы — строками, кириллица — без экранирования.
    partner = raw["firm"]["team"][0]
    assert partner["compensation"] == {"kind": "monthly", "amount": "400000"}
    assert partner["target_utilization"] == "0.625"
    assert "Демо-фирма «Слайд 3»" in text
    assert text.endswith("}\n")


def test_export_without_projects() -> None:
    raw = json.loads(dumps(demo_workspace(), include_projects=False))
    assert raw["projects"] == []
    assert loads(dumps(demo_workspace(), include_projects=False)).workspace.projects == ()


def test_rates_quote_round_trip() -> None:
    quote = RatesQuote(
        rates=ExchangeRates(rub_per_kzt=d("0.1543"), rub_per_usd=d("81.2345")),
        source=RateSource.NBRK,
        rate_date=dt.date(2026, 10, 21),
        requested=dt.date(2026, 10, 22),
    )
    # Курс тенге изменён вручную после загрузки: в файле и то, и другое.
    firm = Firm(exchange_rates=ExchangeRates(rub_per_kzt=d("0.16"), rub_per_usd=d("81.2345")))
    workspace = Workspace(firm=firm, rates_quote=quote)
    raw = raw_file(workspace)
    assert raw["rates_quote"]["source"] == "nbrk"
    assert raw["rates_quote"]["rate_date"] == "2026-10-21"
    assert loads(dumps(workspace)).workspace == workspace


def test_numbers_in_json_are_read_as_decimal() -> None:
    raw = raw_file()
    raw["firm"]["team"][0]["target_utilization"] = 0.1
    raw["firm"]["overheads"][0]["amount"] = 18000.10
    firm = loads(encode(raw)).workspace.firm
    assert firm.team[0].target_utilization == Decimal("0.1")
    assert firm.overheads[0].amount == Decimal("18000.1")


def full_featured_workspace() -> Workspace:
    """Всё, что появилось в фазах 2–3: оклад и сумма, «из прибыли», МСП, РК, пошлины."""
    team = (
        Employee(
            name="Партнёр",
            role=Role.PARTNER,
            compensation=MonthlyCost(amount=d(500_000)),
            billing_rate=d(20_000),
            target_utilization=d("0.5"),
            paid_from_profit=True,
        ),
        Employee(
            name="Юрист",
            role=Role.SENIOR,
            compensation=Salary(gross_salary=d(200_000), employer_contribution_rate=d("0.2163")),
            billing_rate=d(8_000),
            contract_hours_per_month=d(160),
        ),
        Employee(
            name="Юрист РК",
            role=Role.ASSOCIATE,
            currency=Currency.KZT,
            compensation=Salary(gross_salary=d(800_000), employer_contribution_rate=d("0.1628")),
            billing_rate=d(30_000),
        ),
    )
    firm = Firm(
        name="ТОО «Право»",
        jurisdiction=Jurisdiction(
            mode=JurisdictionMode.BOTH,
            rf=UsnProfile(
                tax_object=UsnObject.INCOME_MINUS_EXPENSES,
                vat=UsnVat.VAT_7,
                social_contributions=SocialContributions.SME,
            ),
            kz=KzOurProfile(form=KzForm.IP, vat=KzVat.VAT_16),
        ),
        team=team,
        overheads=(
            Overhead(name="Аренда", amount=d(100_000), period=Period.MONTHLY),
            Overhead(name="Сервер", amount=d(1_200), currency=Currency.USD, period=Period.ANNUAL),
        ),
        exchange_rates=ExchangeRates(rub_per_kzt=d("0.1543"), rub_per_usd=d("81.2345")),
        facts=FirmFacts(
            rf_legal_form=LegalForm.ENTREPRENEUR,
            annual_income_rf=d(50_000_000),
            annual_income_kz=d(30_000_000),
            headcount=7,
            agency_contracts=True,
        ),
    )
    project = Project(
        name="Товарный знак в РК",
        client="ТОО «Клиент»",
        description="Многострочное\nописание",
        pricing=CappedPricing(cap=d(1_000_000)),
        target_margin=d("0.35"),
        vat_mode=VatMode.INCLUDED,
        calc_country=Country.KZ,
        stages=(
            Stage(
                name="Подача",
                assignments=(
                    Assignment(employee="Юрист РК", hours=d("12.5")),
                    Assignment(employee="Партнёр", hours=d(1)),
                ),
            ),
        ),
        disbursements=(
            Disbursement(
                name="Перевод", kind=DisbursementKind.OWN, amount=d(15_000), stage="Подача"
            ),
            Disbursement(
                name="Курьер", kind=DisbursementKind.BILLABLE, amount=d(50), currency=Currency.USD
            ),
        ),
    )
    kz_action = load_catalog(Country.KZ).duties[0].action
    record = ProjectRecord(
        project=project,
        duties={
            Country.RF: DutySelection(actions=(TM_APPLICATION,), quantities={Quantity.CLASSES: 2}),
            Country.KZ: DutySelection(actions=(kz_action,), category=ApplicantCategory.SME),
        },
    )
    return Workspace(firm=firm, projects=(record, ProjectRecord()))


def test_full_featured_round_trip() -> None:
    workspace = full_featured_workspace()
    result = loads(dumps(workspace))
    assert result.workspace == workspace
    assert result.notes == ()


# Случайные проекты: модель цены, режим НДС, этапы и расходы.
money = st.integers(min_value=0, max_value=10**9).map(lambda cents: Decimal(cents) / 100)
positive_money = st.integers(min_value=1, max_value=10**9).map(lambda cents: Decimal(cents) / 100)
names = st.text(min_size=1, max_size=20)
pricing_models: st.SearchStrategy[PricingModel] = st.one_of(
    st.just(HourlyPricing()),
    st.builds(CappedPricing, cap=positive_money),
    st.builds(FixedPricing, price=st.none() | positive_money),
)


@st.composite
def projects(draw: st.DrawFn) -> ProjectRecord:
    stage_names = draw(st.lists(names, max_size=3, unique=True))
    stages = tuple(
        Stage(
            name=name,
            assignments=tuple(
                draw(
                    st.lists(
                        st.builds(
                            Assignment,
                            employee=st.sampled_from(["Партнёр", "Юрист", "Стажёр"]),
                            hours=money,
                        ),
                        max_size=3,
                    )
                )
            ),
        )
        for name in stage_names
    )
    expenses = draw(
        st.lists(
            st.builds(
                Disbursement,
                name=names,
                kind=st.sampled_from(DisbursementKind),
                amount=money,
                currency=st.sampled_from(Currency),
                stage=st.sampled_from([None, *stage_names]),
            ),
            max_size=3,
        )
    )
    actions = [duty.action for duty in load_catalog(Country.RF).duties]
    fact_rows = draw(
        st.lists(
            st.tuples(st.sampled_from(stage_names), st.sampled_from(["Партнёр", "Юрист"])),
            unique=True,
            max_size=4,
        )
        if stage_names
        else st.just([])
    )
    project_actuals = draw(
        st.none()
        | st.builds(
            ProjectActuals,
            hours=st.just(
                tuple(
                    ActualHours(stage=stage_name, employee=who, hours=Decimal(len(who)))
                    for stage_name, who in fact_rows
                )
            ),
            billed=st.none() | positive_money,
        )
    )
    return ProjectRecord(
        project=Project(
            name=draw(st.text(max_size=30)),
            client=draw(st.text(max_size=30)),
            pricing=draw(pricing_models),
            target_margin=draw(st.integers(0, 99).map(lambda pct: Decimal(pct) / 100)),
            vat_mode=draw(st.sampled_from(VatMode)),
            calc_country=draw(st.none() | st.sampled_from(Country)),
            stages=stages,
            disbursements=tuple(expenses),
            actuals=project_actuals,
        ),
        duties={
            Country.RF: DutySelection(
                actions=tuple(draw(st.lists(st.sampled_from(actions), unique=True, max_size=3))),
                quantities=draw(
                    st.dictionaries(st.sampled_from(Quantity), st.integers(0, 50), max_size=3)
                ),
            )
        },
    )


@settings(max_examples=60, deadline=None)
@given(records=st.lists(projects(), max_size=3))
def test_random_projects_round_trip(records: list[ProjectRecord]) -> None:
    workspace = demo_workspace().model_copy(update={"projects": tuple(records)})
    assert loads(dumps(workspace)).workspace == workspace


# ---------------------------------------------------------------------------
# Пошлины: выбор, а не суммы
# ---------------------------------------------------------------------------
def test_duty_amounts_are_written_for_reference() -> None:
    raw = raw_file()
    trademark = raw["projects"][1]
    assert trademark["duties"]["RF"] == {
        "actions": [TM_APPLICATION, TM_EXAMINATION],
        "quantities": {"classes": 3},
        "category": "legal_entity",
        "amounts_at_export": {TM_APPLICATION: "6000", TM_EXAMINATION: "18000"},
    }
    # Расходы, введённые вручную, — отдельно от пошлин.
    assert [item["name"] for item in trademark["project"]["disbursements"]] == [
        "Нотариальный перевод доверенности"
    ]


def test_duties_are_recalculated_by_current_catalog() -> None:
    raw = raw_file()
    raw["projects"][1]["duties"]["RF"]["amounts_at_export"][TM_APPLICATION] = "5000"
    result = loads(encode(raw))
    (note,) = result.notes
    assert note.level == NoteLevel.WARNING
    assert note.message == (
        f"Проект «Регистрация товарного знака»: пошлина «{TM_APPLICATION}» при выгрузке была "
        "5 000 ₽, по текущему каталогу — 6 000 ₽. В смете будет новая сумма."
    )
    record = result.workspace.projects[1]
    assert record.duties[Country.RF].amounts_at_export == {}
    duties = record.to_project().disbursements[:2]
    assert [item.amount for item in duties] == [d(6_000), d(18_000)]


def test_unknown_duty_is_skipped_and_old_name_is_renamed() -> None:
    catalog = load_catalog(Country.RF)
    (aliased,) = [duty for duty in catalog.duties if duty.aliases][:1]
    raw = raw_file()
    raw["projects"][3]["duties"] = {
        "RF": {"actions": ["Несуществующая пошлина", aliased.aliases[0], aliased.action]}
    }
    result = loads(encode(raw))
    assert result.workspace.projects[3].duties[Country.RF].actions == (aliased.action,)
    (note,) = result.notes
    assert note.message == (
        "Проект «Ответ на уведомление экспертизы»: пошлины «Несуществующая пошлина» нет в "
        "каталоге — она пропущена."
    )


def test_unknown_duty_has_no_reference_amount() -> None:
    record = ProjectRecord(
        duties={Country.RF: DutySelection(actions=("Нет такой", TM_APPLICATION))}
    )
    raw = raw_file(Workspace(projects=(record,)))
    assert raw["projects"][0]["duties"]["RF"]["amounts_at_export"] == {TM_APPLICATION: "4000"}


def test_to_project_puts_duties_of_firm_countries_first() -> None:
    workspace = full_featured_workspace()
    record = workspace.projects[0]
    project = record.scenario(workspace.firm).project
    assert [item.name for item in project.disbursements] == [
        TM_APPLICATION,
        record.duties[Country.KZ].actions[0],
        "Перевод",
        "Курьер",
    ]
    rf_only = record.to_project([Country.RF])
    assert [item.name for item in rf_only.disbursements] == [TM_APPLICATION, "Перевод", "Курьер"]


def test_unknown_employee_in_assignment_is_reported() -> None:
    raw = raw_file()
    raw["projects"][0]["project"]["stages"][0]["assignments"][0]["employee"] = "Уволенный"
    (note,) = loads(encode(raw)).notes
    assert note.message == (
        "Проект «Слайд 3: план и факт», этап «Проект»: исполнителя «Уволенный» нет в команде — "
        "назначение не войдёт в расчёт."
    )


def test_untitled_project_is_numbered_in_notes() -> None:
    raw = raw_file()
    raw["projects"][0]["project"]["name"] = " "
    raw["projects"][0]["project"]["stages"][0]["assignments"][0]["employee"] = "Нет"
    (note,) = loads(encode(raw)).notes
    assert note.message.startswith("Проект № 1, этап «Проект»")


# ---------------------------------------------------------------------------
# Ошибки
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"\xff\xfe\x00", "Файл не в кодировке UTF-8"),
        (b"{not json", "Файл не является JSON: Expecting property name"),
        (b"[1, 2]", "ожидался объект JSON"),
        (b'{"name": "x"}', "нет поля format"),
        (b'{"format": "other"}', "format = 'other'"),
        (b'{"format": "rentab"}', "Неверная версия схемы файла: None"),
        (b'{"format": "rentab", "schema_version": true}', "Неверная версия схемы файла: True"),
        (b'{"format": "rentab", "schema_version": 0}', "Неверная версия схемы файла: 0"),
        (
            b'{"format": "rentab", "schema_version": 2}',
            "Файл создан более новой версией Rentab (схема 2, поддерживается до 1)",
        ),
        (b'{"format": "rentab", "schema_version": 1, "x": NaN}', "значение NaN"),
        (b"[" * 100_000 + b"]" * 100_000, "слишком глубокая вложенность"),
    ],
)
def test_bad_files_are_rejected(data: bytes, message: str) -> None:
    with pytest.raises(ProfileImportError) as error:
        loads(data)
    assert message in str(error.value)


def test_too_large_file_is_rejected() -> None:
    with pytest.raises(ProfileImportError, match="Файл слишком большой"):
        loads(b" " * (MAX_PROFILE_BYTES + 1))


def test_utf8_bom_is_accepted() -> None:
    workspace = demo_workspace()
    assert loads(b"\xef\xbb\xbf" + dumps(workspace)).workspace == workspace


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            lambda raw: raw["projects"][0]["project"]["stages"][0]["assignments"][0].update(
                hours="-1"
            ),
            "projects[0].project.stages[0].assignments[0].hours: должно быть не меньше 0",
        ),
        (lambda raw: raw["firm"].update(unknown=1), "firm.unknown: неизвестное поле"),
        (
            lambda raw: raw["firm"]["team"][0].pop("role"),
            "firm.team[0].role: обязательное поле не заполнено",
        ),
        (
            lambda raw: raw["firm"]["team"][0].update(role="boss"),
            "firm.team[0].role: допустимые значения: 'partner', 'senior', 'associate' or 'intern'",
        ),
        (
            lambda raw: raw["firm"]["team"][0]["compensation"].update(kind="bonus"),
            "firm.team[0].compensation: неизвестный вид «bonus», допустимые: 'salary', 'monthly'",
        ),
        (
            lambda raw: raw["firm"]["team"][0].update(target_utilization="много"),
            "firm.team[0].target_utilization: должно быть числом",
        ),
        (
            lambda raw: raw["projects"][0]["project"]["stages"].append(
                {"name": "Проект", "assignments": []}
            ),
            "projects[0].project: Названия этапов должны быть уникальны, повторяются: Проект",
        ),
        (lambda raw: raw.update(projects={}), "projects: должно быть списком"),
    ],
)
def test_invalid_values_point_to_field(change: Any, message: str) -> None:
    raw = raw_file()
    change(raw)
    with pytest.raises(ProfileImportError) as error:
        loads(encode(raw))
    assert str(error.value).startswith("Неверные данные в файле: ")
    assert message in str(error.value)


def test_many_errors_are_truncated() -> None:
    raw = raw_file()
    for member in raw["firm"]["team"]:
        member["billing_rate"] = "-1"
    for overhead in raw["firm"]["overheads"]:
        overhead["amount"] = "-1"
    with pytest.raises(ProfileImportError, match=r"и ещё 1$"):
        loads(encode(raw))


# ---------------------------------------------------------------------------
# Миграции и схема
# ---------------------------------------------------------------------------
def test_every_old_version_has_migration_step() -> None:
    assert all(version in MIGRATIONS for version in range(1, SCHEMA_VERSION))
    assert ProfileFile().schema_version == SCHEMA_VERSION


def test_upgrade_runs_steps_in_order() -> None:
    def to_v2(raw: RawProfile) -> tuple[RawProfile, list[Any]]:
        rest = {key: value for key, value in raw.items() if key != "old"}
        return {**rest, "renamed": raw["old"]}, [info("1 → 2")]

    def to_v3(raw: RawProfile) -> tuple[RawProfile, list[Any]]:
        return {**raw, "added": True}, [info("2 → 3")]

    raw, notes = upgrade({"old": 1}, 1, steps={1: to_v2, 2: to_v3}, target=3)
    assert raw == {"renamed": 1, "added": True, "schema_version": 3}
    assert [note.message for note in notes] == ["1 → 2", "2 → 3"]
    with pytest.raises(ProfileImportError, match="с версии схемы 2 на 3"):
        upgrade({"old": 1}, 1, steps={1: to_v2}, target=3)


def test_json_schema_is_up_to_date(request: pytest.FixtureRequest) -> None:
    text = json_schema_text()
    if request.config.getoption("--update-snapshots"):
        SCHEMA_PATH.write_text(text, encoding="utf-8")
        return
    assert SCHEMA_PATH.read_text(encoding="utf-8") == text, (
        "Модели файла профиля изменились. Если старые файлы больше не читаются — поднимите "
        "SCHEMA_VERSION и добавьте шаг в rentab_core.io.migrations; затем "
        "uv run python -m rentab_core.io docs/profile.schema.json"
    )


def test_error_message_falls_back_to_pydantic_text() -> None:
    error: Any = {"type": "enum", "loc": ("x",), "msg": "Input should be 'a'", "input": "b"}
    assert _error_message(error) == "Input should be 'a'"


def test_schema_module_runs_as_script(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["rentab_core.io"])
    runpy.run_module("rentab_core.io", run_name="__main__")
    assert capsys.readouterr().out == json_schema_text()


def test_schema_main_writes_file_or_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "schema.json"
    main([str(target)])
    assert target.read_text(encoding="utf-8") == json_schema_text()
    main([])
    assert capsys.readouterr().out == json_schema_text()


# ---------------------------------------------------------------------------
# Совместимость: файл, выгруженный приложением фазы 4
# ---------------------------------------------------------------------------
# Выгрузка демо-фирмы кодом фазы 4 (main@64ab11e), до появления факта (фаза 5).
PHASE4_FIXTURE = Path(__file__).parents[1] / "fixtures" / "profile" / "phase4_demo.json"
# NNE смет демо-фирмы фазы 4: слайд 3 (план, факт), товарный знак, изобретение, потолок.
PHASE4_NNE = (d(225_500), d(-2_200), d(58_800), d(49_125), d(32_200))
# Версия Rentab, которая выгрузила файл фазы 4; повторная выгрузка пишет текущую.
PHASE4_APP_VERSION = "1.0.0.dev0"


def test_phase4_file_loads_unchanged() -> None:
    data = PHASE4_FIXTURE.read_bytes()
    result = loads(data)
    assert result.notes == ()
    assert result.source_version == 1
    workspace = result.workspace
    assert [record.project.name for record in workspace.projects] == [
        "Слайд 3: план",
        "Слайд 3: факт — партнёр тянет проект",
        "Регистрация товарного знака",
        "Патентование изобретения",
        "Ответ на уведомление экспертизы",
    ]
    nne = tuple(
        calculate_estimate(record.scenario(workspace.firm)).nne for record in workspace.projects
    )
    assert tuple(round_to(value) for value in nne) == PHASE4_NNE
    # Повторная выгрузка совпадает с файлом фазы 4: новые поля без значения не пишутся.
    expected = data.replace(
        f'"app_version": "{PHASE4_APP_VERSION}"'.encode(),
        f'"app_version": "{__version__}"'.encode(),
    )
    assert dumps(workspace, exported_at=EXPORTED_AT) == expected


def test_actuals_round_trip_and_are_omitted_when_empty() -> None:
    project = Project(
        name="С фактом",
        stages=(stage("Проект", ("Партнёр", 5)),),
        actuals=actuals(("Проект", "Партнёр", 62), ("Проект", "Стажёр", 1), billed=280_000),
    )
    workspace = demo_workspace().model_copy(
        update={"projects": (ProjectRecord(project=project), ProjectRecord())}
    )
    raw = raw_file(workspace)
    assert raw["projects"][0]["project"]["actuals"] == {
        "hours": [
            {"stage": "Проект", "employee": "Партнёр", "hours": "62"},
            {"stage": "Проект", "employee": "Стажёр", "hours": "1"},
        ],
        "billed": "280000",
    }
    # Проект без факта пишется без поля: такой файл читает и приложение фазы 4.
    assert "actuals" not in raw["projects"][1]["project"]
    assert loads(dumps(workspace)).workspace == workspace


def test_actuals_on_unknown_stage_point_to_project() -> None:
    raw = raw_file()
    raw["projects"][0]["project"]["actuals"] = {
        "hours": [{"stage": "Нет такого", "employee": "Партнёр", "hours": "1"}]
    }
    with pytest.raises(ProfileImportError, match=r"projects\[0\]\.project: Факт «Партнёр»"):
        loads(encode(raw))


# ---------------------------------------------------------------------------
# Фаза 7: бланк фирмы, условия КП и описания этапов — без новой версии схемы
# ---------------------------------------------------------------------------
LETTERHEAD = Letterhead(
    requisites="ИНН 0000000000 · КПП 000000000",
    address="Москва, ул. Примерная, д. 1",
    email="info@example.com",
    signatory_name="Иванов И. И.",
    signatory_position="управляющий партнёр",
    bank_details="р/с 00000000000000000000\nБИК 000000000",
    logo=read_logo(image_bytes()),
)
OFFER = OfferTerms(number="КП-7", validity_days=14, payment_terms="50% — аванс")


def test_letterhead_offer_and_stage_description_round_trip() -> None:
    base = demo_workspace()
    record = base.projects[1]
    described = record.project.model_copy(
        update={
            "stages": tuple(
                stage_.model_copy(update={"description": f"Результат: {stage_.name}"})
                for stage_ in record.project.stages
            )
        }
    )
    workspace = base.model_copy(
        update={
            "letterhead": LETTERHEAD,
            "projects": (record.model_copy(update={"project": described, "offer": OFFER}),),
        }
    )
    raw = raw_file(workspace)
    assert raw["letterhead"]["logo"]["media_type"] == "image/png"
    assert isinstance(raw["letterhead"]["logo"]["data"], str)  # base64
    assert raw["projects"][0]["offer"]["validity_days"] == 14
    assert raw["projects"][0]["project"]["stages"][0]["description"].startswith("Результат")
    result = loads(dumps(workspace))
    assert result.workspace == workspace
    assert result.notes == ()


def test_empty_letterhead_offer_and_descriptions_are_not_written() -> None:
    # Файл без КП пишется без новых полей: его читает и приложение фаз 4–6.
    demo = demo_workspace()
    projects = tuple(
        record.model_copy(
            update={
                "offer": None,
                "project": record.project.model_copy(
                    update={
                        "stages": tuple(
                            item.model_copy(update={"description": ""})
                            for item in record.project.stages
                        )
                    }
                ),
            }
        )
        for record in demo.projects
    )
    raw = raw_file(demo.model_copy(update={"letterhead": None, "projects": projects}))
    assert "letterhead" not in raw
    for record in raw["projects"]:
        assert "offer" not in record
        assert all("description" not in item for item in record["project"]["stages"])


def test_broken_logo_is_dropped_with_warning() -> None:
    raw = raw_file(demo_workspace().model_copy(update={"letterhead": LETTERHEAD}))
    # Сигнатура PNG верна, но картинка повреждена: модель её пропустит, Pillow — нет.
    broken = b"\x89PNG\r\n\x1a\n" + b"\x00" * 30
    raw["letterhead"]["logo"]["data"] = base64.b64encode(broken).decode("ascii")
    result = loads(encode(raw))
    assert result.workspace.letterhead is not None
    assert result.workspace.letterhead.logo is None
    assert result.workspace.letterhead.requisites == LETTERHEAD.requisites
    assert [note.level for note in result.notes] == [NoteLevel.WARNING]
    assert "Логотип из файла не загружен" in result.notes[0].message


def test_logo_of_wrong_type_points_to_field() -> None:
    raw = raw_file(demo_workspace().model_copy(update={"letterhead": LETTERHEAD}))
    raw["letterhead"]["logo"]["media_type"] = "image/jpeg"
    with pytest.raises(ProfileImportError, match=r"letterhead\.logo: логотип не похож"):
        loads(encode(raw))
