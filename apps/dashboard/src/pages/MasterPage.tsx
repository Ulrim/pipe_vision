import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ItemMaster, ItemMasterUpdate } from "@aivis/shared-types";
import {
  calibrateItem,
  clearActiveOrder,
  fetchActiveOrder,
  fetchItems,
  putActiveOrder,
  updateItem,
} from "@/api/endpoints";
import { useAuthStore, canEdit } from "@/store/auth";
import { fmtNum } from "@/lib/format";
import { ApiError } from "@/api/client";

/** M13 — 기준정보 관리(조회/수정, 권한자만). 변경 시 version 표시. */
export function MasterPage(): JSX.Element {
  const role = useAuthStore((s) => s.role);
  const editable = canEdit(role);
  const qc = useQueryClient();
  const [edit, setEdit] = useState<ItemMaster | null>(null);

  const { data, isFetching } = useQuery({
    queryKey: ["master-items"],
    queryFn: fetchItems,
  });

  const mutation = useMutation({
    mutationFn: (p: { code: string; body: ItemMasterUpdate }) =>
      updateItem(p.code, p.body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["master-items"] });
      setEdit(null);
    },
  });

  const items = data ?? [];

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <h1 className="text-xl font-bold">기준정보 관리</h1>
        {!editable && (
          <span className="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-500">
            읽기 전용 (quality+ 권한 필요)
          </span>
        )}
        {isFetching && <span className="text-sm text-slate-400">로딩…</span>}
      </div>

      <ActiveOrderCard items={items} editable={editable} />

      <div className="card overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-left text-slate-500">
            <tr>
              <Th>품목</Th><Th>품목명</Th><Th>기준길이</Th><Th>공차(+/−)</Th>
              <Th>개수</Th><Th>외경(mm)</Th>
              <Th>px→mm</Th><Th>유분기</Th><Th>변색</Th><Th>스크래치</Th>
              <Th>ver</Th><Th>{"수정"}</Th>
            </tr>
          </thead>
          <tbody>
            {items.map((it) => (
              <tr key={it.item_code} className="border-t border-slate-100">
                <Td>{it.item_code}</Td>
                <Td>{it.item_name}</Td>
                <Td>{fmtNum(it.ref_length_mm, 3)}</Td>
                <Td>+{fmtNum(it.tol_plus_mm, 3)} / −{fmtNum(it.tol_minus_mm, 3)}</Td>
                <Td testid={`count-${it.item_code}`}>{fmtNum(it.expected_count, 0)}</Td>
                <Td testid={`od-${it.item_code}`}>{fmtNum(it.outer_diameter_mm, 2)}</Td>
                <Td>{fmtNum(it.px_to_mm_scale, 6)}</Td>
                <Td>{fmtNum(it.oil_threshold, 4)}</Td>
                <Td>{fmtNum(it.discolor_threshold, 4)}</Td>
                <Td>{fmtNum(it.scratch_threshold, 4)}</Td>
                <Td><span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs">v{it.version}</span></Td>
                <Td>
                  <button type="button" className="btn-ghost"
                    disabled={!editable} onClick={() => setEdit(it)}
                    data-testid={`edit-${it.item_code}`}>
                    수정
                  </button>
                </Td>
              </tr>
            ))}
            {items.length === 0 && !isFetching && (
              <tr><td colSpan={12} className="p-6 text-center text-slate-400">품목 없음</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {edit && (
        <ItemEditForm
          item={edit}
          saving={mutation.isPending}
          error={mutation.error as Error | null}
          onCancel={() => setEdit(null)}
          onSave={(body) => mutation.mutate({ code: edit.item_code, body })}
          onCalibrated={(updated) => {
            setEdit(updated);
            qc.invalidateQueries({ queryKey: ["master-items"] });
          }}
        />
      )}
    </div>
  );
}

/**
 * 현재 검사 오더 카드 (발주 기반 전환).
 *
 * 발주마다 품목(모양/외경/개수)·절단 길이가 달라진다. 품질관리자가 여기서
 * 품목+LOT(+작업지시)을 설정하면 라즈베리파이 워커가 폴링(15초 주기)해
 * **재시작 없이** 검사 품목을 전환한다. 미설정이면 워커는 기동 시 환경설정
 * 품목으로 검사한다(기존 동작).
 */
function ActiveOrderCard({
  items, editable,
}: {
  items: ItemMaster[];
  editable: boolean;
}): JSX.Element {
  const qc = useQueryClient();
  const [itemCode, setItemCode] = useState("");
  const [lot, setLot] = useState("");
  const [workOrder, setWorkOrder] = useState("");
  const [msg, setMsg] = useState<string | null>(null);

  const { data: active, isLoading } = useQuery({
    queryKey: ["active-order"],
    queryFn: fetchActiveOrder,
  });

  // 활성 오더 로드 시 폼 초기값 동기화(편집 시작점).
  useEffect(() => {
    if (active) {
      setItemCode(active.item_code);
      setLot(active.lot ?? "");
      setWorkOrder(active.work_order ?? "");
    }
  }, [active]);

  const put = useMutation({
    mutationFn: () =>
      putActiveOrder({
        item_code: itemCode,
        lot: lot.trim() || null,
        work_order: workOrder.trim() || null,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["active-order"] });
      setMsg("적용됨 — 워커가 15초 내 자동 전환합니다.");
    },
    onError: (e) => {
      const err = e as ApiError | Error;
      const detail =
        err instanceof ApiError && err.status === 403
          ? "품질관리자 이상 권한이 필요합니다."
          : err.message;
      setMsg(`적용 실패: ${detail}`);
    },
  });

  const clear = useMutation({
    mutationFn: clearActiveOrder,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["active-order"] });
      setMsg("오더 해제됨 — 워커는 마지막 품목으로 계속 검사합니다.");
    },
    onError: (e) => setMsg(`해제 실패: ${(e as Error).message}`),
  });

  return (
    <div className="card p-4" data-testid="active-order-card">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <h2 className="text-base font-bold">현재 검사 오더</h2>
        {isLoading ? (
          <span className="text-sm text-slate-400">로딩…</span>
        ) : active ? (
          <span
            className="rounded bg-ok-bg px-2 py-0.5 text-sm font-semibold text-ok-fg"
            data-testid="active-order-badge"
          >
            검사 중: {active.item_code}
            {active.lot ? ` · LOT ${active.lot}` : ""}
            {active.work_order ? ` · 지시 ${active.work_order}` : ""}
            {active.updated_by ? ` (설정: ${active.updated_by})` : ""}
          </span>
        ) : (
          <span className="text-sm text-slate-500" data-testid="active-order-empty">
            오더 미설정 — 워커는 기본(환경설정) 품목으로 검사 중
          </span>
        )}
      </div>

      <p className="mb-3 text-xs text-slate-500">
        발주가 바뀌면 여기서 품목과 LOT을 지정하세요. 워커가 15초 내 자동
        전환합니다(재시작 불필요). 길이·공차·개수·배치방향은 품목 기준정보를
        따릅니다.
      </p>

      <div className="flex flex-wrap items-end gap-3">
        <div>
          <span className="label">품목</span>
          <select className="input" value={itemCode}
            onChange={(e) => setItemCode(e.target.value)}
            data-testid="order-item">
            <option value="">선택…</option>
            {items.map((it) => (
              <option key={it.item_code} value={it.item_code}>
                {it.item_code} — {it.item_name}
              </option>
            ))}
          </select>
        </div>
        <div>
          <span className="label">LOT</span>
          <input className="input" value={lot} placeholder="예: LOT20260821-01"
            onChange={(e) => setLot(e.target.value)} data-testid="order-lot" />
        </div>
        <div>
          <span className="label">작업지시(선택)</span>
          <input className="input" value={workOrder} placeholder="예: WO-1042"
            onChange={(e) => setWorkOrder(e.target.value)}
            data-testid="order-wo" />
        </div>
        <button type="button" className="btn-primary"
          disabled={!editable || !itemCode || put.isPending}
          onClick={() => put.mutate()} data-testid="order-apply">
          {put.isPending ? "적용 중…" : "오더 적용"}
        </button>
        {active && (
          <button type="button" className="btn-ghost"
            disabled={!editable || clear.isPending}
            onClick={() => clear.mutate()} data-testid="order-clear">
            해제
          </button>
        )}
      </div>

      {msg && (
        <div className="mt-2 text-sm text-slate-600" data-testid="order-msg">
          {msg}
        </div>
      )}
    </div>
  );
}

const NUM_FIELDS: { key: keyof ItemMasterUpdate; label: string }[] = [
  { key: "ref_length_mm", label: "기준길이(mm)" },
  { key: "tol_plus_mm", label: "공차 +(mm)" },
  { key: "tol_minus_mm", label: "공차 −(mm)" },
  { key: "outer_diameter_mm", label: "외경(mm, 비우면 없음)" },
  { key: "px_to_mm_scale", label: "px→mm 보정계수" },
  { key: "oil_threshold", label: "유분기 임계(0~1)" },
  { key: "discolor_threshold", label: "변색 임계(0~1)" },
  { key: "scratch_threshold", label: "스크래치 임계(0~1)" },
];

function ItemEditForm({
  item, saving, error, onCancel, onSave, onCalibrated,
}: {
  item: ItemMaster;
  saving: boolean;
  error: Error | null;
  onCancel: () => void;
  onSave: (body: ItemMasterUpdate) => void;
  onCalibrated: (updated: ItemMaster) => void;
}): JSX.Element {
  const [name, setName] = useState(item.item_name);
  const [count, setCount] = useState(String(item.expected_count ?? 1));
  const [nums, setNums] = useState<Record<string, string>>(() => {
    const o: Record<string, string> = {};
    for (const f of NUM_FIELDS) {
      const v = item[f.key as keyof ItemMaster];
      o[f.key as string] = v == null ? "" : String(v);
    }
    return o;
  });
  const [recipe, setRecipe] = useState(
    item.capture_recipe ? JSON.stringify(item.capture_recipe, null, 2) : "",
  );
  const [recipeErr, setRecipeErr] = useState<string | null>(null);

  const countInvalid = !Number.isInteger(Number(count)) || Number(count) < 1;

  function submit(): void {
    setRecipeErr(null);
    if (countInvalid) return;
    const body: ItemMasterUpdate = {
      item_name: name,
      expected_count: Number(count),
    };
    for (const f of NUM_FIELDS) {
      const raw = nums[f.key as string];
      body[f.key] = raw === "" ? null : (Number(raw) as never);
    }
    if (recipe.trim()) {
      try {
        body.capture_recipe = JSON.parse(recipe);
      } catch {
        setRecipeErr("촬영 레시피 JSON 형식 오류");
        return;
      }
    } else {
      body.capture_recipe = null;
    }
    onSave(body);
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      role="dialog" aria-modal="true" onClick={onCancel} data-testid="item-edit">
      <div className="card max-h-[90vh] w-full max-w-2xl overflow-y-auto p-5"
        onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-lg font-bold">
            {item.item_code} 기준정보 수정
            <span className="ml-2 rounded bg-slate-100 px-1.5 py-0.5 text-xs">
              현재 v{item.version} → 저장 시 v{item.version + 1}
            </span>
          </h2>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="col-span-2">
            <span className="label">품목명</span>
            <input className="input w-full" value={name}
              onChange={(e) => setName(e.target.value)} data-testid="edit-name" />
          </div>
          <div>
            <span className="label" title="한 프레임(오더)당 튜브 개수. 1=단일, N=다중 검사">
              개수(오더당 튜브)
            </span>
            <input type="number" step="1" min="1" className="input w-full"
              value={count} aria-invalid={countInvalid}
              title="한 프레임(오더)당 튜브 개수. 1=단일, N=다중 검사"
              onChange={(e) => setCount(e.target.value)}
              data-testid="edit-expected_count" />
            {countInvalid && (
              <div className="mt-1 text-xs text-ng" data-testid="count-err">
                1 이상의 정수를 입력하세요.
              </div>
            )}
          </div>
          {NUM_FIELDS.map((f) => (
            <div key={f.key as string}>
              <span className="label">{f.label}</span>
              <input type="number" step="any" className="input w-full"
                value={nums[f.key as string]}
                onChange={(e) => setNums({ ...nums, [f.key as string]: e.target.value })}
                data-testid={`edit-${f.key as string}`} />
            </div>
          ))}
          <div className="col-span-2">
            <span className="label">촬영 레시피 (JSON)</span>
            <textarea className="input h-28 w-full font-mono text-xs" value={recipe}
              onChange={(e) => setRecipe(e.target.value)} data-testid="edit-recipe" />
            {recipeErr && <div className="mt-1 text-xs text-ng">{recipeErr}</div>}
          </div>
        </div>

        <CalibrationSection
          item={item}
          onCalibrated={(updated) => {
            setNums((n) => ({ ...n, px_to_mm_scale: String(updated.px_to_mm_scale) }));
            onCalibrated(updated);
          }}
        />

        {error && (
          <div className="mt-3 rounded bg-ng-bg p-2 text-sm text-ng-fg">
            저장 실패: {error instanceof ApiError ? error.message : error.message}
          </div>
        )}

        <div className="mt-4 flex justify-end gap-2">
          <button type="button" className="btn-ghost" onClick={onCancel}>취소</button>
          <button type="button" className="btn-primary" disabled={saving || countInvalid}
            onClick={submit} data-testid="edit-save">
            {saving ? "저장 중…" : "저장"}
          </button>
        </div>
      </div>
    </div>
  );
}

/**
 * 웹 자기보정 섹션(M13). 기준 길이를 아는 파이프의 시스템 측정값과 실제값을
 * 입력하면 px_to_mm_scale := 기존 scale × (actual/measured) 로 자동 보정된다.
 * POST /master/items/{code}/calibrate 를 호출하고 결과를 부모 폼에 반영한다.
 */
function CalibrationSection({
  item, onCalibrated,
}: {
  item: ItemMaster;
  onCalibrated: (updated: ItemMaster) => void;
}): JSX.Element {
  const [measured, setMeasured] = useState("");
  const [actual, setActual] = useState("");
  const [before, setBefore] = useState<number | null>(null);
  const [after, setAfter] = useState<number | null>(null);

  const m = Number(measured);
  const a = Number(actual);
  const valid = measured !== "" && actual !== "" && m > 0 && a > 0;
  const preview = valid ? item.px_to_mm_scale * (a / m) : null;

  const mutation = useMutation({
    mutationFn: () =>
      calibrateItem(item.item_code, { measured_mm: m, actual_mm: a }),
    onSuccess: (updated) => {
      setBefore(item.px_to_mm_scale);
      setAfter(updated.px_to_mm_scale);
      setMeasured("");
      setActual("");
      onCalibrated(updated);
    },
  });

  return (
    <div className="mt-4 rounded border border-slate-200 p-3" data-testid="calib-section">
      <h3 className="mb-1 text-sm font-semibold">캘리브레이션</h3>
      <p className="mb-2 text-xs text-slate-500">
        기준 길이를 아는 파이프를 검사한 뒤, 시스템 측정값과 실제값을 입력하면
        px→mm 계수가 자동 보정됩니다.
      </p>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <span className="label">측정값(measured_mm)</span>
          <input type="number" step="any" min="0" className="input w-full"
            value={measured} onChange={(e) => setMeasured(e.target.value)}
            data-testid="calib-measured" />
        </div>
        <div>
          <span className="label">실제값(actual_mm)</span>
          <input type="number" step="any" min="0" className="input w-full"
            value={actual} onChange={(e) => setActual(e.target.value)}
            data-testid="calib-actual" />
        </div>
      </div>

      {preview !== null && (
        <div className="mt-2 text-xs text-slate-600" data-testid="calib-preview">
          예상 새 계수: {fmtNum(item.px_to_mm_scale, 6)} → {fmtNum(preview, 6)}
        </div>
      )}

      {after !== null && (
        <div className="mt-2 rounded bg-ok-bg p-2 text-xs text-ok-fg" data-testid="calib-result">
          보정 완료: {fmtNum(before, 6)} → {fmtNum(after, 6)} (v{item.version})
        </div>
      )}

      {mutation.error && (
        <div className="mt-2 rounded bg-ng-bg p-2 text-xs text-ng-fg" data-testid="calib-error">
          보정 실패: {(mutation.error as ApiError | Error).message}
        </div>
      )}

      <div className="mt-2 flex justify-end">
        <button type="button" className="btn-primary"
          disabled={!valid || mutation.isPending}
          onClick={() => mutation.mutate()} data-testid="calib-submit">
          {mutation.isPending ? "보정 중…" : "보정"}
        </button>
      </div>
    </div>
  );
}

function Th({ children }: { children: React.ReactNode }): JSX.Element {
  return <th className="px-3 py-2 font-medium">{children}</th>;
}
function Td({
  children, testid,
}: {
  children: React.ReactNode;
  testid?: string;
}): JSX.Element {
  return (
    <td className="px-3 py-2 tabular-nums" data-testid={testid}>
      {children}
    </td>
  );
}
