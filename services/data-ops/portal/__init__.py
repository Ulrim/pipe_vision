"""전남 AX 오픈플랫폼 데이터포털 제출 모듈 (협약서 제16조 데이터의 수집·활용).

세 데이터셋(원시/가공/AI분석)을 포털 규격 폴더로 내보내고(export), 포털 업로드
API(`POST /dataset-uploads`, `X-Dataset-Code`)로 전송한다(upload).

- 레이아웃/명세 단일 진실원: `portal.layout` (docs/DATA_DEFINITION.md 와 1:1)
- 내보내기: `portal.export`
- 업로드(B안 API 직접 연계): `portal.upload`
- CLI: `python -m portal.cli`
"""
