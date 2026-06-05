/**
 * 브라우저 파일 다운로드 트리거. Blob -> <a download> 클릭 (jsdom 에서 모킹 가능).
 */
export function triggerBlobDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // 즉시 revoke 하면 일부 브라우저에서 취소되므로 다음 틱에.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

/** 현재 조회결과 행 배열을 CSV 문자열로 직렬화(M11 CSV 다운로드). */
export function rowsToCsv(
  rows: Record<string, unknown>[],
  columns: { key: string; header: string }[],
): string {
  const esc = (v: unknown): string => {
    if (v === null || v === undefined) return "";
    const s = Array.isArray(v) ? v.join("|") : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const head = columns.map((c) => esc(c.header)).join(",");
  const body = rows
    .map((r) => columns.map((c) => esc(r[c.key])).join(","))
    .join("\n");
  return `${head}\n${body}\n`;
}
