# AIVIS data-ops + MES 연계 (data-mes 에이전트)

CLAUDE.md §5(M9, M16), §6.4, §7.3, 부록 A 를 구현한다. 소유 디렉터리:
`services/api/mes/*` (MES 연계 어댑터) + `services/data-ops/*` (데이터 운영).

DB 스키마/모델은 backend(`services/api/db/models.py`)를 **import 재사용**하며
정의/마이그레이션을 복제·변경하지 않는다. 라이브 PostgreSQL 없이 sqlite 로
모든 단위 테스트가 통과한다.

---

## 1. MES 연계 어댑터 (`services/api/mes/`)

검사 1건(`inspection` 행)을 MES 로 연계한다. 멱등키 = `lot|item_code|inspected_at|cam_id`
(중복 적재 금지, `mes_quality_if.idem_key` UNIQUE).

| 파일 | 역할 |
|---|---|
| `config.py` | `MES_MODE`(table\|rest) + 워치독/재시도 설정(환경변수). **backend 제공(변경 금지)** |
| `transport.py` | REST 전송 추상화: `HttpxMesTransport`(실제), `FakeMesTransport`(통합 전/테스트) |
| `adapter.py` | table/rest 모드 단건 연계, 멱등 스테이징, 실패 시 `sys_log(category=mes)` |
| `watchdog.py` | 미전송(`mes_synced=false`) 재시도, 연계율 100% 보장 + 연계 상태 조회 |
| `cli.py` | 운영 CLI(status / watchdog) |

### 모드 전환 (§7.3 우선순위: table 우선)

```bash
export MES_MODE=table   # 기본. mes_quality_if 스테이징 테이블을 MES가 폴링
export MES_MODE=rest    # 외부 MES REST로 직접 POST
export MES_REST_URL=https://mes.example/quality   # rest 모드 엔드포인트
export MES_IDEM_HEADER=X-Idempotency-Key          # 멱등키 헤더명(기본값)
```

- **table 모드**: `mes_quality_if` 에 멱등 INSERT(이미 backend `POST /inspection`
  트랜잭션이 적재). 어댑터는 누락분을 보충 스테이징하고, 스테이징 보장 = 연계
  완료 계약으로 `inspection.mes_synced=true` 표시.
- **rest 모드**: `MesTransport.send(payload, idem_key=...)` 로 외부 MES POST.
  멱등키를 헤더+바디 양쪽에 실어 전송. 성공 시 `mes_synced=true`, 실패 시
  유지(워치독 재시도). `MES_REST_URL` 미설정이면 `FakeMesTransport` 주입으로
  파이프라인이 끊기지 않음(통합 전 단계).

### 워치독 (연계율 100% 보장)

```bash
cd services/api
python -m mes.cli status                 # {total, synced, pending, rate, mode}
python -m mes.cli watchdog --once        # 미전송 1배치 재연계
python -m mes.cli watchdog --cycles 5    # 5주기 후 종료
python -m mes.cli watchdog               # 무한 주기(서비스형)
```

설정(환경변수):

| 변수 | 기본 | 설명 |
|---|---|---|
| `MES_WATCHDOG_INTERVAL_S` | 10.0 | 주기(초) |
| `MES_WATCHDOG_BATCH` | 100 | 1회 재시도 배치 크기 |
| `MES_MAX_RETRY` | 8 | 행별 최대 재시도 |
| `MES_BACKOFF_BASE_S` / `MES_BACKOFF_MAX_S` | 0.5 / 30 | 지수 백오프 |

백그라운드 기동(코드): `mes.watchdog.run_watchdog_forever(stop_event=...)`.
연계 상태(대시보드용): `mes.watchdog.get_linkage_status(db)`.

---

## 2. 데이터 운영 (`services/data-ops/`)

### 2.1 라벨링 / 정답셋 (`labeling/`, 부록 A.4)

`dataset/raw/<CLASS>/{품목}_{구도}_{클래스}_{YYYYMMDD-HHmmss}_{seq}.jpg` +
동일명 사이드카 `.json`(`item_code, view(END|SIDE), labels[], border,
length_mm_gt, scale_ref_mm ...`)을 읽어 정답셋(ground-truth)을 구성한다.
QA 에이전트가 이 매니페스트를 소비해 항목별 정확도/혼동행렬(§1.2)을 산출한다.

```bash
cd services/data-ops
export PYTHONPATH=../api          # backend db 모델 재사용
export AIVIS_DATASET_DIR=/data/dataset/raw

python -m labeling.cli build --dataset $AIVIS_DATASET_DIR --out gt.json
python -m labeling.cli build --dataset $AIVIS_DATASET_DIR --view SIDE --out gt_side.json
python -m labeling.cli inspect --image HP12_SIDE_SCR_20260610-141233_007.jpg
```

- 사이드카 우선, 없으면 파일명의 클래스 코드로 폴백.
- 라벨은 **배열**(복합불량) → §7.2 `defect_codes` 매핑. 2종 이상이면 `MULTI` 보강.
- `border:true` 경계 샘플 카운트 별도 집계(부록 A.2 — 정확도 95% 돌파 핵심).

### 2.2 재학습 / 임계 보정 (`retrain/`, §5 M16)

오검·미검(`manual_verdict != final_verdict`) + `review_flag=true` 행을 추출해
§6.4 `review/` 버킷 규격으로 재학습 데이터셋을 빌드한다.

```bash
cd services/data-ops
export PYTHONPATH=../api

# 오검·미검 후보 추출(오검=system_ng_human_ok, 미검=system_ok_human_ng)
python -m retrain.cli candidates --item HP12

# 재학습 데이터셋 매니페스트 + 이미지 복사(review/<miss_kind>/)
python -m retrain.cli build --out /data/retrain --item HP12 --copy --image-root /data

# 임계값 보정 제안(현재 임계 vs 사람 재확인 기준 제안 임계 비교)
python -m retrain.cli thresholds --item HP12 --min-samples 10
```

- 임계 제안은 사람이 검토 후 `item_master` 에 반영하는 **제안값**(자동 적용 안 함).
- 표본 부족 시 현재 임계 유지(보수적).

---

## 3. 테스트

```bash
# MES 어댑터/워치독/전송 (services/api)
cd services/api && python -m pytest mes/tests -q

# 라벨링/재학습 (services/data-ops)
cd services/data-ops && python -m pytest -q
```

모두 임시 sqlite 로 동작하며 외부 의존(라이브 DB/MES) 없이 통과한다.
