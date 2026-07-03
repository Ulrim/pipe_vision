/**
 * WS 연결 상태 인디케이터 (CLAUDE.md 원칙: 끊김 시 재연결, 상태 표시).
 * 색 + 텍스트 이중표기.
 */
import { useLiveStore, type ConnState } from "@/store/liveStore";

const META: Record<ConnState, { label: string; dot: string; text: string }> = {
  connecting: { label: "연결 중…", dot: "bg-amber-400", text: "text-amber-700" },
  open: { label: "실시간 연결됨", dot: "bg-ok", text: "text-ok-fg" },
  reconnecting: {
    label: "재연결 중…",
    dot: "bg-amber-500 animate-pulse",
    text: "text-amber-700",
  },
  closed: { label: "연결 끊김", dot: "bg-ng", text: "text-ng-fg" },
};

export function ConnectionIndicator() {
  const conn = useLiveStore((s) => s.conn);
  const attempts = useLiveStore((s) => s.reconnectAttempts);
  const m = META[conn];
  return (
    <div
      className="inline-flex items-center gap-2 rounded-lg bg-white/80 px-3 py-2 shadow-sm"
      role="status"
      aria-label={`연결 상태: ${m.label}`}
      data-conn={conn}
    >
      <span className={`inline-block h-3 w-3 rounded-full ${m.dot}`} aria-hidden />
      <span className={`font-semibold ${m.text}`}>{m.label}</span>
      {conn === "reconnecting" && attempts > 0 && (
        <span className="text-sm text-gray-500">(시도 {attempts})</span>
      )}
    </div>
  );
}
