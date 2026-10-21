"""Rentab Core — расчётное ядро Rentab.

Чистый Python-пакет без зависимостей от UI: себестоимость часа, накладные,
налоги, модели ценообразования и аналитика проектов IP-юридического
консалтинга (РФ, РК) по методологии Д. Майстера.

Ядро не импортирует streamlit и plotly — это проверяется линтером и тестами.
Формулы описаны в docs/methodology.md (появится в фазе 2).

Основной сценарий:

    from rentab_core import Scenario, calculate_estimate, dashboard

    estimate = calculate_estimate(Scenario(firm=firm, project=project))
    kpi = dashboard(estimate)
"""

__version__ = "1.0.0.dev0"

from rentab_core.analysis.dashboard import Dashboard, dashboard
from rentab_core.errors import FixedPriceError, ReferenceDataError, RentabError
from rentab_core.estimate import calculate_estimate
from rentab_core.export import estimate_xlsx
from rentab_core.models import Firm, Project, Scenario
from rentab_core.results import Estimate, FixedPriceEstimate, HourlyEstimate

__all__ = [
    "Dashboard",
    "Estimate",
    "Firm",
    "FixedPriceError",
    "FixedPriceEstimate",
    "HourlyEstimate",
    "Project",
    "ReferenceDataError",
    "RentabError",
    "Scenario",
    "__version__",
    "calculate_estimate",
    "dashboard",
    "estimate_xlsx",
]
