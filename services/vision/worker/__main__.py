"""`python -m worker` 진입점 — 검사 런타임 루프 기동."""
from __future__ import annotations

import sys

from .runner import main

if __name__ == "__main__":
    sys.exit(main())
