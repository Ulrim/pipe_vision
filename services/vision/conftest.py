"""pytest 부트스트랩: services/ 를 sys.path 에 올려 `import vision.*` 가능하게 한다.

services/__init__.py 가 없어 네임스페이스 'services.vision' 대신 'vision' 패키지로
import 한다(vision/__init__.py 존재). 모든 내부 모듈은 상대 import 를 쓴다.
"""
from __future__ import annotations

import sys
from pathlib import Path

# services/vision/conftest.py → parent = services/vision, parents[1] = services
_SERVICES_DIR = Path(__file__).resolve().parents[1]
if str(_SERVICES_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVICES_DIR))
