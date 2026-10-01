/** 표시 포맷 유틸. */
export function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "-";
  return Number.isInteger(v)
    ? v.toLocaleString()
    : v.toLocaleString(undefined, { maximumFractionDigits: digits });
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  // 브라우저 로케일을 그대로 쓰면 한국 현장 화면에 `10/1/2026, 12:41:14 AM`
  // 같은 미국식 12시간제가 찍힌다(실제로 그렇게 나왔다). HMI 는 한국식인데
  // 대시보드만 미국식이라 같은 제품 안에서 표기가 달랐다. 24시간제로 고정한다.
  return d.toLocaleString("ko-KR", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

/** 같은 날 안에서 비교할 때 쓰는 짧은 표기 — `10-01 00:41:14`. */
export function fmtTimeShort(iso: string | null | undefined): string {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(
    d.getMinutes(),
  )}:${p(d.getSeconds())}`;
}

/** 현재 월 YYYY-MM. */
export function currentPeriod(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}
