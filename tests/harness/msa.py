"""MSA re-export shim — 실제 구현은 services/vision/quality/msa.py (제품 코드).

MSA 핵심 로직을 tests/ 밖 제품 코드로 옮겼다(현장 CLI/재사용). 기존 임포트
경로 호환을 유지하기 위해 이 모듈은 vision.quality.msa 를 그대로 re-export 한다:

    from tests.harness.msa import run_msa, MsaResult      # 기존 방식 계속 동작
    from tests.harness import msa as msa_mod              # msa_mod.run_msa(...)

§5 M3 DoD(길이 반복성/재현성)의 산출/판정 로직은 vision.quality.msa 에 단일
진실원으로 존재한다.
"""
from __future__ import annotations

from vision.quality.msa import MsaResult, run_msa, write_msa_reports

__all__ = ["MsaResult", "run_msa", "write_msa_reports"]
