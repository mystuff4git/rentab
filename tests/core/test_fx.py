"""Пересчёт валют по курсам фирмы (L-06) и загрузка курсов ЦБ РФ и НБРК."""

from __future__ import annotations

import datetime as dt
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator

import pytest

from rentab_core.errors import CurrencyConversionError, ExchangeRateFetchError, RentabError
from rentab_core.fx import (
    FETCH_TIMEOUT_SECONDS,
    MAX_RESPONSE_BYTES,
    TODAY_CACHE_TTL,
    RateSource,
    RatesQuote,
    clear_cache,
    convert,
    fetch_rates,
    http_get,
    parse_cbr,
    parse_nbrk,
    rub_per,
)
from rentab_core.models import ExchangeRates
from rentab_core.money import ONE, Currency
from tests.core.factories import d

RATES = ExchangeRates(rub_per_kzt=d("0.18"), rub_per_usd=d(90))


@pytest.mark.parametrize(
    ("amount", "source", "target", "expected"),
    [
        ("100", Currency.RUB, Currency.RUB, "100"),
        ("120000", Currency.KZT, Currency.RUB, "21600"),
        ("21600", Currency.RUB, Currency.KZT, "120000"),
        ("10", Currency.USD, Currency.RUB, "900"),
        ("10", Currency.USD, Currency.KZT, "5000"),
    ],
)
def test_convert(amount: str, source: Currency, target: Currency, expected: str) -> None:
    assert convert(d(amount), source, target, RATES) == d(expected)


def test_same_currency_needs_no_rate() -> None:
    assert convert(d(5), Currency.KZT, Currency.KZT, ExchangeRates()) == d(5)
    assert rub_per(ExchangeRates(), Currency.RUB) == 1


def test_missing_rate_is_an_explicit_error() -> None:
    # v0.3 складывал тенге с рублями как есть; ядро требует курс.
    with pytest.raises(CurrencyConversionError, match="Укажите курс ₽ за 1 ₸") as error:
        convert(d(1), Currency.KZT, Currency.RUB, ExchangeRates(rub_per_usd=d(90)))
    assert isinstance(error.value, RentabError)
    with pytest.raises(CurrencyConversionError, match=r"₽ за 1 \$"):
        convert(d(1), Currency.RUB, Currency.USD, RATES.model_copy(update={"rub_per_usd": None}))


# ---------------------------------------------------------------------------
# Загрузка курсов (docs/methodology.md, раздел 9.1)
# ---------------------------------------------------------------------------
# Ответы составлены по документированному формату сервисов: живые ЦБ РФ и
# НБРК из окружения разработки недоступны. ЦБ РФ отдаёт windows-1251 и
# десятичную запятую, НБРК — UTF-8 и точку.
CBR_XML = (
    '<?xml version="1.0" encoding="windows-1251"?>'
    '<ValCurs Date="22.10.2026" name="Foreign Currency Market">'
    '<Valute ID="R01235"><NumCode>840</NumCode><CharCode>USD</CharCode>'
    "<Nominal>1</Nominal><Name>Доллар США</Name><Value>81,2345</Value>"
    "<VunitRate>81,2345</VunitRate></Valute>"
    '<Valute ID="R01335"><NumCode>398</NumCode><CharCode>KZT</CharCode>'
    "<Nominal>100</Nominal><Name>Казахстанских тенге</Name><Value>15,4321</Value>"
    "<VunitRate>0,154321</VunitRate></Valute>"
    "</ValCurs>"
).encode("cp1251")
NBRK_XML = (
    '<?xml version="1.0" encoding="utf-8"?><rates><title>Official exchange rates</title>'
    "<date>21.10.2026</date>"
    "<item><fullname>РОССИЙСКИЙ РУБЛЬ</fullname><title>RUB</title>"
    "<description>6.48</description><quant>1</quant><index>DOWN</index>"
    "<change>-0.02</change></item>"
    "<item><fullname>ДОЛЛАР США</fullname><title>USD</title>"
    "<description>525.10</description><quant>1</quant><index>UP</index>"
    "<change>1.10</change></item>"
    "</rates>"
).encode()
DAY = dt.date(2026, 10, 22)
NOON = dt.datetime(2026, 10, 22, 12, 0)


class FakeFetcher:
    """Подменяет HTTP: отвечает по префиксу URL, считает вызовы."""

    def __init__(self, responses: dict[str, bytes | Exception]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.calls.append(url)
        for prefix, response in self.responses.items():
            if url.startswith(prefix):
                if isinstance(response, Exception):
                    raise response
                return response
        raise ExchangeRateFetchError(f"нет ответа для {url}")


CBR = "https://www.cbr.ru/"
NBRK = "https://nationalbank.kz/"


@pytest.fixture(autouse=True)
def _empty_cache() -> Iterator[None]:
    clear_cache()
    yield
    clear_cache()


def test_parse_cbr() -> None:
    quote = parse_cbr(CBR_XML, DAY)
    assert quote.rates == ExchangeRates(rub_per_kzt=d("0.154321"), rub_per_usd=d("81.2345"))
    assert quote.source == RateSource.CBR
    assert quote.rate_date == DAY


def test_parse_nbrk() -> None:
    quote = parse_nbrk(NBRK_XML, DAY)
    assert quote.rates.rub_per_kzt == ONE / d("6.48")
    assert quote.rates.rub_per_usd == d("525.10") / d("6.48")
    assert quote.rate_date == dt.date(2026, 10, 21)


@pytest.mark.parametrize(
    ("content", "parser", "message"),
    [
        (b"<ValCurs", parse_cbr, "не разбирается как XML"),
        (b'<ValCurs Date="22.10.2026"></ValCurs>', parse_cbr, "нет курсов KZT, USD"),
        (CBR_XML.replace(b"81,2345", b"abc"), parse_cbr, "не число"),
        (CBR_XML.replace(b"<Nominal>1<", b"<Nominal>0<"), parse_cbr, "Nominal USD"),
        (CBR_XML.replace(b"22.10.2026", b"2026-10-22"), parse_cbr, "дата курсов"),
        (
            NBRK_XML.replace(b"<title>RUB</title>", b"<title>EUR</title>"),
            parse_nbrk,
            "нет курсов RUB",
        ),
    ],
)
def test_parse_errors(
    content: bytes, parser: Callable[[bytes, dt.date], RatesQuote], message: str
) -> None:
    with pytest.raises(ExchangeRateFetchError, match=message):
        parser(content, DAY)


def test_fetch_rates_prefers_cbr_and_caches() -> None:
    fetcher = FakeFetcher({CBR: CBR_XML, NBRK: NBRK_XML})
    quote = fetch_rates(DAY, fetch=fetcher, clock=lambda: NOON)
    assert quote.source == RateSource.CBR
    assert fetcher.calls == ["https://www.cbr.ru/scripts/XML_daily.asp?date_req=22/10/2026"]
    again = fetch_rates(DAY, fetch=fetcher, clock=lambda: NOON + dt.timedelta(hours=1))
    assert again == quote
    assert len(fetcher.calls) == 1


def test_today_rates_expire_past_rates_do_not() -> None:
    fetcher = FakeFetcher({CBR: CBR_XML})
    fetch_rates(DAY, fetch=fetcher, clock=lambda: NOON)
    fetch_rates(DAY, fetch=fetcher, clock=lambda: NOON + TODAY_CACHE_TTL)
    assert len(fetcher.calls) == 2
    yesterday = DAY - dt.timedelta(days=1)
    fetch_rates(yesterday, fetch=fetcher, clock=lambda: NOON)
    fetch_rates(yesterday, fetch=fetcher, clock=lambda: NOON + 10 * TODAY_CACHE_TTL)
    assert len(fetcher.calls) == 3


def test_fetch_rates_defaults_to_today() -> None:
    fetcher = FakeFetcher({CBR: CBR_XML})
    assert fetch_rates(fetch=fetcher, clock=lambda: NOON).requested == DAY


def test_fetch_rates_falls_back_to_nbrk() -> None:
    fetcher = FakeFetcher({CBR: ExchangeRateFetchError("ЦБ недоступен"), NBRK: NBRK_XML})
    quote = fetch_rates(DAY, fetch=fetcher, clock=lambda: NOON)
    assert quote.source == RateSource.NBRK
    assert fetcher.calls[1] == "https://nationalbank.kz/rss/get_rates.cfm?fdate=22.10.2026"


def test_fetch_rates_fails_with_both_errors() -> None:
    fetcher = FakeFetcher({CBR: b"<html>", NBRK: ExchangeRateFetchError("НБРК недоступен")})
    with pytest.raises(ExchangeRateFetchError, match="введите их вручную") as error:
        fetch_rates(DAY, fetch=fetcher, clock=lambda: NOON)
    assert "ЦБ РФ: ответ не разбирается как XML" in str(error.value)
    assert str(error.value).endswith("НБРК недоступен.")
    assert isinstance(error.value, RentabError)


class FakeResponse:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, size: int) -> bytes:
        return self.content[:size]


def test_http_get(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[urllib.request.Request] = []

    def urlopen(request: urllib.request.Request, timeout: float) -> FakeResponse:
        seen.append(request)
        assert timeout == FETCH_TIMEOUT_SECONDS
        return FakeResponse(CBR_XML)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    assert http_get("https://www.cbr.ru/x") == CBR_XML
    assert seen[0].get_header("User-agent", "").startswith("Rentab")


def test_http_get_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def offline(request: urllib.request.Request, timeout: float) -> FakeResponse:
        raise urllib.error.URLError("нет сети")

    monkeypatch.setattr(urllib.request, "urlopen", offline)
    with pytest.raises(ExchangeRateFetchError, match=r"сервис недоступен \(нет сети\)"):
        http_get("https://www.cbr.ru/x")

    def timeout(request: urllib.request.Request, timeout: float) -> FakeResponse:
        raise TimeoutError("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", timeout)
    with pytest.raises(ExchangeRateFetchError, match="timed out"):
        http_get("https://www.cbr.ru/x")

    huge = b"x" * (MAX_RESPONSE_BYTES + 10)
    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout: FakeResponse(huge))
    with pytest.raises(ExchangeRateFetchError, match="слишком большой"):
        http_get("https://www.cbr.ru/x")
