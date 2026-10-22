"""Генерация JSON Schema файла профиля:

uv run python -m rentab_core.io docs/profile.schema.json
"""

import sys

from rentab_core.io.schema import main

main(sys.argv[1:])
