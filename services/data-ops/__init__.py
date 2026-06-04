"""AIVIS data-ops 패키지 (CLAUDE.md §5 M16, 부록 A).

data-mes 에이전트 소유. 학습/검증 데이터 운영:
- labeling/ : 부록 A.4 사이드카 .json + 파일명 규칙으로 정답셋(ground-truth) 구성.
- retrain/  : 오검·미검(review) 분리·태깅 → 재학습 데이터셋 빌드, 임계값 보정.
"""
