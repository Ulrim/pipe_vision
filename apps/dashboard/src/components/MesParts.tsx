/**
 * MES 화면(작업지시·생산실적·출하·연계) 공통 조각.
 * 표·배지·오류 상자 모양을 네 화면이 똑같이 쓰게 해 "같은 말은 같은 모양" 을 지킨다.
 */
import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import type { ItemMaster } from "@aivis/shared-types";
import { fetchItems } from "@/api/endpoints";
import { MES_UNREACHABLE } from "@/api/mes";
import { fmtNum } from "@/lib/format";
import { TONE_CLASS, type StatusView } from "@/lib/mes";

/** AIVIS 기준정보 품목(수주·작업지시·출하 품목 선택지). 기준정보 화면과 같은 캐시 키. */
export function useAivisItems(): { items: ItemMaster[]; failed: boolean } {
  const { data, isError } = useQuery({
    queryKey: ["master-items"],
    queryFn: fetchItems,
    staleTime: 60_000,
  });
  return { items: data ?? [], failed: isError };
}

/**
 * MES 조회 실패 상자. MES 는 별도 서버라 꺼져 있어도 대시보드의 나머지는 돈다 —
 * 그래서 화면 전체를 깨지 말고 그 구역에만 이유를 적는다.
 */
export function MesError({
  what,
  error,
  testid,
}: {
  what: string;
  error: unknown;
  testid?: string;
}): JSX.Element {
  const msg = error instanceof Error ? error.message : String(error ?? "");
  const unreachable = msg.startsWith(MES_UNREACHABLE);
  return (
    <div className="rounded-md bg-ng-bg p-3 text-sm text-ng-fg" role="alert" data-testid={testid}>
      <span aria-hidden="true">✕ </span>
      {what} 실패: {msg || "알 수 없는 오류"}
      {unreachable && (
        <div className="mt-1 text-xs opacity-80">
          MES 서비스(services/mes, 기본 포트 8100)가 켜져 있는지, 역프록시 배포라면
          /mes-api 경로가 MES 로 연결돼 있는지 확인하세요.
        </div>
      )}
    </div>
  );
}

/** 상태 배지 — 색 + 기호 + 한국어(색 단독 금지). */
export function StatusBadge({ view, testid }: { view: StatusView; testid?: string }): JSX.Element {
  return (
    <span
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded px-1.5 py-0.5 text-xs font-bold ${TONE_CLASS[view.tone]}`}
      data-testid={testid}
    >
      {view.symbol && <span aria-hidden="true">{view.symbol}</span>}
      {view.text}
    </span>
  );
}

/** 진척률 막대. 100% 초과(과생산)는 막대를 꽉 채우고 글자로 "초과" 를 덧붙인다. */
export function ProgressBar({
  pct,
  testid,
}: {
  pct: number | null | undefined;
  testid?: string;
}): JSX.Element {
  if (pct === null || pct === undefined || !Number.isFinite(pct)) {
    return <span className="text-slate-400">-</span>;
  }
  const width = Math.max(0, Math.min(100, pct));
  const over = pct > 100;
  return (
    <div className="flex min-w-[7rem] items-center gap-2" data-testid={testid}>
      <div
        className="h-2 flex-1 overflow-hidden rounded bg-slate-200"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(width)}
        aria-label="진척률"
      >
        <div
          className={`h-full ${pct >= 100 ? "bg-ok" : "bg-brand"}`}
          style={{ width: `${width}%` }}
        />
      </div>
      <span className="whitespace-nowrap text-xs tabular-nums text-slate-700">
        {fmtNum(pct, 1)}%{over && <span className="text-amber-800"> ▲초과</span>}
      </span>
    </div>
  );
}

/** 납기 준수 표시. null = 수주 납기 없음(판단 불가). */
export function OnTimeMark({ onTime }: { onTime: boolean | null }): JSX.Element {
  if (onTime === null) {
    return (
      <span className="text-slate-400" title="수주에 납기가 없거나 수주 없이 출하">
        -
      </span>
    );
  }
  return onTime ? (
    <span className="whitespace-nowrap font-semibold text-ok-fg">
      <span aria-hidden="true">✓ </span>납기 내
    </span>
  ) : (
    <span className="whitespace-nowrap font-semibold text-ng-fg">
      <span aria-hidden="true">✕ </span>납기 초과
    </span>
  );
}

/**
 * 품목 선택. 선택지는 AIVIS 기준정보지만, 수주에서 넘어온 품목이 기준정보에 없을 수도
 * 있어(삭제·오타) 현재 값은 항상 선택지에 넣는다 — 안 그러면 select 가 조용히 빈 값을
 * 보여 주고 사용자는 품목이 비었다고 착각한다.
 */
export function ItemSelect({
  label,
  items,
  value,
  onChange,
  disabled,
  optional,
  testid,
}: {
  label: string;
  items: ItemMaster[];
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
  optional?: boolean;
  testid?: string;
}): JSX.Element {
  const known = items.some((it) => it.item_code === value);
  return (
    <Field label={label}>
      <select
        className="input w-full"
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        data-testid={testid}
      >
        <option value="">{optional ? "선택 안 함" : "선택…"}</option>
        {value && !known && <option value={value}>{value}</option>}
        {items.map((it) => (
          <option key={it.item_code} value={it.item_code}>
            {it.item_code} — {it.item_name}
          </option>
        ))}
      </select>
    </Field>
  );
}

/** 라벨로 감싼 입력칸(getByLabelText 로 찾을 수 있게 label 이 입력을 감싼다). */
export function Field({ label, children }: { label: string; children: ReactNode }): JSX.Element {
  return (
    <label className="block min-w-0">
      <span className="label">{label}</span>
      {children}
    </label>
  );
}

export function Th({ children, right }: { children?: ReactNode; right?: boolean }): JSX.Element {
  return (
    <th className={`whitespace-nowrap px-3 py-2 font-medium ${right ? "text-right" : ""}`}>
      {children}
    </th>
  );
}

export function Td({
  children,
  right,
  testid,
}: {
  children?: ReactNode;
  right?: boolean;
  testid?: string;
}): JSX.Element {
  return (
    <td
      className={`px-3 py-2 ${right ? "text-right tabular-nums" : ""}`}
      data-testid={testid}
    >
      {children}
    </td>
  );
}

/** 표 안의 빈 상태 한 줄. */
export function EmptyRow({
  colSpan,
  children,
  testid,
}: {
  colSpan: number;
  children: ReactNode;
  testid?: string;
}): JSX.Element {
  return (
    <tr>
      <td colSpan={colSpan} className="p-6 text-center text-slate-400" data-testid={testid}>
        {children}
      </td>
    </tr>
  );
}

/** 표 머리 줄 공통 스타일. */
export const THEAD_CLASS = "bg-slate-50 text-left text-slate-500";

/** 저장 결과 한 줄(성공은 초록 ✓, 실패는 빨강 ✕ — 기호 병기). */
export function ResultLine({
  ok,
  children,
  testid,
}: {
  ok: boolean;
  children: ReactNode;
  testid?: string;
}): JSX.Element {
  return (
    <div
      className={`text-sm ${ok ? "text-ok-fg" : "text-ng-fg"}`}
      role={ok ? "status" : "alert"}
      data-testid={testid}
    >
      <span aria-hidden="true">{ok ? "✓ " : "✕ "}</span>
      {children}
    </div>
  );
}
