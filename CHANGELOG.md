# Changelog

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/), версии — по
[SemVer](https://semver.org/lang/ru/).

## [Unreleased]

### Added

- Каркас репозитория: пакет `rentab_core` (src-layout), uv, ruff, mypy strict,
  pytest с hypothesis и порогом покрытия ядра 90%.
- CI на GitHub Actions: ruff, mypy, pytest на Python 3.11 и 3.13.
- Код Rentab v0.3 в `legacy/rentab_v03/` как эталон поведения.
- Characterization-тесты v0.3:
  - снимки по 13 налоговым конфигурациям × 2 модели ценообразования;
  - контрольные сценарии, слайд 3 защиты, светофор этапов, режим «Оба»;
  - XLSX-смета, ФОТ и зарплатные налоги, формат профиля фирмы.
- Аудит v0.3 (`docs/legacy_audit.md`), ROADMAP, ADR 0001–0002, `CLAUDE.md`.
