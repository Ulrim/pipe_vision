# AIVIS 백엔드 API (API.md)

> CLAUDE.md §7.4 엔드포인트 요약의 구현. 구현 위치: `services/api/routers/`, `services/api/ws/`.
> 공용 스키마는 `packages/shared-types`(Python `aivis_types` + TS mirror). 변경은 오케스트레이터 승인.

## 인증 / RBAC (§5 M14)
- JWT(HS256, `JWT_SECRET`). 역할 위계: `admin` > `quality` > `operator`.
- `require_role(*roles)` 정확 매칭, `require_min_role(min)` 위계 이상 허용, `require_internal` 검사워커 내부 토큰.

### 역할 정책 요약
| 역할 | 조회(GET inspection/master/kpi/logs) | 재확인(PATCH review) | 기준정보 수정 / KPI 수기입력 | 로그 조회 | 사용자 관리 | 품목 삭제 |
|---|---|---|---|---|---|---|
| operator | O | O | X (단, 오더 치수는 O — 아래) | X(quality+) | X | X |
| quality | O | O | O | O | X | X |
| admin | O | O | O | O | O | O |

- `/logs` 조회는 quality+ (운영 민감 정보). 그 외 조회(inspection/master/kpi summary·report 중 summary)는 operator+.
- `/kpi/report` 는 quality+ (리포트 산출물).
- **예외 — 검사 모드 전환은 operator+** (2026-10-05). 카메라를 다른 자리로 돌려 세운 작업자가 바로 모드를 맞춰야 한다. 바꿀 수 있는 것은 `inspection_stage` 하나다.
- **예외 — 오더 치수 변경은 operator+** (2026-10-03 도입기업 확정). 기준정보 전체 수정(`PUT /master/items/{code}`)은 quality+ 그대로지만,
  주문마다 바뀌는 **기준길이·공차·검사개수**는 `PUT /master/items/{code}/spec` 으로 작업자가 직접 바꾼다. 권한을 넓히는 대신
  수정 가능 항목을 그 셋으로 좁히고 전건을 before→after 감사 로그로 남겨 위험을 상쇄했다. `AIVIS_SPEC_EDIT_MIN_ROLE` 로 조일 수 있다.

### 로그 적재 커버리지 (M15, sys_log.category)
| 동작 | category | 위치 |
|---|---|---|
| 로그인 | user | `auth.login` |
| 사용자 생성 | user | `auth.create_user` |
| 기준정보 생성/수정/삭제 | user | `master.*` |
| 검사 재확인 | user | `inspection.review` |
| 검사결과 저장 성공 | db | `inspection._save_with_backup` |
| 검사결과 저장 실패(로컬 큐 백업) | error | `inspection._save_with_backup` |
| MES REST 스테이징 | mes | `mes.quality` |

| 메서드 | 경로 | 권한 | 설명 |
|---|---|---|---|
| POST | `/auth/login` | 공개 | JSON 로그인 → `TokenResponse` |
| POST | `/auth/login/oauth` | 공개 | OAuth2 password-form (Swagger Authorize) |
| POST | `/auth/users` | admin | 사용자 등록 → `UserPublic` |
| GET | `/auth/me` | 로그인 | 내 정보 |

## 검사결과 (§5 M7,M8,M10)
| 메서드 | 경로 | 권한 | 설명 |
|---|---|---|---|
| POST | `/inspection` | 내부(검사워커) | 결과 적재. 성공 `status=stored`, DB 실패 시 로컬 큐 백업 `status=queued`. 저장 성공 시 WS 푸시 + 연속 NG 알람(M6) |
| POST | `/inspection/retry-queue` | quality+ | 로컬 큐 백업분 재저장 |
| GET | `/inspection?lot=&item=&from=&to=&verdict=&limit=&offset=` | operator+ | 필터 조회(서버 페이지네이션) |
| GET | `/inspection/{id}` | operator+ | 단건 조회 |
| GET | `/inspection/{id}/images` | operator+ | 원본/결과 이미지 경로 → `InspectionImages` |
| GET | `/inspection/{id}/images/{kind}` | operator+ | 원본/결과 이미지 **바이트** 스트리밍(`kind`=raw\|result, `image/jpeg`). 공유 볼륨 `AIVIS_IMAGES_DIR` 하위 상대경로를 traversal 안전하게 서빙. 경로 없음/파일 부재/escape 시 404 |
| PATCH | `/inspection/{id}/review` | operator+ | NG 재확인 결과 입력(`manual_verdict`, `review_flag` 해제) |

### 내부 호출 인증 — POST /inspection (M14)
검사워커(vision 컨테이너) 전용 내부 엔드포인트. `require_internal` 가드:
- `AIVIS_SERVICE_TOKEN` **미설정(기본)**: 사내 단일 호스트 토폴로지(§4)에서 무인증 허용(화이트리스트).
- **설정 시**: `X-Service-Token: <token>` 헤더 또는 `Authorization: Bearer <token>` 가 일치해야 허용, 불일치 시 401.
- 테스트는 `core.security.get_settings` 의존성 오버라이드(monkeypatch)로 토큰 유무를 전환한다.

### 연속 NG 알람 (M6)
`POST /inspection` 처리 시 `cam_id` 단위 **연속 NG 카운터**(`ws/alarm.py`, 인메모리)를 유지한다.
- 매 NG 마다 단일 알람 `{event:"alarm", data:{kind:"ng", id, lot, cam_id, defect_codes}}` 발행.
- 연속 NG 가 임계(`AIVIS_CONSEC_NG_THRESHOLD`, 기본 3) **이상**이면 추가 알람
  `{event:"alarm", data:{kind:"consecutive_ng", cam_id, count, threshold}}` 발행.
- OK 수신 시 해당 cam 카운터 0 리셋.

## 기준정보 (§5 M13)
| 메서드 | 경로 | 권한 | 설명 |
|---|---|---|---|
| GET | `/master/items` | operator+ | 목록 |
| GET | `/master/items/{code}` | operator+ | 단건 |
| POST | `/master/items` | quality+ | 등록(version=1) |
| PUT | `/master/items/{code}` | quality+ | 부분 갱신(version +1, updated_by/at 기록) |
| GET | `/master/active?cam_id=` | operator+ | 현재 오더. `cam_id` 를 주면 **그 스테이션의 모드**(station_config)가 전역보다 우선 적용된 값을 돌려주고 `stage_source`(`station`\|`order`\|null)로 출처를 말한다. 워커는 자기 `AIVIS_CAM_ID` 로 폴링한다 |
| PUT | `/master/active/stage` | **operator+** | **검사 모드만** 바꾼다(`{item_code, inspection_stage, cam_id?}`; CUT_LENGTH\|POST_WASH_SURFACE\|CRATE_COUNT). `cam_id` 를 주면 **그 스테이션만**(station_config), 비우면 전역(active_order). 오더가 없으면 품목으로 만든다. 감사 로그 `[station=X]`/`[global]` before→after. 워커 15s 폴링으로 재시작 없이 전환. `PUT /master/active` 도 `inspection_stage` 를 받는다(NULL=스테이션 env 기본) |
| GET | `/master/stations` | operator+ | 스테이션별 설정 목록(`{cam_id, inspection_stage, updated_by, updated_at}`) — 2대 구성 모니터/HMI 용 |
| GET | `/master/stations/{cam_id}` | operator+ | 한 스테이션의 설정. 미설정이면 200 + null |
| DELETE | `/master/stations/{cam_id}` | **operator+** | 스테이션 모드 해제 → 다시 전역/env 를 따른다(멱등 204, 감사 로그) |
| PUT | `/master/items/{code}/spec` | **operator+** | **오더 교체용**: `ref_length_mm`/`tol_plus_mm`/`tol_minus_mm`/`expected_count` 만 수정(version +1, 감사 로그에 before→after). px_to_mm_scale·표면 임계값은 **전송 불가**. 값이 그대로면 version 을 올리지 않음. 최소 권한은 `AIVIS_SPEC_EDIT_MIN_ROLE`(기본 `operator`) |
| POST | `/master/items/{code}/calibrate` | quality+ | 웹 자기보정: px_to_mm_scale ×= actual_mm/measured_mm (version +1) |
| DELETE | `/master/items/{code}` | admin | 삭제 |

## KPI (§5 M12, §1.1 — 산출식 그대로)
| 메서드 | 경로 | 권한 | 설명 |
|---|---|---|---|
| GET | `/kpi/summary?period=YYYY-MM` | operator+ | 월별 자동 산출 → `KpiSummary`. 2026-10-10 추가 필드: `pending_review_count`(재확인 대기), `audited_count`(사람이 재확인한 표본), `misjudge_rate_pct`/`miss_rate_pct`, `mes_mode`(table\|rest\|rest_fake\|rest_unconfigured), `mes_consumed_count`(MES 수신 확인), `claim_count_ytd`(Claim 연 누계) |
| GET | `/kpi/targets` | operator+ | 목표표(리포트·화면 단일 출처). 행마다 `baseline_value`(구축 전), `profile`(plan\|contract). 수기 KPI 목표(`claim_count_ytd`·`lead_time_days`·`workload_index`) 포함 |
| POST | `/kpi/manual` | quality+ | 작업공수/리드타임/Claim upsert |
| GET | `/kpi/report?period=&fmt=pdf\|xlsx` | quality+ | 월간 리포트 **파일** 생성(PDF=reportlab / XLSX=openpyxl). `attachment` 다운로드. `period` 미지정 시 당월 |

### 월간 품질 리포트 내용 (M12, GET /kpi/report)
- §1.1 KPI: 공정불량률(ppm) / 검사불량률(%) / 자동검사율(%) / 저장·MES 연계율(%) / 평균 처리속도(ms) / 총·불량 수량.
- 2026-10-10 점검 보완: 오검·미검 **분리**(건수·비율), 재확인 대기·재확인 표본 수, MES 연계 방식·수신 확인 건수,
  Claim 연 누계·리드타임·작업공수 지수, 목표표에 **구축 전** 열과 **목표 기준**(plan=사업계획서·개발지침 /
  contract=협약 성과지표). 가짜 MES 전송(`rest_fake`)으로 센 연계율은 "가짜 전송 — 증빙 아님" 으로 적고 판정보류.
- 불량유형별 집계(`defect_codes` 배열 카운트, §7.2 코드).
- 일자별 검사수/불량수 표.
- 응답: `Content-Type` = `application/pdf` 또는 xlsx MIME, `Content-Disposition: attachment; filename="aivis_kpi_YYYY-MM.{ext}"`.
- 한글 라벨 기본(PDF 는 reportlab 내장 CID 한글폰트 `HYSMyeongJo-Medium`). 폰트 미가용 시 라틴 라벨 + 코드 폴백.
- 산출 경로: `kpi.py::_compute_summary` 를 summary 엔드포인트와 공유(산출식 일관성). 렌더러는 `core/report.py`.

산출식(§1.1):
- 공정불량률(ppm) = (final_verdict=NG 수 ÷ 총 검사수) × 1,000,000
- 검사불량률(%) = (오검 + 미검) ÷ 총 검사수 × 100
  - 오검(과검출) = final_verdict=NG AND manual_verdict=OK
  - 미검(놓친 불량) = final_verdict=OK AND manual_verdict=NG — AI 가 OK 라 한 것도 일부 재확인(표본 감사)해야 알 수 있다
  - 재확인 대기 = review_flag=true AND manual_verdict 미입력 — **판정 오류가 아니므로 검사불량률에 넣지 않는다**
  - (2026-10-10 정의 수정: 종전에는 재확인 대기를 미검으로, AI OK→사람 NG 를 오검으로 셌다)
- 개수 확인(CRATE_COUNT) 행은 제품 지표에서 제외(크레이트 1판), 저장·연계율에는 포함
- 자동검사율(%) = final_verdict 존재 수 ÷ 총 검사대상 × 100
- 저장&MES 연계율(%) = mes_synced 수 ÷ 전체 검사 × 100
- avg_proc_time_ms = 평균 처리속도(목표 ≤ 300ms/ea). 행의 `proc_time_ms` 는 **이미지 취득 시작 ~ 원본·결과 이미지 저장 완료**
  (§1.2 정의, 2026-10-10 — 종전엔 판정 구간만). 다발(한 장 N개)은 1개당 = 프레임 전체 ÷ N. 단계별 분해는 하트비트 `timings`
- 목표값: `AIVIS_KPI_PROFILE=plan`(기본, CLAUDE.md §1.1: 600ppm·30%·Claim 2·리드타임 5일·공수 50) | `contract`(10/10 점검 보고서 기재 협약값:
  검사불량률 40%·Claim 3·리드타임 6일 — **협약서 원문과 대조 후 사용**). 개별 `AIVIS_KPI_TARGET_*` 가 그 위에 덮인다.
  구축 전 기준값 `AIVIS_KPI_BASELINE_PROCESS_PPM/_LEAK_PPM/_CLAIM/_LEAD_DAYS/_WORKLOAD`(기본 2000/2000/5/7/100)

## 로그 / MES / 실시간
| 메서드 | 경로 | 권한 | 설명 |
|---|---|---|---|
| GET | `/logs?category=&limit=&offset=` | quality+ | 로그 조회(inspect/db/mes/error/user) |
| POST | `/mes/quality` | 내부 | REST 모드 MES 연계 수신(멱등키 중복 방지) |
| POST | `/inspection/status` | 내부 | 워커 하트비트. `stage`(현재 검사 모드), 선택 `timings{grab_ms, infer_ms, save_ms, total_ms, per_ea_ms, n, post_ms}`(단계별 처리시간), `waiting`(센서 트리거 대기 — 제품 없음, 촬영 안 함), 선택 `host{…}`(그 파이의 온도·CPU·메모리·디스크·전원 — `/system/stations` 로 나간다) 포함 — HMI 헤더가 첫 결과 전에도 모드를 표시. **카메라별로** 기록되어 `/system/status` 의 `services.workers[]`(`{cam_id, state, last_seen_s, stage}`)에 스테이션마다 한 줄씩 나온다(단일 `services.worker` 는 가장 최근 1대 기준 — 2대 이상이면 `workers` 를 볼 것) |
| GET | `/inspection/stats?item=&from=&to=&cam_id=&stage=` | operator+ | 통계 **서버 집계**(2026-10-10): `{total, ng, by_code[{code,count}], monthly[{month(KST), total, ng, defect_rate_pct}]}`. 대시보드 통계 화면용 — 종전 행 5,000건 요청이 목록 상한 2,000건을 넘어 실패하던 버그 수정 |
| GET | `/inspection/lot-summary?lot=&require=` | operator+ | **LOT 종합 판정**(2026-10-10): 모드(스테이션)별 결과를 LOT 단위로 합친다. `final_verdict` = NG(어느 모드든 NG 1건↑) \| INCOMPLETE(NG 없음 + 거쳐야 할 모드 결과 없음) \| OK \| NONE. `require` 미지정 시 스테이션 설정의 모드 전부. `reasons` 에 모드별 수치("길이 검사 NG 1개 / 20개 (LEN 1)") |
| GET | `/inspection?…&cam_id=&stage=` | operator+ | 이력 조회 필터에 스테이션(`cam_id`)·검사 모드(`stage`, 대소문자 무관) 추가. KPI `/kpi/summary` 는 `CRATE_COUNT` 행(크레이트 1판 = 제품 아님)을 공정불량률·검사수량에서 **제외**하고 저장·연계율에는 포함한다 |
| WS | `/ws/live?token=<JWT>` | 로그인 | 검사결과/알람 실시간 푸시. `token` 쿼리에 JWT 필요(무효/누락 시 accept 전 `1008` close). 이벤트 봉투 `{event, data}` (event=inspection\|alarm; alarm.data.kind = ng\|consecutive_ng) |
| GET | `/health` | 공개 | 헬스체크(DB 연결 확인) |
| GET | `/system/status` | operator+ | 현장 상태 스냅샷(API 가 도는 호스트 자원·워커·검사 집계·오류). "오늘" 은 **KST 0시** 기준(2026-10-09, 종전 UTC 0시 = 한국 09시) |
| GET | `/system/stations` | operator+ | **실시간 현황**(파이 여러 대, 2026-10-09). `{ts, stations[]}`, 스테이션 = 하트비트 ∪ station_config ∪ 최근 24h 결과의 cam_id(재기동 직후에도 죽은 파이가 `down` 으로 남는다). 각 항목: `cam_id, state(up\|stale\|down), last_seen_s, stage`(하트비트 > 스테이션 설정 > 마지막 결과), 마지막 사이클 `item_code/expected/detected/mismatch/error/proc_time_ms`, 그 파이 자신의 `host{cpu_temp_c, cpu_percent, load_1m, mem_percent, disk_percent, disk_free_gb, throttled}`(구 워커는 null), `last_hour`/`today{total, ng, ng_rate_pct}`, `latest{id, inspected_at, lot, item_code, inspection_stage, final_verdict, defect_codes, meas_length_mm, deviation_mm, length_verdict, oil/discolor/scratch_score, review_flag, has_result_image, has_raw_image, frame_total, frame_ng, limits{ref_length_mm, tol_plus/minus_mm, oil/discolor/scratch_threshold, expected_count}}`(다발이면 NG 튜브가 대표). DB 가 죽어도 200(하트비트만으로 채움) |
| PUT | `/inspection/images/{key}` | 내부 | **워커 → 허브 사진 업로드**(2026-10-09, `AIVIS_STORAGE_BACKEND=api` 워커). 본문 = JPEG 바이트(FF D8 시작), 키 = `raw\|result\|review/<파일명>.jpg`(하위 폴더·`..`·다른 확장자 400), 25MB 상한(413). 허브 `AIVIS_IMAGES_DIR` 의 같은 경로에 임시파일→rename 으로 저장(같은 키 재전송은 덮어씀 = 멱등). 디스크 오류 507(워커가 스풀에 보존 후 재전송). 서버가 `supabase` 백엔드면 409. 응답 201 `{key, bytes}` |

## 배포 환경변수 (클라우드 데모)
- `ALLOWED_ORIGINS`: CORS 허용 출처 콤마 목록(예 `https://aivis-hmi.vercel.app,https://aivis-dashboard.vercel.app`). 미설정 시 `*`(모든 출처, credentials 불가). 명시 목록이면 `allow_credentials=True`.
- `AIVIS_SEED_DEMO_ITEM`(기본 `false`): 데모 배포에서 `true`로 켜면 `item_master`에 데모 품목 1건을 멱등 시드(워커 검사결과 FK 충족).
- `AIVIS_DEMO_ITEM_CODE`(기본 `HP12`): 데모 시드 품목코드.
- 이미지 스토리지 백엔드: `AIVIS_STORAGE_BACKEND`(기본 `local`=공유 볼륨 FileResponse, `supabase`=Supabase Storage 오브젝트를 JWT 가드 뒤에서 프록시). 워커 쪽에는 `api` 값이 하나 더 있다 — 파이 여러 대 허브 구성에서 2호기 이상이 `PUT /inspection/images/{key}` 로 허브에 올린다(허브 API 는 `local`). `supabase` 모드는 `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_STORAGE_BUCKET`(기본 `inspection-images`)을 사용하며, DB 상대경로(raw/...\|result/...)를 오브젝트 키로 서빙한다.

## 공용 스키마 (packages/shared-types)
`InspectionResult`, `ItemMaster(+Create/Update)`, `ReviewUpdate`, `InspectionImages`,
`LengthResult`, `SurfaceResult`, `VerdictResult`(비전 파이프라인),
`KpiSummary`, `KpiManual`, `UserCreate/Public`, `LoginRequest`, `TokenResponse`, `SysLog`,
Enum: `DefectCode`, `Verdict`, `Role`, `LogCategory`, `CameraView`.
Python(`aivis_types`)과 TS(`ts/src/index.ts`)의 필드명/타입은 1:1 일치(자동 검증).
