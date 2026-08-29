import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, within } from "@testing-library/react";
import type { SystemStatus } from "@/api/endpoints";
import { renderApp } from "@/test/utils";

// 엔드포인트 모킹(네트워크 차단) — 기존 페이지 테스트와 동일 패턴.
const fetchSystemStatus = vi.fn();
vi.mock("@/api/endpoints", () => ({
  fetchSystemStatus: (...a: unknown[]) => fetchSystemStatus(...a),
}));

import { MonitorPage, relativeTimeKo, fmtMetric } from "./MonitorPage";

const base: SystemStatus = {
  ts: "2026-08-29T09:00:00Z",
  system: {
    cpu_temp_c: 52.4,
    cpu_percent: 31.2,
    load_1m: 0.85,
    mem_total_mb: 3800,
    mem_used_mb: 1520,
    mem_percent: 40,
    disk_total_gb: 58.2,
    disk_used_gb: 21.5,
    disk_percent: 37,
    throttled: false,
  },
  services: { db: "up", worker: "up", worker_last_seen_s: 3 },
  inspection: {
    last_hour: { total: 420, ng: 7, ng_rate_pct: 1.67 },
    today: { total: 3150, ng: 41, ng_rate_pct: 1.3 },
    avg_proc_time_ms: 182.5,
    p95_proc_time_ms: 248,
    last_inspected_at: "2026-08-29T08:59:30Z",
    mes_pending: 0,
  },
  active_order: { item_code: "HP12", lot: "LOT-77", work_order: "WO-1" },
  recent_errors: [
    { ts: "2026-08-29T08:40:00Z", message: "MES 전송 타임아웃" },
  ],
};

/** 부분 오버라이드로 상태 픽스처 생성. */
function statusWith(patch: Partial<SystemStatus>): SystemStatus {
  return { ...base, ...patch };
}

beforeEach(() => {
  fetchSystemStatus.mockReset().mockResolvedValue(base);
});

describe("MonitorPage — 정상 응답", () => {
  it("워커/DB/오더 배지와 검사 현황, 오류 목록을 표시한다", async () => {
    renderApp(<MonitorPage />);

    const worker = await screen.findByTestId("svc-worker");
    expect(worker).toHaveTextContent("정상");
    expect(worker).toHaveTextContent("[O]"); // 색 단독 아님 — 기호 병기
    expect(worker).toHaveTextContent("마지막 응답 3초 전");

    const db = screen.getByTestId("svc-db");
    expect(db).toHaveTextContent("[O]");
    expect(db).toHaveTextContent("정상");

    const order = screen.getByTestId("svc-order");
    expect(order).toHaveTextContent("HP12");
    expect(order).toHaveTextContent("LOT-77");

    // 검사 현황 수치.
    expect(screen.getByTestId("insp-last-hour")).toHaveTextContent("420 / 7");
    expect(screen.getByTestId("insp-last-hour")).toHaveTextContent("1.67%");
    expect(screen.getByTestId("insp-today")).toHaveTextContent("3,150 / 41");
    expect(screen.getByTestId("insp-avg")).toHaveTextContent("182.5ms");
    expect(screen.getByTestId("insp-p95")).toHaveTextContent("248ms");

    // 자원 카드(사용/전체 병기).
    expect(screen.getByTestId("res-cpu-temp")).toHaveTextContent("52.4℃");
    expect(screen.getByTestId("res-mem")).toHaveTextContent("1,520 / 3,800 MB");
    expect(screen.getByTestId("res-disk")).toHaveTextContent("21.5 / 58.2 GB");

    // 최근 오류.
    expect(within(screen.getByTestId("error-list")).getByText("MES 전송 타임아웃")).toBeInTheDocument();
    expect(screen.queryByTestId("errors-empty")).not.toBeInTheDocument();
    // 정상 상태에서는 스로틀 배너 없음.
    expect(screen.queryByTestId("throttle-banner")).not.toBeInTheDocument();
  });

  it("갱신 시각을 표시한다(5초 자동 갱신 안내 포함)", async () => {
    renderApp(<MonitorPage />);
    await screen.findByTestId("svc-worker");
    expect(screen.getByTestId("monitor-updated")).toHaveTextContent("5초마다 자동 갱신");
  });
});

describe("MonitorPage — 워커/DB 상태 표기(기호+문자, 색 단독 금지)", () => {
  it("worker=stale 이면 '응답 지연' 문자가 보인다", async () => {
    fetchSystemStatus.mockResolvedValue(
      statusWith({ services: { db: "up", worker: "stale", worker_last_seen_s: 47 } }),
    );
    renderApp(<MonitorPage />);
    const worker = await screen.findByTestId("svc-worker");
    expect(worker).toHaveTextContent("응답 지연");
    expect(worker).toHaveTextContent("[△]");
    expect(worker).toHaveTextContent("마지막 응답 47초 전");
  });

  it("worker=down 이면 '정지' 문자가 보인다", async () => {
    fetchSystemStatus.mockResolvedValue(
      statusWith({ services: { db: "down", worker: "down", worker_last_seen_s: null } }),
    );
    renderApp(<MonitorPage />);
    const worker = await screen.findByTestId("svc-worker");
    expect(worker).toHaveTextContent("정지");
    expect(worker).toHaveTextContent("[X]");
    expect(worker).toHaveTextContent("마지막 응답 기록 없음");

    const db = screen.getByTestId("svc-db");
    expect(db).toHaveTextContent("정지");
    expect(db).toHaveTextContent("[X]");
  });

  it("활성 오더가 없으면 '오더 미설정'", async () => {
    fetchSystemStatus.mockResolvedValue(statusWith({ active_order: null }));
    renderApp(<MonitorPage />);
    expect(await screen.findByTestId("svc-order")).toHaveTextContent("오더 미설정");
  });
});

describe("MonitorPage — null 지표는 '측정 불가'(0 표시 금지 회귀)", () => {
  it("자원 값이 모두 null 이면 측정 불가로 표기한다", async () => {
    fetchSystemStatus.mockResolvedValue(
      statusWith({
        system: {
          cpu_temp_c: null, cpu_percent: null, load_1m: null,
          mem_total_mb: null, mem_used_mb: null, mem_percent: null,
          disk_total_gb: null, disk_used_gb: null, disk_percent: null,
          throttled: null,
        },
        inspection: { ...base.inspection, avg_proc_time_ms: null, p95_proc_time_ms: null },
      }),
    );
    renderApp(<MonitorPage />);

    const temp = await screen.findByTestId("res-cpu-temp");
    expect(temp).toHaveTextContent("측정 불가");
    expect(temp).not.toHaveTextContent("0℃");

    for (const id of ["res-cpu", "res-mem", "res-disk"]) {
      const card = screen.getByTestId(id);
      expect(card).toHaveTextContent("측정 불가");
      expect(card).not.toHaveTextContent("0%");
    }

    expect(screen.getByTestId("insp-avg")).toHaveTextContent("측정 불가");
    expect(screen.getByTestId("insp-p95")).toHaveTextContent("측정 불가");
    // throttled=null 은 경고 배너를 띄우지 않는다(측정 불가 ≠ 이상).
    expect(screen.queryByTestId("throttle-banner")).not.toBeInTheDocument();
  });

  it("마지막 검사 시각이 null 이면 '기록 없음'", async () => {
    fetchSystemStatus.mockResolvedValue(
      statusWith({ inspection: { ...base.inspection, last_inspected_at: null } }),
    );
    renderApp(<MonitorPage />);
    expect(await screen.findByTestId("insp-last-at")).toHaveTextContent("기록 없음");
  });
});

describe("MonitorPage — 경고 표기", () => {
  it("CPU 75℃ 는 경고 문구를 표시한다", async () => {
    fetchSystemStatus.mockResolvedValue(
      statusWith({ system: { ...base.system, cpu_temp_c: 75 } }),
    );
    renderApp(<MonitorPage />);
    const temp = await screen.findByTestId("res-cpu-temp");
    expect(temp).toHaveTextContent("75℃");
    expect(temp).toHaveTextContent("경고");
    expect(temp).toHaveTextContent("[△]");
  });

  it("CPU 85℃ 는 위험 문구를 표시한다", async () => {
    fetchSystemStatus.mockResolvedValue(
      statusWith({ system: { ...base.system, cpu_temp_c: 85 } }),
    );
    renderApp(<MonitorPage />);
    const temp = await screen.findByTestId("res-cpu-temp");
    expect(temp).toHaveTextContent("위험");
    expect(temp).toHaveTextContent("[X]");
  });

  it("throttled=true 면 전원 부족/스로틀 배너를 표시한다", async () => {
    fetchSystemStatus.mockResolvedValue(
      statusWith({ system: { ...base.system, throttled: true } }),
    );
    renderApp(<MonitorPage />);
    expect(await screen.findByTestId("throttle-banner")).toHaveTextContent(
      "전원 부족/스로틀 감지",
    );
  });

  it("처리속도가 300ms 를 넘으면 목표 초과 경고를 표시한다", async () => {
    fetchSystemStatus.mockResolvedValue(
      statusWith({
        inspection: { ...base.inspection, avg_proc_time_ms: 310, p95_proc_time_ms: 480 },
      }),
    );
    renderApp(<MonitorPage />);
    expect(await screen.findByTestId("insp-avg")).toHaveTextContent("300ms 목표 초과");
    expect(screen.getByTestId("insp-p95")).toHaveTextContent("300ms 목표 초과");
  });

  it("MES 미전송이 0 초과면 강조 문구를 표시한다", async () => {
    fetchSystemStatus.mockResolvedValue(
      statusWith({ inspection: { ...base.inspection, mes_pending: 12 } }),
    );
    renderApp(<MonitorPage />);
    const cell = await screen.findByTestId("insp-mes-pending");
    expect(cell).toHaveTextContent("12건");
    expect(cell).toHaveTextContent("미전송 대기");
  });
});

describe("MonitorPage — 오류/예외 상태", () => {
  it("recent_errors 가 비면 '최근 오류 없음'", async () => {
    fetchSystemStatus.mockResolvedValue(statusWith({ recent_errors: [] }));
    renderApp(<MonitorPage />);
    expect(await screen.findByTestId("errors-empty")).toHaveTextContent("최근 오류 없음");
  });

  it("API 실패 시 파이 전원/네트워크 확인 안내를 표시한다", async () => {
    fetchSystemStatus.mockRejectedValue(new Error("Failed to fetch"));
    renderApp(<MonitorPage />);
    expect(await screen.findByTestId("monitor-error")).toHaveTextContent(
      "API 연결 실패 — 파이 전원/네트워크 확인",
    );
  });
});

describe("MonitorPage 표시 헬퍼", () => {
  it("relativeTimeKo — 초/분/시간/일 단위", () => {
    const now = new Date("2026-08-29T09:00:00Z").getTime();
    expect(relativeTimeKo("2026-08-29T08:59:30Z", now)).toBe("30초 전");
    expect(relativeTimeKo("2026-08-29T08:55:00Z", now)).toBe("5분 전");
    expect(relativeTimeKo("2026-08-29T06:00:00Z", now)).toBe("3시간 전");
    expect(relativeTimeKo("2026-08-27T09:00:00Z", now)).toBe("2일 전");
    expect(relativeTimeKo(null, now)).toBe("기록 없음");
  });

  it("fmtMetric — null 은 0 이 아니라 '측정 불가'", () => {
    expect(fmtMetric(null, "%")).toBe("측정 불가");
    expect(fmtMetric(0, "%")).toBe("0%");
    expect(fmtMetric(41.25, "%", 1)).toBe("41.3%");
  });
});
