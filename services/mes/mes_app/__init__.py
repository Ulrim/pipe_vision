"""AIVIS MES — 품질·생산실적 관리 (2026-10-14).

AIVIS(검사)와 **별도 서비스**다. 자기 DB·자기 API 를 갖고, AIVIS 와는 연계
인터페이스(`mes_quality_if` 테이블 폴링 또는 REST 수신)로만 주고받는다. 나중에
상용 MES 로 갈아끼워도 AIVIS 는 바뀌지 않는다.
"""
