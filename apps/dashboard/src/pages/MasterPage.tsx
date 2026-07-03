import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ItemMaster, ItemMasterUpdate } from "@aivis/shared-types";
import { fetchItems, updateItem } from "@/api/endpoints";
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

      <div className="card overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-left text-slate-500">
            <tr>
              <Th>품목</Th><Th>품목명</Th><Th>기준길이</Th><Th>공차(+/−)</Th>
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
              <tr><td colSpan={10} className="p-6 text-center text-slate-400">품목 없음</td></tr>
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
        />
      )}
    </div>
  );
}

const NUM_FIELDS: { key: keyof ItemMasterUpdate; label: string }[] = [
  { key: "ref_length_mm", label: "기준길이(mm)" },
  { key: "tol_plus_mm", label: "공차 +(mm)" },
  { key: "tol_minus_mm", label: "공차 −(mm)" },
  { key: "px_to_mm_scale", label: "px→mm 보정계수" },
  { key: "oil_threshold", label: "유분기 임계(0~1)" },
  { key: "discolor_threshold", label: "변색 임계(0~1)" },
  { key: "scratch_threshold", label: "스크래치 임계(0~1)" },
];

function ItemEditForm({
  item, saving, error, onCancel, onSave,
}: {
  item: ItemMaster;
  saving: boolean;
  error: Error | null;
  onCancel: () => void;
  onSave: (body: ItemMasterUpdate) => void;
}): JSX.Element {
  const [name, setName] = useState(item.item_name);
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

  function submit(): void {
    setRecipeErr(null);
    const body: ItemMasterUpdate = { item_name: name };
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

        {error && (
          <div className="mt-3 rounded bg-ng-bg p-2 text-sm text-ng-fg">
            저장 실패: {error instanceof ApiError ? error.message : error.message}
          </div>
        )}

        <div className="mt-4 flex justify-end gap-2">
          <button type="button" className="btn-ghost" onClick={onCancel}>취소</button>
          <button type="button" className="btn-primary" disabled={saving}
            onClick={submit} data-testid="edit-save">
            {saving ? "저장 중…" : "저장"}
          </button>
        </div>
      </div>
    </div>
  );
}

function Th({ children }: { children: React.ReactNode }): JSX.Element {
  return <th className="px-3 py-2 font-medium">{children}</th>;
}
function Td({ children }: { children: React.ReactNode }): JSX.Element {
  return <td className="px-3 py-2 tabular-nums">{children}</td>;
}
