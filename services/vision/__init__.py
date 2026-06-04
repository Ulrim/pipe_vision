"""AIVIS vision service (services/vision).

검사 추론 엔진: 이미지 취득(HAL) → 전처리 → 길이/표면 분석 → 종합 판정.
모든 추론은 결정적이며 proc_time_ms 를 계측해 반환한다(CLAUDE.md §6, vision-ai 원칙).
공용 출력 스키마는 aivis_types(packages/shared-types) 를 그대로 import 한다.
"""
