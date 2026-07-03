# AIVIS 데이터 모델 (DATA_MODEL.md)

> 본 문서는 DB 스키마의 단일 진실원이다. CLAUDE.md §7.1 스키마를 그대로 구현하며,
> 변경 시 오케스트레이터 승인이 필요하다(`.claude/agents/backend.md`).
> 구현 위치: `services/api/db/models.py` (SQLAlchemy 2.0), `services/api/alembic/versions/`.

## 환경 / 방언

- 운영: **PostgreSQL 16** (`DATABASE_URL=postgresql+psycopg://...`).
- 개발/테스트: **SQLite** (DATABASE_URL 미설정 시 `sqlite:///./aivis_dev.db`).
- 포터블 타입(`db/types.py`):
  - `defect_codes` → Postgres `TEXT[]`, SQLite `JSON`.
  - `capture_recipe`, `sys_log.payload` → Postgres `JSONB`, SQLite `JSON`.
- 마이그레이션: Postgres 는 `alembic upgrade head`. SQLite 개발 시 `init_db()` 가 자동 생성.

## 테이블

### item_master — 품목/기준정보 (§5 M13)
| 컬럼 | 타입 | 비고 |
|---|---|---|
| item_code | TEXT PK | 품목 코드 |
| item_name | TEXT NOT NULL | 품목명 |
| ref_length_mm | NUMERIC(10,3) NOT NULL | 기준 길이 |
| tol_plus_mm | NUMERIC(10,3) NOT NULL | 허용 공차 + |
| tol_minus_mm | NUMERIC(10,3) NOT NULL | 허용 공차 − |
| px_to_mm_scale | NUMERIC(12,6) NOT NULL | 픽셀-mm 환산 보정계수 |
| oil_threshold | NUMERIC(5,4) | 유분기 임계 0~1 |
| discolor_threshold | NUMERIC(5,4) | 변색 임계 0~1 |
| scratch_threshold | NUMERIC(5,4) | 스크래치 임계 0~1 |
| capture_recipe | JSONB | 촬영 레시피(노출/게인/조명) |
| version | INT NOT NULL DEFAULT 1 | 변경 시 +1 (이력) |
| updated_by | TEXT | 최종 수정자 |
| updated_at | TIMESTAMPTZ | 최종 수정 시각 |

### inspection — 검사결과 (제품 1개 = 1행) (§5 M7,M8)
식별/메타: `id`(BIGSERIAL PK), `lot`(NOT NULL), `work_order`, `item_code`(FK→item_master),
`cam_id`(NOT NULL), `inspected_at`(TIMESTAMPTZ NOT NULL), `shift`, `operator`.
길이: `ref_length_mm`, `meas_length_mm`, `deviation_mm`, `length_verdict`(OK/NG).
표면(0~1): `oil_score`, `discolor_score`, `scratch_score` (NUMERIC(5,4)).
종합: `final_verdict`(NOT NULL OK/NG), `defect_codes`(TEXT[] {LEN,OIL,DIS,SCR,MULTI}),
`confidence`, `raw_image_path`, `result_image_path`, `proc_time_ms`(처리속도 KPI).
운영/재확인: `review_flag`(BOOL d.false), `manual_verdict`(OK/NG), `mes_synced`(BOOL d.false).

인덱스(§7.1):
- `ix_insp_lot` (lot)
- `ix_insp_time` (inspected_at)
- `ix_insp_item_verdict` (item_code, final_verdict)

### kpi_manual — 비자동 KPI (§5 M12, §1.1)
`period`(DATE PK, 월 1일), `claim_count`(INT), `workload_index`(NUMERIC),
`lead_time_days`(NUMERIC), `note`(TEXT).

### app_user — 사용자/권한 (§5 M14)
`username`(TEXT PK), `pw_hash`(TEXT NOT NULL, bcrypt), `role`(TEXT NOT NULL
CHECK in operator/quality/admin), `active`(BOOL d.true).

### sys_log — 로그 (§5 M15)
`id`(BIGSERIAL PK), `ts`(TIMESTAMPTZ d.now()), `level`, `category`
(inspect/db/mes/error/user), `message`, `payload`(JSONB).

### mes_quality_if — MES 연계 스테이징 (§7.3)
DB 인터페이스 테이블 방식. 검사결과의 식별자+판정 핵심값을 적재하면 MES 가 폴링/트리거로 소비.
`id`(BIGSERIAL PK), `inspection_id`(FK→inspection), `lot`, `item_code`, `inspected_at`,
`cam_id`, `idem_key`(TEXT UNIQUE = `lot|item_code|inspected_at|cam_id`), `work_order`,
`final_verdict`, `defect_codes`(TEXT[]), `meas_length_mm`, `deviation_mm`,
`consumed`(BOOL d.false), `retry_count`(INT d.0), `created_at`(TIMESTAMPTZ).
인덱스 `ix_mesif_consumed`(consumed).

## 저장 무결성 / 100% 저장 전략 (§5 M7 DoD)
- `core/inspection_service.save_inspection()` 가 한 트랜잭션에서
  `inspection` INSERT + (table 모드) `mes_quality_if` 멱등 INSERT 를 함께 커밋.
- 저장 실패 시 라우터가 롤백 후 `core/local_queue` 로 검사결과 JSON 을 파일 백업(원자적 write),
  `POST /inspection` 은 `status=queued` 응답. 워치독/`POST /inspection/retry-queue` 가 재처리.
- MES 멱등키 중복은 스테이징을 건너뛰어 중복 적재를 방지.
