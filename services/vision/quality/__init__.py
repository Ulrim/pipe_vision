"""품질/측정시스템 분석 유틸 (제품 코드).

MSA(길이 반복성/재현성, §5 M3 DoD)를 tests/ 밖 제품 코드로 둔다 — 현장
(라즈베리파이)에서 CLI(tools/run_msa.py)로 FAT/SAT 인수 자료를 산출할 수 있게
한다. tests/harness/msa.py 는 하위호환을 위해 이 모듈을 re-export 한다.
"""
from __future__ import annotations

from .msa import MsaResult, run_msa, write_msa_reports

__all__ = ["MsaResult", "run_msa", "write_msa_reports"]
