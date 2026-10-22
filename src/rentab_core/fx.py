"""Курсы валют: пересчёт сумм (L-06) и загрузка курсов ЦБ РФ и НБРК.

Курс задаётся как цена единицы валюты в рублях (`ExchangeRates`), поэтому
пересчёт идёт через рубль:

    amount_target = amount_source × rub_per(source) / rub_per(target)

Если нужного курса нет, ядро не складывает суммы как есть, а бросает
`CurrencyConversionError`.

Курсы можно загрузить на дату (`fetch_rates`, docs/methodology.md,
раздел 9.1): сначала ЦБ РФ, при ошибке — НБРК. Загруженные курсы хранятся в
памяти процесса, на диск ничего не пишется. Если не ответил ни один
источник — `ExchangeRateFetchError`, и курсы вводятся вручную.
"""

from __future__ import annotations

import datetime as dt
import threading
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from rentab_core.errors import CurrencyConversionError, ExchangeRateFetchError
from rentab_core.models import CoreModel, ExchangeRates
from rentab_core.money import CURRENCY_SYMBOLS, ONE, Currency

# Поле `ExchangeRates` с курсом каждой валюты, кроме рубля.
RATE_FIELDS: Mapping[Currency, str] = {
    Currency.KZT: "rub_per_kzt",
    Currency.USD: "rub_per_usd",
}


def rub_per(rates: ExchangeRates, currency: Currency) -> Decimal:
    """Цена единицы валюты в рублях.

    Raises:
        CurrencyConversionError: Курс валюты не задан.
    """
    if currency == Currency.RUB:
        return ONE
    value: Decimal | None = getattr(rates, RATE_FIELDS[currency])
    if value is None:
        raise CurrencyConversionError(
            f"Укажите курс ₽ за 1 {CURRENCY_SYMBOLS[currency]}: "
            f"без него суммы в {currency.value} нельзя пересчитать"
        )
    return value


def convert(amount: Decimal, source: Currency, target: Currency, rates: ExchangeRates) -> Decimal:
    """Пересчитывает сумму из валюты source в валюту target.

    Raises:
        CurrencyConversionError: Не задан курс одной из валют (если валюты различаются).
    """
    if source == target:
        return amount
    return amount * rub_per(rates, source) / rub_per(rates, target)


# ---------------------------------------------------------------------------
# Загрузка курсов ЦБ РФ и НБРК
# ---------------------------------------------------------------------------
class RateSource(StrEnum):
    """Откуда взяты курсы."""

    CBR = "cbr"
    NBRK = "nbrk"
    MANUAL = "manual"


RATE_SOURCE_LABELS: Mapping[RateSource, str] = {
    RateSource.CBR: "ЦБ РФ",
    RateSource.NBRK: "НБРК",
    RateSource.MANUAL: "введены вручную",
}
# Официальные курсы ЦБ РФ на дату: рубли за Nominal единиц валюты.
CBR_DAILY_URL = "https://www.cbr.ru/scripts/XML_daily.asp?date_req={date:%d/%m/%Y}"
# Официальные курсы НБРК на дату: тенге за quant единиц валюты.
NBRK_RATES_URL = "https://nationalbank.kz/rss/get_rates.cfm?fdate={date:%d.%m.%Y}"
FETCH_TIMEOUT_SECONDS = 10
# Ответ сервиса курсов — несколько килобайт; больше — не тот документ.
MAX_RESPONSE_BYTES = 1_000_000
USER_AGENT = "Rentab (https://github.com/mystuff4git/rentab)"
# Курсы на сегодня могут обновиться в течение дня; на прошедшую дату — нет.
TODAY_CACHE_TTL = dt.timedelta(hours=6)

Fetcher = Callable[[str], bytes]
Clock = Callable[[], dt.datetime]


class RatesQuote(CoreModel):
    """Курсы, загруженные на дату.

    Attributes:
        rates: Курсы ₽ за 1 ₸ и ₽ за 1 $.
        source: Источник.
        rate_date: Дата курсов по данным источника (в выходной — последняя
            рабочая или следующая установленная дата).
        requested: Дата, на которую запрашивали курсы.
    """

    rates: ExchangeRates
    source: RateSource
    rate_date: dt.date
    requested: dt.date


def http_get(url: str) -> bytes:
    """Скачивает документ по HTTPS (urllib, системный прокси и сертификаты).

    Raises:
        ExchangeRateFetchError: Сеть недоступна, ответ не 200 или слишком большой.
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
            content: bytes = response.read(MAX_RESPONSE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ExchangeRateFetchError(f"Не удалось загрузить {url}: {exc}") from exc
    if len(content) > MAX_RESPONSE_BYTES:
        raise ExchangeRateFetchError(f"Слишком большой ответ от {url}")
    return content


def _xml(content: bytes, source: RateSource) -> ET.Element:
    try:
        return ET.fromstring(content)
    except ET.ParseError as exc:
        raise ExchangeRateFetchError(
            f"Ответ {RATE_SOURCE_LABELS[source]} не разбирается как XML: {exc}"
        ) from exc


def _decimal(text: str | None, source: RateSource, what: str) -> Decimal:
    try:
        value = Decimal((text or "").strip().replace(",", "."))
    except InvalidOperation as exc:
        raise ExchangeRateFetchError(
            f"{RATE_SOURCE_LABELS[source]}: не число в поле {what}: {text!r}"
        ) from exc
    if not value.is_finite() or value <= 0:
        raise ExchangeRateFetchError(f"{RATE_SOURCE_LABELS[source]}: {what} = {text!r}")
    return value


def _date(text: str | None, source: RateSource) -> dt.date:
    try:
        return dt.datetime.strptime((text or "").strip(), "%d.%m.%Y").date()
    except ValueError as exc:
        raise ExchangeRateFetchError(
            f"{RATE_SOURCE_LABELS[source]}: дата курсов {text!r} не разбирается"
        ) from exc


def parse_cbr(content: bytes, requested: dt.date) -> RatesQuote:
    """Разбирает `XML_daily.asp` ЦБ РФ: ₽ за 1 ₸ и ₽ за 1 $ = Value / Nominal.

    Raises:
        ExchangeRateFetchError: Документ не разбирается или в нём нет KZT и USD.
    """
    source = RateSource.CBR
    root = _xml(content, source)
    per_unit: dict[str, Decimal] = {}
    for valute in root.iter("Valute"):
        code = (valute.findtext("CharCode") or "").strip()
        if code in (Currency.KZT, Currency.USD):
            value = _decimal(valute.findtext("Value"), source, f"Value {code}")
            nominal = _decimal(valute.findtext("Nominal"), source, f"Nominal {code}")
            per_unit[code] = value / nominal
    missing = {Currency.KZT, Currency.USD} - per_unit.keys()
    if missing:
        raise ExchangeRateFetchError(f"ЦБ РФ: в ответе нет курсов {', '.join(sorted(missing))}")
    return RatesQuote(
        rates=ExchangeRates(rub_per_kzt=per_unit[Currency.KZT], rub_per_usd=per_unit[Currency.USD]),
        source=source,
        rate_date=_date(root.get("Date"), source),
        requested=requested,
    )


def parse_nbrk(content: bytes, requested: dt.date) -> RatesQuote:
    """Разбирает `get_rates.cfm` НБРК: тенге за quant единиц → рубли за единицу.

    ₽ за 1 ₸ = 1 / (₸ за 1 ₽); ₽ за 1 $ = (₸ за 1 $) / (₸ за 1 ₽).

    Raises:
        ExchangeRateFetchError: Документ не разбирается или в нём нет RUB и USD.
    """
    source = RateSource.NBRK
    root = _xml(content, source)
    kzt_per: dict[str, Decimal] = {}
    for item in root.iter("item"):
        code = (item.findtext("title") or "").strip()
        if code in (Currency.RUB, Currency.USD):
            value = _decimal(item.findtext("description"), source, f"description {code}")
            quant = _decimal(item.findtext("quant"), source, f"quant {code}")
            kzt_per[code] = value / quant
    missing = {Currency.RUB, Currency.USD} - kzt_per.keys()
    if missing:
        raise ExchangeRateFetchError(f"НБРК: в ответе нет курсов {', '.join(sorted(missing))}")
    kzt_per_rub = kzt_per[Currency.RUB]
    return RatesQuote(
        rates=ExchangeRates(
            rub_per_kzt=ONE / kzt_per_rub, rub_per_usd=kzt_per[Currency.USD] / kzt_per_rub
        ),
        source=source,
        rate_date=_date(root.findtext("date"), source),
        requested=requested,
    )


SOURCES: tuple[tuple[RateSource, str, Callable[[bytes, dt.date], RatesQuote]], ...] = (
    (RateSource.CBR, CBR_DAILY_URL, parse_cbr),
    (RateSource.NBRK, NBRK_RATES_URL, parse_nbrk),
)

_cache: dict[tuple[RateSource, dt.date], tuple[RatesQuote, dt.datetime]] = {}
_cache_lock = threading.Lock()


def clear_cache() -> None:
    """Очищает кэш загруженных курсов."""
    with _cache_lock:
        _cache.clear()


def _cached(key: tuple[RateSource, dt.date], now: dt.datetime) -> RatesQuote | None:
    with _cache_lock:
        entry = _cache.get(key)
    if entry is None:
        return None
    quote, fetched_at = entry
    if key[1] < now.date() or now - fetched_at < TODAY_CACHE_TTL:
        return quote
    return None


def fetch_rates(
    on: dt.date | None = None,
    *,
    fetch: Fetcher = http_get,
    clock: Clock = dt.datetime.now,
) -> RatesQuote:
    """Курсы ₽ за 1 ₸ и ₽ за 1 $ на дату: ЦБ РФ, при ошибке — НБРК.

    Результат кэшируется в памяти процесса по источнику и дате.

    Args:
        on: Дата курсов; по умолчанию — сегодня.
        fetch: Загрузка документа по URL (подменяется в тестах).
        clock: Текущее время (подменяется в тестах).

    Raises:
        ExchangeRateFetchError: Не ответил ни один источник; текст перечисляет
            ошибки каждого.
    """
    now = clock()
    requested = on or now.date()
    errors: list[str] = []
    for source, url, parse in SOURCES:
        key = (source, requested)
        cached = _cached(key, now)
        if cached is not None:
            return cached
        try:
            quote = parse(fetch(url.format(date=requested)), requested)
        except ExchangeRateFetchError as exc:
            errors.append(str(exc))
            continue
        with _cache_lock:
            _cache[key] = (quote, now)
        return quote
    raise ExchangeRateFetchError("Курсы не загружены, введите их вручную. " + " ".join(errors))
