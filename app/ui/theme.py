"""Оформление поверх темы Streamlit: KPI-карточки, светофор, пустые состояния, таблицы.

Цвета, шрифты и скругления темы — в `.streamlit/config.toml` (светлая и
тёмная). Streamlit не отдаёт цвета темы в CSS, поэтому стили здесь не
зависят от темы: границы и фон — полупрозрачные, текст наследуется, а цвета
статусов подобраны так, что значок или полоска контрастны (не ниже 3:1) и на
светлом, и на тёмном фоне. Текст статуса — всегда рядом со значком.

Классы `st-key-…` Streamlit ставит контейнерам с ключом (`st.container(key=…)`):
по ним стили находят KPI-карточки и пустые состояния.
"""

from __future__ import annotations

import streamlit as st

# Значки и полоски статусов: контраст не ниже 3:1 к фону #FBF9F5 и #1C1A17.
STATUS_GOOD = "#3A9A5E"
STATUS_WARN = "#B7821A"
STATUS_BAD = "#CC4A5C"
# Ширина экрана, ниже которой строки таблиц становятся карточками.
NARROW_SCREEN_PX = 640

CSS = f"""
<style>
:root {{
  --rt-good: {STATUS_GOOD};
  --rt-warn: {STATUS_WARN};
  --rt-bad: {STATUS_BAD};
  --rt-line: rgba(128, 120, 110, 0.28);
  --rt-line-soft: rgba(128, 120, 110, 0.16);
  --rt-tint: rgba(128, 120, 110, 0.07);
}}

/* Цифры одинаковой ширины: колонки и карточки не «пляшут». */
[data-testid="stMetricValue"], .rt-table td, .rt-table th {{
  font-variant-numeric: tabular-nums;
}}
[data-testid="stMetricValue"] {{ white-space: nowrap; }}

/* KPI-карточка: контейнер с рамкой и ключом «kpi-…»; статус — полоска сверху.
   Карточки одного ряда — одной высоты; подпись переносится, а не обрезается. */
[class*="st-key-kpi-"] {{ border-top-width: 3px !important; height: 100%; }}
[data-testid="stColumn"]:has([class*="st-key-kpi-"], [class*="st-key-step-"])
  > [data-testid="stVerticalBlock"] {{ height: 100%; }}
[data-testid="stLayoutWrapper"]:has(> [class*="st-key-kpi-"], > [class*="st-key-step-"]) {{
  flex: 1 1 auto;
}}
[class*="st-key-kpi-"] [data-testid="stMetricLabel"] .stMarkdownEllipsis,
[class*="st-key-kpi-"] [data-testid="stMetricLabel"] p {{
  white-space: normal; overflow: visible; text-overflow: clip;
}}
[class*="st-key-kpi-"][class*="--good"] {{ border-top-color: var(--rt-good) !important; }}
[class*="st-key-kpi-"][class*="--warn"] {{ border-top-color: var(--rt-warn) !important; }}
[class*="st-key-kpi-"][class*="--bad"] {{ border-top-color: var(--rt-bad) !important; }}
[class*="st-key-kpi-"] [data-testid="stCaptionContainer"] {{ margin-top: -0.4rem; }}

/* Пустое состояние: пунктирная рамка, текст по центру. */
[class*="st-key-empty-"] {{
  border-style: dashed !important;
  background: var(--rt-tint);
  text-align: center;
  align-items: center;
}}
[class*="st-key-empty-"] [data-testid="stMarkdownContainer"] p {{ text-align: center; }}
.rt-empty-icon {{ font-size: 2rem; line-height: 1.2; }}

/* Шаги «Как начать» на главной. */
[class*="st-key-step-"] {{ height: 100%; }}

/* Логотип для КП — на белом листе, как в документе: в тёмной теме тёмный текст
   прозрачного PNG иначе не виден. */
.st-key-logo-preview {{
  background: #FFFFFF; border-radius: 0.5rem; padding: 0.75rem; width: fit-content;
}}

/* Значок статуса в таблицах: цвет + форма (●, ▲, ✕), подпись — рядом. */
.rt-flag {{ font-weight: 700; margin-right: 0.3rem; }}
.rt-flag--good {{ color: var(--rt-good); }}
.rt-flag--warn {{ color: var(--rt-warn); }}
.rt-flag--bad {{ color: var(--rt-bad); }}

/* Таблицы аналитики: на широком экране — таблица, на узком — карточки. */
.rt-table {{ width: 100%; margin: 0.25rem 0 0.75rem; }}
.rt-table table {{ width: 100%; border-collapse: collapse; font-size: 0.92rem; }}
.rt-table th {{
  text-align: left; font-weight: 400; opacity: 0.75;
  border-bottom: 1px solid var(--rt-line); padding: 0.4rem 0.5rem; vertical-align: bottom;
}}
.rt-table td {{
  border-bottom: 1px solid var(--rt-line-soft); padding: 0.4rem 0.5rem; vertical-align: top;
}}
.rt-table .num {{ text-align: right; white-space: nowrap; }}
.rt-table tr.rt-muted td {{ opacity: 0.55; }}
.rt-table tr.rt-total td {{ font-weight: 700; border-top: 1px solid var(--rt-line); }}
.rt-table td.rt-best {{ font-weight: 700; }}
.rt-table td.rt-current {{ background: var(--rt-tint); box-shadow: inset 0 -2px 0 var(--rt-line); }}
.rt-table .rt-note {{ display: block; opacity: 0.7; font-size: 0.85em; }}
@media (max-width: {NARROW_SCREEN_PX}px) {{
  [class*="st-key-kpi-"] [data-testid="stMetricValue"] {{ font-size: 1.75rem; }}
  .rt-flat table {{ font-size: 0.85rem; }}
  .rt-flat th, .rt-flat td {{ padding: 0.35rem 0.25rem; }}
  .rt-table:not(.rt-flat) thead {{ display: none; }}
  .rt-table:not(.rt-flat) table, .rt-table:not(.rt-flat) tbody {{ display: block; }}
  .rt-table:not(.rt-flat) tr {{
    display: block; border: 1px solid var(--rt-line); border-radius: 0.5rem;
    padding: 0.35rem 0.7rem; margin-bottom: 0.6rem;
  }}
  .rt-table:not(.rt-flat) td {{
    display: flex; justify-content: space-between; align-items: baseline; gap: 0.75rem;
    border: none; padding: 0.15rem 0; text-align: right;
  }}
  .rt-table:not(.rt-flat) td::before {{
    content: attr(data-label); opacity: 0.7; text-align: left; flex: 0 1 50%;
  }}
  .rt-table:not(.rt-flat) td.rt-lead {{
    display: block; text-align: left; font-weight: 700; padding-bottom: 0.3rem;
  }}
  .rt-table:not(.rt-flat) td.rt-lead::before {{ content: none; }}
  .rt-table:not(.rt-flat) td:empty {{ display: none; }}
  .rt-table:not(.rt-flat) .num {{ white-space: normal; }}
}}
</style>
"""


def apply() -> None:
    """Добавляет стили на страницу (вызывается точкой входа при каждом запуске)."""
    st.html(CSS)
