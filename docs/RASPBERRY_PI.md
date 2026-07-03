# AIVIS 라즈베리파이 엣지 모듈 설치·운영 가이드

최종 검사 모듈이 **Raspberry Pi 4 (4GB) + Raspberry Pi Camera Module 3 (IMX708)** 로
확정됨에 따라, Pi 를 검사 스테이션의 엣지 노드로 운영하는 따라하기 가이드다.

핵심 설계는 그대로다(CLAUDE.md §6.1 HAL): vision-ai 가 `PiCameraAdapter`(picamera2 기반,
`AIVIS_CAMERA=picam`)를 추가하고, 워커는 `create_camera()` 팩토리로 카메라를 만들어
`item_master.capture_recipe` 로 `configure()` 한다. 따라서 **환경변수 `AIVIS_CAMERA=picam`
하나만으로 워커가 Pi 카메라로 동작하며 애플리케이션 소스는 바뀌지 않는다**(sim ↔ picam ↔
genicam 교체는 HAL 경계 뒤에서 투명).

> 소유: devops. 이 문서와 `deploy/aivis-vision-pi.service`, `deploy/aivis-worker.env.example`
> 은 인프라/배포 메타다. 애플리케이션 소스(`services/*`, `apps/*`, `packages/*`)는 변경하지
> 않는다.

---

## 0. 두 배포 모드 (먼저 결정)

| | (A) 엣지→클라우드 (권장) | (B) 독립형(오프라인) |
|---|---|---|
| Pi 가 구동 | 검사 워커만 | api(sqlite)+워커+정적 HMI 전부 |
| DB | Supabase(PostgreSQL) | sqlite (`aivis_dev.db` 자동) |
| 이미지 스토리지 | Supabase Storage (`AIVIS_STORAGE_BACKEND=supabase`) | 로컬 디스크 (`local`) |
| api/HMI/대시보드 | Render + Vercel (기구축 스택 재사용) | Pi 로컬(uvicorn + 정적 서빙) |
| 인터넷 | 필요(결과 POST/업로드) | 불필요(현장 폐쇄망 가능) |
| 적합 | 다지점·중앙 집계·기존 클라우드 자산 활용 | 망분리 현장·단독 라인·데모 |

두 모드 모두 **워커 실행 방식은 동일**하고, `deploy/aivis-worker.env.example` 의 값만
다르다. postgres/minio 컨테이너는 Pi 4 4GB 에서는 쓰지 않는다(경량 동작).

```
(A)  [Pi: worker(picam)]  ──POST/업로드──▶  Render(api) + Supabase(DB/Storage)  ◀── Vercel(HMI/대시보드)
(B)  [Pi 한 대]  worker(picam) ─▶ 로컬 api(uvicorn, sqlite) ─▶ 로컬 디스크 이미지 + 정적 HMI
```

---

## 1. 하드웨어 준비

- Raspberry Pi 4 Model B (4GB) + 정품 USB-C 전원(5V/3A). 발열 대비 방열판/팬 권장.
- Raspberry Pi Camera Module 3 (IMX708). 표준 화각 또는 Wide 중 촬영거리에 맞게 선택.
- CSI 리본 연결: Pi 의 **CAMERA** 커넥터 탭을 살짝 들고, 리본 **접점(금속)이 HDMI 반대쪽
  (보드 안쪽)을 향하도록** 삽입 후 탭을 눌러 고정. 카메라 보드 쪽도 동일 요령. 전원 끈
  상태에서 결선한다. (Pi 4 는 표준 15핀 리본, Camera Module 3 동봉 케이블 사용.)
- OS: **Raspberry Pi OS 64-bit (Bookworm)**. Raspberry Pi Imager 로 microSD(16GB+) 굽기.
  Imager 고급 옵션에서 SSH/사용자/Wi‑Fi 를 미리 설정하면 헤드리스 셋업이 편하다.

### 1.1 카메라 활성화 확인

Bookworm 은 **libcamera 스택이 기본**이라 별도 활성화가 대개 불필요하다. 다만 리본을 꽂고
부팅한 뒤 인식되는지 확인한다.

```bash
# Bookworm 기본 명령(rpicam-*), 구형 별칭(libcamera-*)도 대개 동작
rpicam-hello --list-cameras      # 또는: libcamera-hello --list-cameras
rpicam-hello -t 2000             # 2초 프리뷰(헤드리스면 --nopreview 로 감지만)
```

카메라가 목록에 없으면 `/boot/firmware/config.txt` 를 확인한다(Bookworm 은 `/boot/firmware/`,
구버전은 `/boot/`).

```bash
sudo nano /boot/firmware/config.txt
# 다음 줄이 있어야 자동 감지가 켜진다(기본값):
#   camera_auto_detect=1
# 특정 센서를 강제하려면(자동 감지 실패 시에만):
#   dtoverlay=imx708
sudo reboot
```

`camera_auto_detect=1` 이면 IMX708 은 오버레이 지정 없이 인식된다. 그래도 안 보이면 §8
트러블슈팅(리본 방향/삽입)을 확인한다.

---

## 2. 소프트웨어 설치

picamera2 와 그 의존(libcamera 바인딩)은 **시스템 패키지로만 제공**되며 pip 로 설치할 수 없다.
따라서 워커 venv 는 반드시 `--system-site-packages` 로 만들어 시스템 picamera2/cv2 를 상속한다.

```bash
# 1) 시스템 패키지(picamera2 · OpenCV · venv 도구)
sudo apt update
sudo apt install -y python3-picamera2 python3-opencv python3-venv

# 2) 저장소 배치(예: /opt/aivis). git 또는 오프라인 복사.
sudo mkdir -p /opt/aivis && sudo chown "$USER" /opt/aivis
# git clone <repo> /opt/aivis   또는   USB 로 복사

# 3) 워커 venv 를 --system-site-packages 로 생성(핵심!)
cd /opt/aivis/services/vision
python3 -m venv --system-site-packages .venv
. .venv/bin/activate

# 4) 시스템에 없는 순수 파이썬 의존만 pip 로
pip install --upgrade pip
pip install httpx numpy
pip install -e /opt/aivis/packages/shared-types/python     # 제공 패키지: aivis_types

# 5) (선택) 표면 ONNX 모델을 쓸 때만. aarch64 휠 자동 설치.
pip install onnxruntime
```

설치 검증:

```bash
# venv python 이 시스템 picamera2/cv2 를 상속하는지 확인
python -c "import picamera2, cv2, numpy, httpx, aivis_types; print('ok', cv2.__version__)"
```

`import picamera2` 가 실패하면 venv 를 `--system-site-packages` 로 다시 만든 것이 맞는지
확인한다(§8).

---

## 3. 실행 (수동 스모크)

환경변수 `AIVIS_CAMERA=picam` 만 주면 워커가 Pi 카메라로 동작한다. 워커는
`services/vision` 에서 모듈로 기동한다.

```bash
cd /opt/aivis/services/vision
. .venv/bin/activate
AIVIS_CAMERA=picam \
AIVIS_API_URL=https://aivis-api.onrender.com \
AIVIS_SERVICE_TOKEN=... \
AIVIS_ITEM_CODE=HP12 \
python -m worker
```

정상 기동 시 로그에 카메라 초기화 → 기준정보(item_master) 조회 → 트리거마다
캡처→추론→POST 흐름과 `proc_time_ms` 가 찍힌다. 상시 운영은 §6 systemd 로 등록한다.

---

## 4. 배포 모드 상세

### (A) 엣지→클라우드 (권장)

Pi 는 **워커만** 구동하고, 검사 결과를 기구축 클라우드 스택으로 보낸다. api/HMI/대시보드/
DB/Storage 를 그대로 재사용하므로 Pi 부담이 최소다.

1. 클라우드 스택이 이미 떠 있어야 한다(`deploy/DEPLOYMENT.md`: Supabase → Render → Vercel).
2. `/etc/aivis/worker.env` (아래 §5)에서 모드 A 블록을 사용:
   - `AIVIS_STORAGE_BACKEND=supabase` + `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` /
     `SUPABASE_STORAGE_BUCKET` — 워커가 이미지를 업로드, Render api 가 프록시 서빙.
   - `AIVIS_API_URL=https://<render-api>` (스킴 포함), `AIVIS_SERVICE_TOKEN=<api 와 동일>`.
3. 워커 하나만 기동(§6). DB/이미지/화면은 클라우드가 담당.

### (B) 독립형(오프라인)

Pi 한 대에서 **api(sqlite) + 워커 + 정적 HMI** 를 함께 돌린다. postgres/minio 불필요.

1. api 실행(같은 Pi, 별도 venv 권장):

   ```bash
   cd /opt/aivis/services/api
   python3 -m venv .venv-api && . .venv-api/bin/activate
   pip install -r requirements.txt
   pip install -e /opt/aivis/packages/shared-types/python
   # DATABASE_URL 미설정 → sqlite(./aivis_dev.db) 자동. 테이블도 자동 생성(sqlite 한정).
   AIVIS_STORAGE_BACKEND=local \
   AIVIS_IMAGES_DIR=/var/lib/aivis/images \
   uvicorn main:app --host 0.0.0.0 --port 8000
   ```

   > sqlite 모드에서는 Alembic 없이 앱 기동 시 테이블이 생성된다(운영 postgres 와 달리).
   > api 도 상시화하려면 별도 systemd 유닛으로 감싼다(위 ExecStart 를 uvicorn 으로).

2. 워커 실행: `/etc/aivis/worker.env` 에서 모드 B 블록 사용
   - `AIVIS_API_URL=http://127.0.0.1:8000`, `AIVIS_STORAGE_BACKEND=local`,
     `AIVIS_IMAGES_DIR=/var/lib/aivis/images` (**api 와 동일 경로여야** api 가 이미지를 읽는다),
     `AIVIS_SERVICE_TOKEN=` (단일호스트는 미설정/화이트리스트 가능).

3. HMI(정적): 빌드 산출물(`apps/hmi/dist`)을 로컬 웹서버로 서빙한다.

   ```bash
   # 인터넷 되는 머신에서 미리 빌드(VITE_API_BASE=http://<pi-ip>:8000 로) → dist 복사
   sudo apt install -y nginx        # 또는: python3 -m http.server 5173 -d apps/hmi/dist
   ```

`local` 백엔드에서는 `AIVIS_IMAGES_DIR` 하위에 `raw/`(원본) `result/`(판정 오버레이)
`review/`(오검·미검) 이 생성된다(§6.4).

---

## 5. 환경파일 (`/etc/aivis/worker.env`)

템플릿을 복사해 현장 값으로 채운다.

```bash
sudo install -D -m600 /opt/aivis/deploy/aivis-worker.env.example /etc/aivis/worker.env
sudo nano /etc/aivis/worker.env
```

주요 변수(전체·주석은 `deploy/aivis-worker.env.example`):

| 변수 | 용도 |
|---|---|
| `AIVIS_CAMERA=picam` | Pi 카메라 어댑터 선택(이 값만으로 동작) |
| `AIVIS_PICAM_SIZE` | 캡처 해상도 `가로x세로`(예 `2304x1296`) |
| `AIVIS_PICAM_SWAP_RB` | 색상 R/B 뒤바뀜 보정(`true`/`false`) |
| `AIVIS_ITEM_CODE` | 검사 품목(기준정보/촬영 레시피 FK) |
| `AIVIS_CAM_ID` | 카메라/스테이션 식별자 |
| `AIVIS_API_URL` | 결과 POST 대상 api(스킴 포함) |
| `AIVIS_SERVICE_TOKEN` | POST /inspection 내부 토큰(api 와 동일) |
| `AIVIS_STORAGE_BACKEND` | `supabase`(모드 A) / `local`(모드 B) |
| `AIVIS_IMAGES_DIR` | local 모드 이미지 경로(api 와 동일) |
| `AIVIS_WORKER_INTERVAL_MS` | 타이머 트리거 간격 |

---

## 6. systemd 서비스 (상시 운영·자동 재시작)

`deploy/aivis-vision-pi.service` 를 설치한다(경로/User 는 실제 배치에 맞게 확인).

```bash
sudo cp /opt/aivis/deploy/aivis-vision-pi.service /etc/systemd/system/aivis-vision.service
sudo systemctl daemon-reload
sudo systemctl enable --now aivis-vision.service

systemctl status aivis-vision            # 상태
journalctl -u aivis-vision -f            # 실시간 로그(proc_time_ms 등)
```

유닛은 `EnvironmentFile=/etc/aivis/worker.env`, `WorkingDirectory=.../services/vision`,
`ExecStart=.venv/bin/python -m worker`, `Restart=always`(크래시/카메라 일시장애 자동 복구),
`Group=video`(카메라 접근 권한)로 구성된다. 독립형(B)에서 api 도 상시화하려면 동일 방식으로
uvicorn 유닛을 하나 더 만든다.

---

## 7. 촬영 레시피 · 캘리브레이션 (길이 반복성의 핵심)

임계값/보정계수/촬영 파라미터는 하드코딩하지 않고 **`item_master.capture_recipe`(JSONB)** 에
저장한다(M1/M13). 워커가 품목 조회 시 이 레시피로 `PiCameraAdapter.configure()` 를 호출한다.

### 7.1 IMX708 촬영 레시피 예시

```json
{
  "exposure_us": 4000,
  "analogue_gain": 1.5,
  "af_mode": "manual",
  "lens_position": 4.2,
  "width": 2304,
  "height": 1296
}
```

- `exposure_us` / `analogue_gain`: 반사·포화를 피하도록 고정 노출. 자동노출 금지(프레임마다
  밝기가 흔들리면 표면 판정·엣지 검출이 불안정).
- `af_mode=manual` + `lens_position`(고정 초점): **반드시 수동 초점**. Continuous AF 는
  프레임마다 초점거리가 미세하게 변해 **픽셀↔mm 스케일이 흔들리고 길이 오차를 유발**한다.
  → **Manual AF + 고정 렌즈위치 + 고정 촬영거리(치구)** 3종을 반드시 함께 고정한다.
- `lens_position` 은 대략 1/거리(m) 단위(디옵터)로, 촬영거리에서 가장 선명한 값을 실측으로
  찾아 저장한다(§8 초점 흐림).

### 7.2 길이 캘리브레이션 (px→mm)

고정 초점·고정 거리 상태에서만 유효하다(초점/거리 바뀌면 재캘리브레이션).

1. 알려진 길이의 게이지/기준자를 제품과 **동일 평면**에 놓고 촬영(부록 A.3 스케일 기준자).
2. 게이지 양 끝 엣지의 픽셀 거리 측정 → `px_to_mm_scale = 실제_mm / 픽셀_거리`.
3. 그 값을 해당 품목의 `item_master.px_to_mm_scale` 로 저장(관리자/품질 권한, 변경 이력).
4. 이후 길이 판정: `length_mm = pixel_distance × px_to_mm_scale`, `|deviation| ≤ 공차` → OK.

> 해상도를 바꾸면(예 2304→4608) 픽셀 스케일이 달라지므로 `px_to_mm_scale` 와
> `AIVIS_PICAM_SIZE`/레시피 `width/height` 를 **세트로** 관리한다.

---

## 8. 성능 (Pi 4 CPU) 과 300ms 목표

Pi 4 는 GPU 가속이 없으므로 CPU 예산 안에서 해상도·경로를 튜닝한다.

- **해상도 트레이드오프**: 기본 `2304x1296`(2x2 binning, 빠름)을 우선 사용. 정밀 계측/미세
  결함이 필요할 때만 풀 `4608x2592`(부하↑). 대부분의 길이·표면 1차 판정은 2304 로 충분.
- **길이 경로(고전 CV)**: 서브픽셀 엣지 기반 계측은 Pi 4 에서 보통 **수십 ms** 로 300ms 예산
  안에 든다(§6.2). 길이는 안정적으로 실시간 가능.
- **표면 판정**: 데이터 부족 초기에는 **고전 CV 폴백 우선**(§6.3)으로 예산을 지킨다. 무거운
  ONNX(YOLOv8-seg 등)는 Pi CPU 에서 느릴 수 있으므로 **INT8 양자화** 또는 초기 생략,
  경량 분류만 온디바이스로 운용한다.
- **계측·튜닝**: 워커가 반환하는 `proc_time_ms`(검사행에 저장)로 p50/p95 를 보고, 초과 시
  해상도↓ / ROI 축소 / ONNX 경량화로 조정한다.
- **현실적 기대치**: 300ms/ea 는 **해상도·경로 튜닝을 전제로 달성 가능한 목표**다. 길이+
  경량 표면(고전 CV) 조합은 여유 있게 충족하고, 무거운 세그멘테이션을 온디바이스로 강행하면
  초과할 수 있다 → 그런 경우 모드 A 에서 무거운 모델은 클라우드로 분리하거나 양자화한다.

---

## 9. 트러블슈팅

| 증상 | 원인 / 조치 |
|---|---|
| `rpicam-hello` 에 카메라 없음 | CSI 리본 방향/삽입 불량(전원 끄고 재결선, 접점 방향 확인), `/boot/firmware/config.txt` 의 `camera_auto_detect=1` 확인, 필요시 `dtoverlay=imx708` 후 재부팅 |
| `ModuleNotFoundError: picamera2` | venv 가 `--system-site-packages` 없이 생성됨 → `python3 -m venv --system-site-packages .venv` 로 재생성. pip 로 picamera2 설치 시도 금지(시스템 패키지 전용) |
| 색상이 푸르스름/불그스름 | RGB↔BGR 채널 순서 → `AIVIS_PICAM_SWAP_RB=true` |
| 초점 흐림/길이 흔들림 | `af_mode=manual` + `lens_position` 재조정(선명한 값 실측), 촬영거리·치구 고정. Continuous AF 금지 |
| 노출 포화(하이라이트 날림) | `exposure_us`/`analogue_gain` 낮춤, 확산광 사용, 스크래치는 저각도 사광 |
| 처리시간 초과(>300ms) | `AIVIS_PICAM_SIZE` 낮춤(2304), 표면을 고전 CV 폴백/양자화, ROI 축소, `proc_time_ms` 로 재확인 |
| 발열/스로틀링(성능 저하) | 방열판+팬 부착, 정품 5V/3A 전원 사용. `vcgencmd measure_temp` / `vcgencmd get_throttled`(0x0 정상) 확인 |
| api 로 결과 미도달 | `AIVIS_API_URL` 스킴(http/https) 포함 여부, `AIVIS_SERVICE_TOKEN` 이 api 와 동일한지, 네트워크/방화벽 확인 |
| 이미지가 대시보드에 안 보임 | 모드 A: `SUPABASE_*` 키/버킷 확인. 모드 B: 워커와 api 의 `AIVIS_IMAGES_DIR` 가 동일 경로인지 확인 |

---

## 10. 레퍼런스

- picamera2 (GitHub): https://github.com/raspberrypi/picamera2
- picamera2 매뉴얼(PDF): https://datasheets.raspberrypi.com/camera/picamera2-manual.pdf
- Camera Module 3 문서: https://www.raspberrypi.com/documentation/accessories/camera.html
- 카메라 소프트웨어(rpicam/libcamera): https://www.raspberrypi.com/documentation/computers/camera_software.html
- AIVIS 클라우드 스택(모드 A 기반): `deploy/DEPLOYMENT.md`
- HAL/파이프라인 설계 근거: `CLAUDE.md` §6.1(HAL), §6.2(길이), §6.3(표면), §6.4(이미지 저장)
