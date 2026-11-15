/**
 * MES 범위 확대분(기준정보·공정진행·재고·설비) 공통 표기 — lib/mes.ts 와 같은 규칙:
 * 색만으로 말하지 않고(기호 + 한국어), 모르는 값은 원문 그대로 보여 준다.
 */
import type { ItemType, RoutingSource } from "@/api/mesPlant";
import type { StatusView } from "@/lib/mes";

export interface Msg {
  ok: boolean;
  text: string;
}

export function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

function lookup(table: Record<string, StatusView>, v: string | null | undefined): StatusView | null {
  if (!v) return null;
  return table[v.toUpperCase()] ?? { symbol: "?", text: v, tone: "neutral" };
}

const OP_STATUS: Record<string, StatusView> = {
  PENDING: { symbol: "○", text: "대기", tone: "neutral" },
  RUNNING: { symbol: "▶", text: "진행 중", tone: "active" },
  DONE: { symbol: "✓", text: "완료", tone: "ok" },
  SKIPPED: { symbol: "⤼", text: "건너뜀", tone: "muted" },
};

export function opStatusView(s: string | null | undefined): StatusView {
  return lookup(OP_STATUS, s) ?? { symbol: "", text: "-", tone: "neutral" };
}

const EQUIP_STATE: Record<string, StatusView> = {
  RUN: { symbol: "▶", text: "가동", tone: "ok" },
  IDLE: { symbol: "‖", text: "대기", tone: "neutral" },
  DOWN: { symbol: "✕", text: "고장", tone: "danger" },
  PM: { symbol: "◇", text: "점검", tone: "warn" },
};

/** 설비 상태. 기록이 한 번도 없으면(null) "기록 없음". */
export function equipStateView(s: string | null | undefined): StatusView {
  return lookup(EQUIP_STATE, s) ?? { symbol: "", text: "기록 없음", tone: "muted" };
}

/** 상태 전환 버튼 순서. */
export const EQUIP_STATES = ["RUN", "IDLE", "DOWN", "PM"] as const;

/** 사유를 꼭 적어야 하는 상태(서버 422 와 같은 규칙). */
export function needsReason(state: string): boolean {
  return state === "DOWN" || state === "PM";
}

export const ITEM_TYPE_OPTIONS: Array<{ value: ItemType; label: string }> = [
  { value: "FG", label: "제품" },
  { value: "RAW", label: "원자재" },
  { value: "SUB", label: "부자재" },
];

export function itemTypeLabel(t: string | null | undefined): string {
  if (!t) return "-";
  return ITEM_TYPE_OPTIONS.find((o) => o.value === t.toUpperCase())?.label ?? t;
}

const TXN_LABEL: Record<string, string> = {
  RECEIPT: "입고",
  ISSUE: "투입",
  PRODUCE: "생산",
  SHIP: "출하",
  ADJUST: "조정",
};

export function txnTypeLabel(t: string | null | undefined): string {
  if (!t) return "-";
  return TXN_LABEL[t.toUpperCase()] ?? t;
}

/** 라우팅이 어디서 왔는지(RoutingOut.source). */
export function routingSourceView(source: RoutingSource | string, itemCode: string): StatusView {
  switch (source) {
    case "item":
      return itemCode === "*"
        ? { symbol: "◆", text: "공통 라우팅", tone: "active" }
        : { symbol: "◆", text: "이 품목 전용 라우팅", tone: "active" };
    case "default":
      return { symbol: "○", text: "공통 라우팅(*) 적용 중", tone: "neutral" };
    case "processes":
      return { symbol: "⚠", text: "라우팅 없음 — 사용 중인 공정 전부", tone: "warn" };
    default:
      return { symbol: "?", text: String(source), tone: "neutral" };
  }
}

function trimNum(v: number, digits: number): string {
  return v.toLocaleString("ko-KR", { maximumFractionDigits: digits });
}

/** 수량(소수 3자리까지, 뒤 0 은 뺀다). */
export function fmtQty(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "-";
  return trimNum(v, 3);
}

/** 부호를 항상 붙인 수량 — 수불 이력(입고 +, 투입 −). 빼기는 하이픈이 아니라 −(U+2212). */
export function fmtSignedQty(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "-";
  if (v === 0) return "0";
  return `${v > 0 ? "+" : "−"}${trimNum(Math.abs(v), 3)}`;
}

/** 초 → "2시간 5분" / "5분" / "40초". 가동률 표·경과 시간. */
export function fmtDuration(sec: number | null | undefined): string {
  if (sec === null || sec === undefined || !Number.isFinite(sec)) return "-";
  const s = Math.max(0, Math.round(sec));
  if (s < 60) return `${s}초`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}분`;
  const h = Math.floor(m / 60);
  if (h < 24) return m % 60 ? `${h}시간 ${m % 60}분` : `${h}시간`;
  const d = Math.floor(h / 24);
  return h % 24 ? `${d}일 ${h % 24}시간` : `${d}일`;
}

/** ISO 시각부터 지금(또는 end)까지 초. 못 읽으면 null. */
export function secondsSince(
  iso: string | null | undefined,
  end: string | null | number = Date.now(),
): number | null {
  if (!iso) return null;
  const a = new Date(iso).getTime();
  const b = typeof end === "number" ? end : end ? new Date(end).getTime() : Date.now();
  if (Number.isNaN(a) || Number.isNaN(b)) return null;
  return Math.max(0, (b - a) / 1000);
}

/** 입력칸 실수 파싱. 잘못됐거나 조건 밖이면 null — 제출 버튼을 막는 데 쓴다. */
export function parseQty(
  v: string,
  opt: { min?: number; exclusiveMin?: boolean; max?: number; exclusiveMax?: boolean } = {},
): number | null {
  const t = v.trim();
  if (t === "") return null;
  const n = Number(t);
  if (!Number.isFinite(n)) return null;
  if (opt.min !== undefined && (opt.exclusiveMin ? n <= opt.min : n < opt.min)) return null;
  if (opt.max !== undefined && (opt.exclusiveMax ? n >= opt.max : n > opt.max)) return null;
  return n;
}

/**
 * 오늘의 **KST 날짜** "YYYY-MM-DD". 가동률 기간은 서버가 KST 날짜로 자른다(docs/MES.md §11.4) —
 * 브라우저 시간대가 한국이 아니어도(원격 접속) 같은 '오늘' 을 보내야 가동 중인 설비가 0 으로 나오지 않는다.
 */
export function todayKst(d: Date = new Date()): string {
  return new Date(d.getTime() + 9 * 3600 * 1000).toISOString().slice(0, 10);
}
