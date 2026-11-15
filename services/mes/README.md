# services/mes — AIVIS MES

AIVIS(검사)와 **별도 서비스**(:8100). 수주 → 작업지시(LOT) → 검사결과 수신 → 생산실적 →
출하 → 클레임 → 월 KPI, 그리고 기준정보(품목·BOM·공정·라우팅·설비) · 공정 진행 · 자재/재고 ·
설비 가동(2026-11-15, `docs/MES.md` §11). AIVIS 와는 연계 인터페이스(`mes_quality_if` 폴링 또는 REST 수신)로만
주고받는다. 설계·규칙 전체: [`docs/MES.md`](../../docs/MES.md), 연계 규격:
[`docs/MES_INTERFACE.md`](../../docs/MES_INTERFACE.md).

```bash
# 개발 실행(같은 JWT_SECRET 으로 AIVIS 토큰을 그대로 쓴다)
cd services/mes
MES_DATABASE_URL=sqlite:///./mes.db \
MES_AIVIS_DB_URL=sqlite:////var/lib/aivis/db/aivis.db \
JWT_SECRET=<AIVIS 와 같은 값> \
uvicorn mes_app.main:app --port 8100

# 테스트
pytest -q
```

| 모듈 | 하는 일 |
|---|---|
| `mes_app/intake.py` | 연계 테이블 폴링(적재 커밋 후 `consumed` 표시, 읽는 중 바뀐 행은 보류), REST upsert |
| `mes_app/production.py` | 생산실적(단계별·재확인 반영·크레이트 개수 → 양품) |
| `mes_app/kpi.py` | 월 출하수량·리드타임·납기 준수·Claim·출하유출 |
| `mes_app/plant.py` | 라우팅 찾기·공정 목록 만들기, 현재고·자재 소요, 생산 입고 동기화, 설비 상태 전환·가동률 |
| `mes_app/routers/` | orders(수주·작업지시) · logistics(출하·클레임·KPI) · intake(수신·연계현황·품질이력) · master(품목·BOM·공정·라우팅) · shopfloor(공정 진행·자재 투입) · stock(재고·수불) · equipment(설비·가동률) |
| `mes_app/security.py` | AIVIS JWT 검증(로그인 하나), REST 수신 토큰 |
