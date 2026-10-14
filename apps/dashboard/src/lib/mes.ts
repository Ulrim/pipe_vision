/**
 * MES 화면 공통 표기 — 상태 배지, 불량유형, 양품 출처, 연계 지연 판단.
 *
 * 표기 규칙은 모니터·실시간 화면과 같다: 색만으로 말하지 않는다(기호 + 한국어 병기,
 * 색약 고려 M10). 모르는 값은 숨기지 않고 원문 그대로 보여 준다.
 */
import type { IntakeStatus } from "@/api/mes";

/** 배지 톤. neutral = 무채색(정상/대기), 나머지는 의미가 있을 때만 색을 쓴다. */
export type MesTone = "neutral" | "active" | "ok" | "warn" | "danger" | "muted";

export interface StatusView {
  symbol: string;
  text: string;
  tone: MesTone;
}

export const TONE_CLASS: Record<MesTone, string> = {
  neutral: "border border-slate-300 bg-white text-slate-700",
  active: "bg-sky-100 text-sky-900",
  ok: "bg-ok-bg text-ok-fg",
  warn: "bg-amber-100 text-amber-900",
  danger: "bg-ng-bg text-ng-fg",
  muted: "bg-slate-100 text-slate-500",
};

const WO_STATUS: Record<string, StatusView> = {
  PLANNED: { symbol: "○", text: "계획", tone: "neutral" },
  RUNNING: { symbol: "▶", text: "진행 중", tone: "active" },
  DONE: { symbol: "✓", text: "완료", tone: "ok" },
  CANCELLED: { symbol: "✕", text: "취소", tone: "muted" },
};

const SO_STATUS: Record<string, StatusView> = {
  OPEN: { symbol: "○", text: "진행 중", tone: "neutral" },
  CLOSED: { symbol: "✓", text: "마감", tone: "muted" },
};

// 미결 클레임은 처리해야 할 일이라 주의 색을 쓴다.
const CLAIM_STATUS: Record<string, StatusView> = {
  OPEN: { symbol: "⚠", text: "처리 중", tone: "warn" },
  CLOSED: { symbol: "✓", text: "종결", tone: "muted" },
};

function lookup(table: Record<string, StatusView>, status: string | null | undefined): StatusView {
  if (!status) return { symbol: "", text: "-", tone: "neutral" };
  return table[status.toUpperCase()] ?? { symbol: "?", text: status, tone: "neutral" };
}

export const woStatusView = (s: string | null | undefined): StatusView => lookup(WO_STATUS, s);
export const soStatusView = (s: string | null | undefined): StatusView => lookup(SO_STATUS, s);
export const claimStatusView = (s: string | null | undefined): StatusView =>
  lookup(CLAIM_STATUS, s);

/** 작업지시 목록 필터. 값은 GET /work-orders?status= 에 그대로 넣는다(쉼표 = 여러 개). */
export const WO_FILTERS: Array<{ value: string; label: string }> = [
  { value: "PLANNED,RUNNING", label: "진행 중" },
  { value: "DONE", label: "완료" },
  { value: "", label: "전체" },
];

/**
 * 불량유형 표기(§7.2). MANUAL 은 MES 집계가 붙이는 코드 — AI 는 OK 라 했는데
 * 재확인에서 NG 가 된 것(유형 코드가 없다).
 */
export const DEFECT_LABEL: Record<string, string> = {
  LEN: "길이",
  OIL: "유분기",
  DIS: "변색",
  SCR: "스크래치",
  COUNT: "개수",
  MULTI: "복합",
  MANUAL: "재확인 NG",
  ETC: "기타",
};

const DEFECT_ORDER = ["LEN", "OIL", "DIS", "SCR", "COUNT", "MANUAL"];

export function defectLabel(code: string | null | undefined): string {
  if (!code) return "-";
  return DEFECT_LABEL[code.toUpperCase()] ?? code;
}

/** by_code 를 표 순서(§7.2 코드표 순, 모르는 코드는 뒤에 이름순)로 편다. 0건은 뺀다. */
export function sortedDefects(byCode: Record<string, number>): Array<[string, number]> {
  const rank = (c: string) => {
    const i = DEFECT_ORDER.indexOf(c);
    return i === -1 ? DEFECT_ORDER.length : i;
  };
  return Object.entries(byCode)
    .filter(([, n]) => n > 0)
    .sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b));
}

/** 클레임 등록 폼의 불량유형 선택지(ClaimIn.defect_type). */
export const CLAIM_DEFECT_OPTIONS: Array<{ value: string; label: string }> = [
  "LEN",
  "OIL",
  "DIS",
  "SCR",
  "COUNT",
  "ETC",
].map((v) => ({ value: v, label: DEFECT_LABEL[v] }));

/**
 * 양품 수가 어디서 왔는지(ProductionOut.good_source). 같은 "양품" 이라도 크레이트에
 * 실제로 담긴 수인지, 검사 단계에서 불량을 뺀 수인지가 다르다 — 숫자만 두면 단계가
 * 바뀔 때 왜 값이 튀는지 설명할 수 없다.
 */
export function goodSourceLabel(source: string | null | undefined): string {
  switch (source) {
    case "crate":
      return "크레이트 개수 확인 합계";
    case "stage:CUT_LENGTH":
      return "길이 검사 양품";
    case "stage:POST_WASH_SURFACE":
      return "표면 검사 양품";
    case "none":
    case null:
    case undefined:
    case "":
      return "아직 검사 결과 없음";
    default:
      return source.startsWith("stage:") ? `${source.slice(6)} 단계 양품` : source;
  }
}

/** 수신 방식(QualityOut.source / IntakeRunOut.source). */
export function intakeSourceLabel(source: string | null | undefined): string {
  switch (source) {
    case "table":
      return "테이블";
    case "rest":
      return "REST";
    default:
      return source || "-";
  }
}

/**
 * 연계 지연 경고: 검사기 쪽에 안 가져간 건이 있는데 마지막 폴링이 주기의 2배보다
 * 오래됐다(또는 한 번도 안 했다). 쌓여 있는 것 자체는 정상이다 — 다음 폴링이 가져간다.
 * 폴링이 멈췄을 때만 사람이 봐야 한다.
 */
export function isPendingStale(s: IntakeStatus, nowMs: number = Date.now()): boolean {
  if (!s.interface_pending || s.interface_pending <= 0) return false;
  if (!s.last_poll_at) return true;
  const last = new Date(s.last_poll_at).getTime();
  if (Number.isNaN(last)) return true;
  return nowMs - last > 2 * s.poll_interval_s * 1000;
}

/** 오늘(브라우저 현지 = 현장 KST) "YYYY-MM-DD" — date input 기본값. */
export function todayIso(d: Date = new Date()): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** 입력칸 정수(>0 / >=0) 파싱. 잘못되면 null — 제출 버튼을 막는 데 쓴다. */
export function parseCount(v: string, min = 1): number | null {
  const t = v.trim();
  if (t === "") return null;
  const n = Number(t);
  return Number.isInteger(n) && n >= min ? n : null;
}

/** 빈 문자열 → null(서버 Optional 필드). */
export function orNull(v: string): string | null {
  const t = v.trim();
  return t === "" ? null : t;
}
