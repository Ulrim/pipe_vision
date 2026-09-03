# 전남 AX 오픈플랫폼 데이터포털 연계 가이드 (JNTP_DATA_PORTAL.md)

> 협약서 제16조(데이터의 수집·활용)에 따라 AIVIS 가 생성하는 원시/가공/AI분석 데이터셋을
> 전남테크노파크 데이터포털에 제출하는 절차와 일정. 데이터셋 정의(명세)는
> [`DATA_DEFINITION.md`](./DATA_DEFINITION.md), 구현은 `services/data-ops/portal/` 과 `scripts/jntp/`.
>
> 소유: data-mes(연계 모듈) + devops(현장 설치·예약). 업로드 코드는 비밀값이며 저장소에 커밋하지 않는다.

---

## 1. 개요

| 항목 | 내용 |
|---|---|
| 요청 | 전남테크노파크 → AI솔루션 데이터 정의서 작성 + API 연동 가능 일정 회신(2026-09-18 16:00 까지) |
| 포털 | 전라남도 AX 오픈플랫폼 데이터포털, 업로드 API `POST {API_BASE}/dataset-uploads` (`X-Dataset-Code` 헤더) |
| 제공 자료 | 업로드 매뉴얼 v1.0, 업로드 안내(2차), `upload.sh`(스크립트 방식), 업로드 유형별 설정 파일 3종(`jntp.conf`, 코드 3개) |
| 방식 | **A안 스크립트**: 전남TP `upload.sh` 로 폴더 전송(정기 cron) / **B안 API 직접 연계**: AIVIS 연동 모듈이 API 호출. 두 방식은 같은 인터페이스라 같은 데이터셋에 누적된다 |
| AIVIS 대응 | `python -m portal.cli` 가 검사결과 DB·이미지에서 **포털 규격 폴더 3종을 생성(export)** 하고, A안(`scripts/jntp/aivis-portal-sync.sh`) 또는 B안(`portal.cli run`)으로 전송한다 |

### 1.1 데이터셋 ↔ 업로드 코드 매핑

| 데이터셋(루트) | 내용 | 포털 업로드 유형 | 설정 파일(현장 `~/jntp/`) | 환경변수 |
|---|---|---|---|---|
| `raw/` | 검사 원본 이미지, 학습 촬영본, 캘리브레이션, 촬영 메타 인덱스 | 원시 데이터 | `jntp-raw.conf` | `JNTP_CONF_RAW` |
| `processed/` | 결함 라벨·정답셋 매니페스트, 작업자 재확인 라벨, 기준정보 스냅샷 | 가공 데이터 | `jntp-processed.conf` | `JNTP_CONF_PROCESSED` |
| `ai-analysis/` | 판정 결과 레코드, 결과 오버레이 이미지, 월간 KPI, FAT/SAT/MSA 리포트 | AI 모델 | `jntp-ai-model.conf` | `JNTP_CONF_AI_MODEL` |

> ⚠️ 전달받은 설정 파일 3개가 모두 `jntp.conf` 라는 같은 이름이라 **어느 코드가 어느 유형인지 파일만으로는 알 수 없다.**
> 원본 메일의 첨부 순서/폴더명으로 확인하거나 전남TP 담당 부서에 확인한 뒤, 각각 `jntp-raw.conf` /
> `jntp-processed.conf` / `jntp-ai-model.conf` 로 이름을 바꿔 보관한다. 잘못 매핑하면 다른 데이터셋에 누적되며
> 포털에서 삭제는 담당자 요청으로만 가능하다(매뉴얼 FAQ).

---

## 2. 제출 폴더 레이아웃 (요약)

`portal.cli export|run` 이 `runs/<회차ID>/<데이터셋>/` 아래에 아래 구조로 생성한다. 필드 명세는
`DATA_DEFINITION.md` 3-3 / 4-3 / 5-3 과 1:1 이며 단일 진실원은 `services/data-ops/portal/layout.py`
(`python -m portal.cli schema` 로 출력).

```
/data/portal_export/                       ← AIVIS_PORTAL_EXPORT_DIR
├── state.json                             ← 데이터셋별 워터마크(last_until), 최근 회차 이력
└── runs/20260903T021700Z/                 ← 회차(UTC 시각). 전송 성공 시 삭제, 실패 시 보존→재시도
    ├── raw/
    │   ├── inspection/YYYY/MM/DD/{LOT}_{품목}_{YYYYMMDDHHmmssSSS}_{OK|NG}.jpg
    │   ├── capture/{CLASS}/{품목}_{END|SIDE}_{CLASS}_{YYYYMMDD-HHmmss}_{seq}.jpg   (--include-capture)
    │   ├── calib/*.jpg                                                            (--include-calib)
    │   └── index/raw_images_{회차ID}.jsonl
    ├── processed/
    │   ├── labels/{CLASS}/{stem}.json      groundtruth/gt_manifest.json
    │   ├── review/review_labels.jsonl      master/item_master.json
    └── ai-analysis/
        ├── inspections/YYYY/MM/inspections_{YYYYMMDD}_{회차ID}.jsonl
        ├── result/YYYY/MM/DD/*.jpg   kpi/kpi_{YYYY-MM}.json   reports/*.json|*.md
```

- **증분**: `inspection/`·`index/`·`inspections/`·`result/` 는 검사 시각 `(last_until, until]` 창만 내보낸다.
- **스냅샷**: 라벨·정답셋·재확인 라벨·기준정보·KPI·리포트는 매 회차 전량 재생성한다. 포털은 같은 경로
  재전송 시 최신 내용으로 갱신하므로 중복 등록이 없다(매뉴얼 §5).
- **개인정보 제외**: `operator`, `inspector`, `updated_by` 는 생성 단계에서 제외된다(`layout.EXCLUDED_PERSONAL_FIELDS`).
- **회차 접미사**: 같은 날 두 번 실행해도 인덱스/레코드 파일이 덮어써지지 않도록 파일명에 회차 ID 를 붙인다.

---

## 3. 현장 설치 (공통)

연동 모듈은 backend 의 DB 모델을 import 하므로 `services/api` 가 `PYTHONPATH` 에 있어야 한다.

```bash
# 1) 파이썬 환경(현장 PC 호스트, 1회)
cd /opt/aivis                      # 저장소 위치 예시
python3 -m venv .venv-dataops
.venv-dataops/bin/pip install -r services/data-ops/requirements.txt \
    -r services/api/requirements.txt -e packages/shared-types/python

# 2) 데이터 소스 환경변수 (docker compose 단일 호스트 기준)
export DATABASE_URL="postgresql+psycopg://aivis:<PW>@localhost:5432/aivis"   # .env 값과 동일
export AIVIS_IMAGES_DIR="$(docker volume inspect pipe_vision_images -f '{{.Mountpoint}}')"  # 공유 images 볼륨
export AIVIS_DATASET_DIR=/path/to/dataset       # 부록 A.4 학습 촬영본(raw/ calib/), 없으면 생략
export AIVIS_REPORTS_DIR=/opt/aivis/tests/fat/report   # FAT/SAT/MSA 리포트(선택)
export AIVIS_PORTAL_EXPORT_DIR=/data/portal_export

# (라즈베리파이 독립형 모드 B 는) DATABASE_URL=sqlite:////var/lib/aivis/db/aivis.db, AIVIS_IMAGES_DIR=/var/lib/aivis/images

# 3) 명세 확인 / 내보내기 시험(전송 없음)
cd services/data-ops && PYTHONPATH=../api ../../.venv-dataops/bin/python -m portal.cli schema
PYTHONPATH=../api ../../.venv-dataops/bin/python -m portal.cli export --dataset all --out /data/portal_export
```

### 3.1 설정 파일(업로드 코드) 보관 — 매뉴얼 §2.1·§2.2

```bash
mkdir -p ~/jntp
cp scripts/jntp/upload.sh ~/jntp/upload.sh          # 전남TP 제공 원본 그대로(저장소에 vendoring)
cp <전달받은 원시용>.conf      ~/jntp/jntp-raw.conf
cp <전달받은 가공용>.conf      ~/jntp/jntp-processed.conf
cp <전달받은 AI모델용>.conf    ~/jntp/jntp-ai-model.conf
chmod 700 ~/jntp/upload.sh && chmod 600 ~/jntp/jntp-*.conf
```

- 설정 파일 형식은 `scripts/jntp/jntp-*.conf.example` 참조(`JNTP_UPLOAD_CODE`, `JNTP_API_BASE`). 내용은 수정하지 않는다.
- `.gitignore` 가 `scripts/jntp/*.conf`, `jntp*.conf` 를 제외한다. 코드가 유출된 것 같으면 즉시 담당자에게 알려 재발급받고
  파일의 코드만 교체한다(매뉴얼 FAQ).

---

## 4. 방식 A — 스크립트(전남TP `upload.sh`) + 정기 실행

`scripts/jntp/aivis-portal-sync.sh` 가 ① 증분 내보내기(`portal.cli run --no-upload`) → ② 대기 중인 모든 회차 폴더를
데이터셋별 설정 파일로 `upload.sh` 전송 → ③ 성공 폴더 삭제(실패는 보존해 다음 실행에서 재시도) 를 수행한다.

```bash
# 수동 실행
JNTP_DIR=~/jntp AIVIS_PYTHON=/opt/aivis/.venv-dataops/bin/python bash scripts/jntp/aivis-portal-sync.sh

# 학습 촬영본·캘리브레이션 일괄 제출(규격 촬영 완료 후 1회)
bash scripts/jntp/aivis-portal-sync.sh --include-capture --include-calib
```

cron(매일 02:17 KST, 매뉴얼 §3 과 같은 시각. 환경변수는 crontab 상단 또는 래퍼 스크립트에서 지정):

```cron
SHELL=/bin/bash
CRON_TZ=Asia/Seoul
DATABASE_URL=postgresql+psycopg://aivis:<PW>@localhost:5432/aivis
AIVIS_IMAGES_DIR=/var/lib/docker/volumes/pipe_vision_images/_data
AIVIS_PYTHON=/opt/aivis/.venv-dataops/bin/python
17 2 * * * /opt/aivis/scripts/jntp/aivis-portal-sync.sh >> $HOME/jntp/aivis-portal-sync.log 2>&1
```

- `upload.sh` 는 300 파일 단위로 나눠 보내고 실패 시 exit 1 로 멈춘다. 래퍼는 그 회차 폴더를 보존하므로 다음 실행에서
  자동으로 다시 보낸다(같은 경로 재전송 = 갱신, 중복 없음).
- `upload.sh` 는 서버 거절/특수문자 파일명 제외가 1건이라도 있으면 exit 1 이므로, 그 회차 폴더가 계속 남을 수 있다.
  로그의 `제외(서버)` 사유를 확인해 해당 파일을 지우면 다음 실행에서 정리된다. (B안은 거절을 경고로만 보고하고 정리한다.)
- Windows 산업용 PC 는 WSL2 + 작업 스케줄러(매뉴얼 §3.1)로 같은 명령을 등록한다.

---

## 5. 방식 B — API 직접 연계 (`portal.cli run`)

AIVIS 내장 클라이언트(`portal/upload.py`)가 매뉴얼 §4 규격으로 직접 전송한다: `X-Dataset-Code`·`X-Upload-Run` 헤더,
multipart `files` 반복(`filename=상대경로`), 300 파일/5 GiB 배치, 500 MiB·확장자·경로 규칙 사전 검사, 5xx·연결 오류
재시도(기본 3회·30초), 401 은 즉시 중단(코드 확인).

```bash
cd services/data-ops
export JNTP_CONF_RAW=~/jntp/jntp-raw.conf JNTP_CONF_PROCESSED=~/jntp/jntp-processed.conf JNTP_CONF_AI_MODEL=~/jntp/jntp-ai-model.conf

# 회차 실행: 대기분 재전송 → 증분 내보내기 → 업로드 → 성공분 정리 → 워터마크 저장
PYTHONPATH=../api python -m portal.cli run --out /data/portal_export
# 전송 없이 배치 계획만 점검(가짜 전송)
PYTHONPATH=../api python -m portal.cli run --out /data/portal_export --dry-run --keep
# 특정 폴더 수동 업로드
PYTHONPATH=../api python -m portal.cli upload --dataset raw --dir /data/portal_export/runs/<회차>/raw --conf ~/jntp/jntp-raw.conf
# 현황(워터마크·대기 회차·최근 실행)
PYTHONPATH=../api python -m portal.cli status --out /data/portal_export
```

cron 은 A안과 같은 시각에 `run` 을 등록한다. 서버 응답 201 의 `acceptedCount`/`rejected`/`version` 을 회차 이력
(`state.json`)에 남긴다.

### 5.1 상태 파일과 재시도 규칙

| 상황 | 동작 |
|---|---|
| 내보내기 성공 | `state.json.last_until[데이터셋]` 을 `until` 로 전진(파일은 `runs/` 에 보존) |
| 업로드 성공(모든 묶음 201) | 회차 폴더 삭제(`--keep` 이면 보존) |
| 업로드 실패(5xx/연결/401) | 폴더 보존, 종료코드 1. 다음 `run` 이 대기분부터 재전송 |
| 서버 거절 파일(`rejected`) | 경고로 보고하고 정리(재전송해도 같은 결과이므로). 사유를 확인해 원인 수정 |
| 설정 파일 없음 | 실행하지 않음(종료코드 2) — 오전송·코드 유출 방지 |

---

## 6. 포털 전송 규칙 요약 (매뉴얼 §5·§6)

| 구분 | 기준 | AIVIS 대응 |
|---|---|---|
| 파일 크기 | 파일당 최대 500 MiB | 이미지 1장 ≤ 4 MB, JSONL 은 일 단위 분할 |
| 요청 총량 | 한 요청 최대 5 GiB | 300 파일 배치 + 총량 상한 동시 적용 |
| 파일 경로 | 상대 경로, `/` 구분, `.`/`..`/역슬래시 금지 | 데이터셋 루트 기준 상대경로만 생성, 사전 검사 |
| 중복 | 같은 요청 내 중복 경로는 첫 파일만, 이후 요청 재전송은 갱신 | 스냅샷 파일은 재전송=갱신에 의존, 증분 파일은 회차 접미사 |
| 포맷 검사 | 일부 이진 형식은 확장자·헤더 일치 확인 | JPEG/PNG 는 원본 그대로(재인코딩 없음) |
| 지원 확장자 | csv xlsx json jsonl jpg png md pdf 등 | jpg / jsonl / json / md 만 사용 |
| 오류 | 400 files 없음 / 401 코드 오류 / 503 스토리지 / 500 | 401 즉시 중단, 5xx 재시도, 응답 본문·시각을 로그로 보관 |

---

## 7. 개인정보·보안 정책

- 이미지는 제품(튜브)만 촬영하는 구도이며 작업자 신체·얼굴이 프레임에 들어오지 않도록 차광 후드·구도를 유지한다.
- 레코드에서 작업자(`operator`), 검수자(`inspector`), 수정자(`updated_by`) 필드를 제외한다 → 정의서 "개인정보 미포함 / 비식별화 해당없음".
- LOT·작업지시·생산 수량은 수요기업 생산정보다. 데이터 공개 여부·라이선스는 양 기업 합의로 정하고 정의서에 기재한다.
- 업로드 코드는 `~/jntp/jntp-*.conf`(권한 600)에만 두고, 로그·예외 메시지·저장소에 싣지 않는다.

---

## 8. API 연동 가능 일정 (회신용)

기준일 2026-09-03. 연동 모듈은 구현·단위 테스트(31건)·모의 포털 서버 종단 시험을 마쳤고, 실 포털 시험 전송은
업로드 코드 ↔ 유형 매핑 확인 후 바로 가능하다. 실생산 데이터의 정기 전송은 현장 장비 설치·광학 셋업 완료 시점에 종속된다.

| 단계 | 내용 | 일정 | 비고 |
|---|---|---|---|
| 1 | 데이터 정의서 초안 제출, 분류 코드·공개 여부·라이선스 협의 | ~ 2026-09-18 | 본 회신 |
| 2 | 연동 모듈 개발 완료(내보내기·A안 래퍼·B안 API 클라이언트) | 2026-09-03 완료 | 모의 서버 종단 시험 통과 |
| 3 | 포털 시험 전송 — 시뮬레이터(합성) 데이터 소량으로 3개 데이터셋 각 1회, 등록·거절 응답 확인 | 2026-09-19 ~ 09-26 | 코드 매핑 확인 직후 착수 |
| 4 | 현장 검사 PC 설치·정기 전송 예약(매일 02:17), 전송 로그 점검 | 2026-09-29 ~ 09-30 | A안/B안 중 현장 환경으로 확정 |
| 5 | 실생산 데이터 정기 전송 개시(원시·AI분석 증분, 가공 스냅샷) | 2026-10-01 ~ 【확인】 | 장비 설치·캘리브레이션 완료 익일부터 |
| 6 | 학습 촬영본·라벨·캘리브레이션 일괄 제출 | 2026-10 중 【확인】 | 규격 재촬영·검수 완료 후 |
| 7 | 월간 KPI·FAT/SAT/MSA 리포트 갱신 제출 | 매월 초 / 검증 완료 시 | 자동 재산출 |

회신 요지: ① 정의서(원시/가공/AI분석 3종) 첨부, ② API 연동은 **2026-09-26 까지 시험 전송, 09-30 정기 전송 예약 완료**,
실데이터 전송은 10월 장비 설치 완료 즉시 개시, ③ 확인 요청 — 산업분류·AI기술분류 코드, 업로드 코드 3종의 유형 매핑,
공개 여부·라이선스.
