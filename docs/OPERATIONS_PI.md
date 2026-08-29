# AIVIS 라즈베리파이 현장 운영 가이드 (현장 담당자용)

> 이 문서는 **리눅스를 몰라도** 파이에서 AIVIS 를 켜고, 보고, 업데이트할 수 있게
> 쓴 실무 안내서다. 명령은 그대로 복사해서 붙여넣으면 된다.
> 설치·카메라 세팅 등 기술 상세는 [`docs/RASPBERRY_PI.md`](RASPBERRY_PI.md),
> 서버/도커 운영은 [`docs/OPERATIONS.md`](OPERATIONS.md) 를 본다.

---

## 0. 한 장 요약 (이것만 외우면 됨)

| 하고 싶은 일 | 명령 |
|---|---|
| 뭐든 조작(메뉴) | `bash ~/pipe_vision/scripts/aivis.sh` |
| 프로그램 최신으로 | `bash ~/pipe_vision/scripts/aivis-update.sh` |
| 지금 상태 보기 | `bash ~/pipe_vision/scripts/aivis.sh status` |
| 실시간 모니터 | `bash ~/pipe_vision/scripts/aivis.sh monitor` |
| 사무실 PC 접속 주소 | `bash ~/pipe_vision/scripts/aivis.sh urls` |

> `~/pipe_vision` 은 저장소를 내려받은 위치다. `/opt/aivis` 등 다른 곳에 두었으면
> 그 경로로 바꿔 읽는다. 아래 예시는 모두 **저장소 폴더 안에서** 실행한다고 가정한다:
> ```bash
> cd ~/pipe_vision
> ```

---

## 1. 최초 설치 (한 번만)

### 1-1. 준비물
- 라즈베리파이 4 (4GB) + Raspberry Pi OS 64-bit(Bookworm)
- Camera Module 3 (IMX708), 7인치 LCD, 정품 전원어댑터(전원 부족은 오작동의 흔한 원인)
- 파이가 사내 네트워크(유선 권장)에 연결되어 있을 것

### 1-2. 소프트웨어 설치
`docs/RASPBERRY_PI.md` 의 **STEP 1~3** 을 순서대로 수행한다(시스템 패키지 → 저장소
배치 → 워커 venv 생성). 요약하면 다음과 같다.

```bash
# 1) 시스템 패키지
sudo apt update
sudo apt install -y git python3-venv python3-picamera2 python3-opencv

# 2) 저장소 내려받기 (예: 홈 폴더)
cd ~
git clone https://github.com/Ulrim/pipe_vision.git
cd pipe_vision

# 3) 검사 워커용 파이썬 환경 (카메라 라이브러리 상속이 핵심)
cd services/vision
python3 -m venv --system-site-packages .venv
.venv/bin/pip install httpx numpy
.venv/bin/pip install -e ~/pipe_vision/packages/shared-types/python
cd ~/pipe_vision
```

### 1-3. 첫 기동(수동)
```bash
bash scripts/aivis.sh start
```
처음 실행하면 API 용 파이썬 패키지를 자동으로 설치하므로 **수 분** 걸린다.
"시작 완료" 와 접속 주소가 나오면 성공이다.

> 화면(HMI/대시보드)이 "빌드 산출물 없음" 이라고 나오면 화면을 아직 안 만든 것이다:
> ```bash
> npm install
> npm run build          # HMI + 대시보드 둘 다 (파이에서 10분 이상 걸릴 수 있음)
> bash scripts/aivis.sh restart
> ```

---

## 2. 부팅 자동시작 등록 (권장)

파이 전원을 켜면 검사 시스템이 저절로 뜨게 한다. 한 번만 등록하면 된다.

```bash
sudo bash scripts/aivis-install-service.sh
```

- 현재 저장소 경로와 로그인 사용자를 자동으로 유닛에 반영한다.
- 등록 후에는 크래시가 나도 systemd 가 자동 재시작한다(`Restart=always`).
- 해제하려면:
  ```bash
  sudo bash scripts/aivis-install-service.sh --uninstall
  ```

> ⚠️ **주의**: `aivis-vision.service`(엣지→클라우드 모드, 워커만 실행)와
> `aivis-standalone.service`(이 문서의 독립형)를 **동시에 켜지 마라.**
> 같은 카메라를 두 프로세스가 열어 충돌하고 같은 제품이 두 번 적재된다.
> 설치 스크립트가 감지해 경고한다. 둘 중 하나만:
> ```bash
> sudo systemctl disable --now aivis-vision.service   # 독립형을 쓸 때
> ```

### 2-1. (선택) 7인치 LCD 키오스크 자동실행
파이 화면에 작업자 HMI 를 전체화면으로 띄우고 싶을 때:

```bash
sudo apt install -y chromium-browser unclutter
mkdir -p ~/.config/autostart
cat > ~/.config/autostart/aivis-kiosk.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=AIVIS HMI Kiosk
Exec=chromium-browser --kiosk --noerrdialogs --disable-infobars --incognito http://localhost:5173
X-GNOME-Autostart-enabled=true
EOF
```
데스크톱 로그인 시 자동으로 전체화면 HMI 가 뜬다(종료: `Alt+F4`).
화면이 꺼지지 않게 하려면 `Preferences > Screen Blanking` 을 끈다.

---

## 3. 일상 조작 — 메뉴 하나로

```bash
bash scripts/aivis.sh
```
```
   1) 시스템 시작          2) 시스템 중지
   3) 재시작               4) 상태 보기
   5) 실시간 모니터        6) 프로그램 업데이트
   7) 로그 보기            8) 접속 주소 표시
   0) 종료
```
번호를 누르고 Enter 만 치면 된다. 명령을 직접 쓰고 싶으면:

```bash
bash scripts/aivis.sh start      # 시작
bash scripts/aivis.sh stop       # 중지
bash scripts/aivis.sh restart    # 재시작 (설정을 바꿨을 때)
bash scripts/aivis.sh status     # 상태 1회 요약
bash scripts/aivis.sh monitor    # 실시간 모니터 (Ctrl+C 로 종료)
bash scripts/aivis.sh logs       # 로그 실시간 보기 (Ctrl+C 로 종료)
bash scripts/aivis.sh urls       # 사무실 PC 접속 주소
```

부팅 자동시작을 등록했으면 systemd 로, 안 했으면 백그라운드 직접 실행으로
**알아서 분기**하므로 담당자는 신경 쓸 필요가 없다.

---

## 4. 접속 주소 확인 (사무실 PC 에서 보기)

```bash
bash scripts/aivis.sh urls
```
```
   작업자 화면 (HMI)   http://192.168.0.31:5173
   관리자 대시보드      http://192.168.0.31:5174
   API/문서             http://192.168.0.31:8000   (문서: /docs)
   로그인   아이디: admin   비밀번호: aivis1234
```
- 파이의 실제 IP 를 읽어 표시한다. 사무실 PC 브라우저 주소창에 그대로 입력한다.
- 파이와 PC 가 **같은 네트워크**에 있어야 한다.
- 비밀번호는 최초 로그인 후 반드시 변경한다(대시보드 > 사용자 관리).
- IP 가 자꾸 바뀌면 공유기에서 파이 MAC 에 **고정 IP(DHCP 예약)** 를 설정한다.

---

## 5. 프로그램 업데이트 (개발사가 새 버전을 올렸을 때)

### 5-0. 권장 — 웹 화면에서 버튼으로 (터미널 불필요)

관리자 대시보드 → **프로그램 업데이트** 메뉴에서 버튼만 누르면 됩니다.
터미널을 열 필요가 없어 현장 담당자가 직접 할 수 있습니다.

1. 사무실 PC 브라우저에서 `http://<파이IP>:5174` 접속 (주소는 §4 참고)
2. **관리자 계정**으로 로그인 (작업자·품질관리자 계정은 이 메뉴가 안 보입니다 —
   교대 중에 실수로 눌러 검사가 멈추는 것을 막기 위해서입니다)
3. 메뉴 → **프로그램 업데이트**
4. **[새 버전 확인]** → 새 버전이 있으면 **[지금 업데이트]**
5. 진행 상황이 화면에 표시됩니다. 중간에 화면 연결이 잠시 끊기는 것은
   정상입니다(프로그램이 다시 시작되는 구간). 끝나면 **[화면 새로고침]**.

> **먼저 §2 의 부팅 자동시작을 등록해 두세요.** 등록돼 있어야 업데이트 후
> 프로그램이 스스로 다시 시작해 새 버전이 곧바로 적용됩니다. 등록돼 있지
> 않으면 화면이 "다시 시작해야 적용됩니다" 라고 알려주며, 그때는 아래
> §5-1 의 `bash scripts/aivis.sh restart` 를 한 번 실행해야 합니다.

### 5-1. 터미널에서 (원격 점검·문제 해결용)

```bash
cd ~/pipe_vision
bash scripts/aivis-update.sh
```

진행 화면 예:
```
[1/5] 사전 점검 — 현재 상태 확인
[2/5] 최신 코드 받는 중…
[3/5] 변경 내용 분석 — 다시 만들어야 할 부분만 고릅니다
[4/5] 필요한 부분만 다시 만드는 중…
[5/5] 서비스 재시작
```

이 스크립트가 알아서 해 주는 것:
- 파이에서 손댄 파일이 있으면 **지우지 않고 보관**(git stash)하고 복구 명령을 알려준다.
- 바뀐 부분만 다시 만든다(화면만 바뀌었으면 파이썬 설치를 건너뛰어 시간 절약).
- 실패하면 **어느 단계에서 왜** 실패했는지와 되돌리는 방법을 출력한다.

자주 쓰는 옵션:
```bash
bash scripts/aivis-update.sh --dry-run     # 무엇이 바뀔지 미리보기(실제 변경 없음)
bash scripts/aivis-update.sh --restart     # 업데이트 후 묻지 않고 재시작
bash scripts/aivis-update.sh --rollback    # 직전 업데이트 이전 상태로 되돌리기
bash scripts/aivis-update.sh --help        # 전체 도움말
```

### 5-2. 업데이트가 실패했을 때
1. 화면에 나온 **[실패]** 줄과 그 아래 안내를 그대로 캡처해 개발사에 전달한다.
2. 급하게 생산을 돌려야 하면 이전 버전으로 되돌린다:
   ```bash
   bash scripts/aivis-update.sh --rollback
   bash scripts/aivis.sh restart
   ```
3. 인터넷이 안 되는 현장이면 업데이트는 실패한다(정상). 사무실에서 USB 로 코드를
   받아오거나, 파이를 잠시 인터넷 되는 망에 연결한 뒤 다시 시도한다.

> 검사 데이터(DB·이미지·미전송 스풀)는 `/var/lib/aivis` 에 있고 git 관리 밖이라
> 업데이트/롤백으로 **절대 사라지지 않는다.**

---

## 6. 모니터링

### 6-1. 파이 터미널에서 (화면·SSH 둘 다)
```bash
bash scripts/aivis.sh monitor          # 2초마다 갱신, Ctrl+C 종료
bash scripts/aivis.sh status           # 1회만 보고 끝
```
표시 내용:
- **라즈베리파이 상태**: CPU 온도/사용률/메모리/디스크 (게이지 + 수치)
- **서비스**: API / 데이터베이스 / 검사 워커 → `정상 / 응답지연 / 정지`
- **검사 실적**: 최근 1시간·오늘 검사수·NG·불량률, 처리속도(평균·p95, 목표 300ms), MES 미전송 건수
- **최근 오류**: 마지막 오류 몇 건

상태는 **색 + 기호 + 한국어** 로 함께 표기한다(`[O] 정상`, `[!] 주의`, `[X] 위험`).
색이 안 보이는 화면이나 색약이어도 기호·글자로 판독할 수 있다.

> **API 가 죽어 있어도** 이 모니터는 동작한다. 그때가 가장 필요한 순간이므로,
> 파이에서 직접 읽는 CPU 온도·부하·메모리·디스크는 계속 표시된다.

옵션:
```bash
python3 scripts/aivis-monitor.py --once                 # 1회 출력
python3 scripts/aivis-monitor.py --interval 5           # 5초 주기
python3 scripts/aivis-monitor.py --url http://다른파이:8000
python3 scripts/aivis-monitor.py --user admin --password <비밀번호>
```

### 6-2. 웹 페이지에서 (사무실 PC)
관리자 대시보드(`http://<파이IP>:5174`) 의 **모니터** 화면에서 같은 지표를 그래프로
본다. 여러 사람이 동시에 보기 좋고, 파이 앞에 갈 필요가 없다.

### 6-3. 무엇을 봐야 하나
| 지표 | 정상 | 조치가 필요한 값 |
|---|---|---|
| CPU 온도 | 60℃ 이하 | **70℃↑ 주의**(환기·방열판), **80℃↑ 위험**(성능 저하·정지 위험) |
| 디스크 | 70% 이하 | **85%↑** 오래된 이미지 정리 필요 |
| 검사 워커 | 정상 | **응답지연/정지** → 재시작(3번) |
| 처리속도 p95 | 300ms 이하 | 초과 지속 시 개발사 문의(ROI·모델 조정) |
| MES 미전송 | 0 | 계속 쌓이면 네트워크/MES 점검 |

---

## 7. 자주 겪는 문제와 대처

### 7-1. 카메라를 못 잡는다
증상: 로그에 `camera`/`picamera2` 오류, 워커가 계속 재시작.
```bash
# 1) 카메라가 하드웨어로 보이는지
rpicam-hello --list-cameras          # 구형: libcamera-hello --list-cameras

# 2) 안 보이면: 리본 케이블 방향/접촉 확인 후 전원 껐다 켜기(재부팅 아님, 완전 종료)
sudo shutdown -h now

# 3) 사용자 권한(카메라는 video 그룹 필요)
sudo usermod -aG video $USER && sudo reboot

# 4) 다른 프로세스가 카메라를 점유했는지 (두 유닛 동시 실행 금지!)
systemctl is-active aivis-vision.service aivis-standalone.service
```
급할 때는 카메라 없이 시뮬레이터로 계속 검증할 수 있다:
```bash
AIVIS_CAMERA=sim bash scripts/aivis.sh restart
```

### 7-2. 디스크가 가득 찼다
증상: 모니터 디스크 85%↑, 저장 실패 로그.
```bash
df -h /                                  # 남은 용량
du -sh /var/lib/aivis/images/*           # 어디가 큰지

# 30일보다 오래된 검사 이미지 삭제(먼저 백업 권장!)
find /var/lib/aivis/images -type f -mtime +30 -name '*.jpg' -delete

# 백업(외장 USB 로)
bash scripts/backup.sh                   # 사용법은 scripts/backup.sh 참조
```
근본 대책: 외장 SSD/USB 를 `/var/lib/aivis` 로 마운트하거나 보관 기간 정책을 정한다.

### 7-3. 검사 워커가 멈췄다("정지"/"응답지연")
```bash
bash scripts/aivis.sh logs        # 마지막 오류 확인 (Ctrl+C 로 빠져나옴)
bash scripts/aivis.sh restart     # 재시작
bash scripts/aivis.sh status      # 다시 확인
```
재시작해도 반복되면 로그 마지막 30줄을 캡처해 개발사에 전달한다.

### 7-4. 화면(HMI/대시보드)이 안 열린다
```bash
bash scripts/aivis.sh urls        # 주소·IP 가 맞는지
bash scripts/aivis.sh status      # API 응답 여부
ping <파이IP>                     # 사무실 PC 에서 파이가 보이는지
```
- API 는 되는데 화면만 안 뜨면 화면 빌드가 없는 경우다 → `npm run build` 후 재시작.
- 파이 IP 가 바뀐 경우가 가장 흔하다 → 공유기에서 고정 IP 를 잡아 준다.

### 7-5. 전원 경고(스로틀)가 뜬다
모니터에 `전원/발열 스로틀 발생` 이 보이면 **정품 5V/3A 어댑터**를 쓰고 있는지,
USB 허브·연장선을 거치지 않는지 확인한다. 전원 부족은 카메라 오류·데이터 손상의
흔한 원인이다.

### 7-6. 로그인 비밀번호를 잊었다
`AIVIS_ADMIN_PASSWORD` 로 시드된 관리자 계정을 쓴다. 값을 모르면 개발사에 문의한다
(초기값 `aivis1234`). 운영 중 변경한 비밀번호는 DB 에만 있으므로 개발사도 복구할 수
없고, 관리자 계정 재시드가 필요하다.

---

## 8. 참고 — 관련 파일

| 파일 | 용도 |
|---|---|
| `scripts/aivis.sh` | 통합 조작 메뉴(시작/중지/상태/모니터/업데이트/로그/주소) |
| `scripts/aivis-update.sh` | 원클릭 업데이트 + 롤백 |
| `scripts/aivis-monitor.py` | 터미널 실시간 모니터(표준 라이브러리만 사용) |
| `scripts/aivis-install-service.sh` | 부팅 자동시작 등록/해제 |
| `scripts/aivis-standalone.sh` | 실제 스택 기동(위 스크립트들이 호출) |
| `deploy/aivis-standalone.service` | 독립형 systemd 유닛(전체 스택) |
| `deploy/aivis-vision-pi.service` | 엣지→클라우드 유닛(워커 전용, 동시 사용 금지) |
| `/var/lib/aivis` | 검사 데이터(DB·이미지·스풀). 백업 대상 |
