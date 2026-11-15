/**
 * MES 범위 확대분(2026-11-15) 클라이언트 — 기준정보·BOM / 공정 진행 / 자재·재고 / 설비 가동
 * (docs/MES.md §11.5, services/mes/mes_app/routers/master.py·shopfloor.py·stock.py·equipment.py).
 *
 * 요청은 api/mes.ts 의 requestMes 를 그대로 쓴다 — 주소 결정(MES_BASE), 토큰, "MES 서버에
 * 연결할 수 없습니다" 처리, 401 에 로그아웃하지 않는 규칙이 기존 MES 화면과 같아야 한다.
 *
 * 타입은 services/mes/mes_app/schemas.py 의 "2026-11-15 범위 확대" 아래 클래스와 1:1.
 * 옮기는 규칙은 api/mes.ts 머리말과 같다: 응답의 `Optional[X]`(기본값 없음)는 `X | null`,
 * 응답의 `Optional[X] = None` 도 pydantic 이 null 로 내보내므로 `X | null`,
 * 입력의 `Optional[X] = None`·기본값 있는 필드는 생략 가능 `?:`.
 */
import { toQuery } from "./client";
import { requestMes } from "./mes";

/* ---------------- 상태 값 ---------------- */
/** 원자재 | 제품 | 부자재 */
export type ItemType = "RAW" | "FG" | "SUB";
export type OpStatus = "PENDING" | "RUNNING" | "DONE" | "SKIPPED";
/** 가동 | 대기 | 고장 | 계획정지(점검) */
export type EquipState = "RUN" | "IDLE" | "DOWN" | "PM";
export type TxnType = "RECEIPT" | "ISSUE" | "PRODUCE" | "SHIP" | "ADJUST";

/* ---------------- 품목·BOM ---------------- */
export interface ItemIn {
  item_code: string;
  name: string;
  /** 기본 FG. */
  item_type?: ItemType;
  /** 기본 EA. */
  unit?: string;
  /** 안전재고 — 이보다 적으면 재고 화면에 경고. 기본 0. */
  safety_stock?: number;
  spec?: string | null;
}

export interface ItemPatch {
  name?: string | null;
  item_type?: ItemType | null;
  unit?: string | null;
  safety_stock?: number | null;
  spec?: string | null;
  active?: boolean | null;
}

export interface ItemOut {
  item_code: string;
  name: string;
  /** 서버 응답 모델은 str — 알려진 값은 ItemType. */
  item_type: string;
  unit: string;
  safety_stock: number;
  spec: string | null;
  active: boolean;
  created_at: string | null;
}

export interface BomLineIn {
  component_code: string;
  /** 제품 1개당 소요량(자재 단위), > 0. */
  qty_per: number;
  /** 손실률 % (0 이상 100 미만) — 소요량에 더한다. 기본 0. */
  scrap_pct?: number;
  note?: string | null;
}

export interface BomIn {
  lines: BomLineIn[];
}

export interface BomLineOut {
  parent_code: string;
  component_code: string;
  component_name: string | null;
  unit: string | null;
  qty_per: number;
  scrap_pct: number;
  note: string | null;
}

/* ---------------- 공정·라우팅 ---------------- */
export interface ProcessIn {
  code: string;
  name: string;
  /** 기본 공정 순서(0 이상). */
  seq: number;
  /** 검사 공정 — 양품·불량을 AIVIS 검사결과에서 가져온다(손으로 넣지 않는다). 기본 false. */
  auto_from_inspection?: boolean;
}

export interface ProcessPatch {
  name?: string | null;
  seq?: number | null;
  auto_from_inspection?: boolean | null;
  active?: boolean | null;
}

export interface ProcessOut {
  code: string;
  name: string;
  seq: number;
  auto_from_inspection: boolean;
  active: boolean;
}

export interface RoutingStepIn {
  process_code: string;
  equipment_code?: string | null;
  /** 표준 작업시간(초/개). */
  std_sec_per_ea?: number | null;
}

export interface RoutingIn {
  steps: RoutingStepIn[];
}

export interface RoutingStepOut {
  seq: number;
  process_code: string;
  process_name: string;
  equipment_code: string | null;
  std_sec_per_ea: number | null;
}

/** item = 이 품목 전용 / default = 공통 라우팅("*") / processes = 라우팅 없음 → 사용 중인 공정 전부 */
export type RoutingSource = "item" | "default" | "processes";

export interface RoutingOut {
  item_code: string;
  source: RoutingSource;
  steps: RoutingStepOut[];
}

/** 공통 라우팅의 품목 코드. */
export const ROUTING_DEFAULT = "*";

/* ---------------- 설비·가동 ---------------- */
export interface EquipmentIn {
  code: string;
  name: string;
  process_code?: string | null;
  note?: string | null;
}

export interface EquipmentPatch {
  name?: string | null;
  process_code?: string | null;
  note?: string | null;
  active?: boolean | null;
}

export interface EquipmentOut {
  code: string;
  name: string;
  process_code: string | null;
  note: string | null;
  active: boolean;
  /** 기록이 한 번도 없으면 null. */
  state: EquipState | null;
  state_since: string | null;
  state_reason: string | null;
  /** 지금 이 설비에서 돌고 있는 작업지시. */
  wo_id: number | null;
  wo_no: string | null;
}

export interface EquipmentStateIn {
  state: EquipState;
  /** DOWN·PM 은 사유 필수(서버 422). */
  reason?: string | null;
}

export interface EquipmentEventOut {
  id: number;
  equipment_code: string;
  /** 서버 응답 모델은 str — 알려진 값은 EquipState. */
  state: string;
  reason: string | null;
  started_at: string;
  /** 진행 중이면 null. */
  ended_at: string | null;
  wo_id: number | null;
  created_by: string | null;
}

export interface UtilizationRow {
  equipment_code: string;
  name: string;
  run_s: number;
  idle_s: number;
  down_s: number;
  pm_s: number;
  /** 가동률 = 가동 ÷ (가동 + 대기 + 고장). 계획정지(PM)와 기록 없는 시간은 분모에서 뺀다. */
  availability_pct: number | null;
  down_count: number;
  /** 평균 수리시간(분) = 고장시간 ÷ 고장 횟수. */
  mttr_min: number | null;
  /** 평균 고장간격(분) = 가동시간 ÷ 고장 횟수. */
  mtbf_min: number | null;
}

export interface UtilizationOut {
  date_from: string;
  date_to: string;
  rows: UtilizationRow[];
}

/* ---------------- 공정 진행 ---------------- */
export interface OperationOut {
  id: number;
  wo_id: number;
  seq: number;
  process_code: string;
  process_name: string;
  equipment_code: string | null;
  /** 서버 응답 모델은 str — 알려진 값은 OpStatus. */
  status: string;
  started_at: string | null;
  ended_at: string | null;
  good_qty: number | null;
  defect_qty: number | null;
  worker: string | null;
  note: string | null;
  /** 검사 공정 — 아래 두 값이 AIVIS 검사결과에서 온다. */
  auto: boolean;
  auto_good_qty: number | null;
  auto_defect_qty: number | null;
}

export interface OpStartIn {
  equipment_code?: string | null;
  worker?: string | null;
}

export interface OpCompleteIn {
  /** 검사 공정은 비워도 된다. 그 밖의 공정은 필수(서버 422). */
  good_qty?: number | null;
  /** 기본 0. */
  defect_qty?: number;
  note?: string | null;
}

/* ---------------- 자재 소요·투입, 재고 ---------------- */
export interface MaterialLine {
  item_code: string;
  name: string | null;
  unit: string | null;
  qty_per: number;
  scrap_pct: number;
  /** 지시수량 × 소요량 × (1 + 손실률) */
  required: number;
  /** 이 작업지시에 이미 투입한 양 */
  issued: number;
  /** 현재고 */
  on_hand: number;
  /** max(0, 남은 필요량 − 현재고) */
  shortage: number;
}

export interface MaterialsOut {
  wo_id: number;
  plan_qty: number;
  lines: MaterialLine[];
}

export interface IssueLineIn {
  item_code: string;
  qty: number;
  /** 투입한 자재 LOT(추적용). */
  lot?: string | null;
}

export interface MaterialIssueIn {
  /** 비우면 BOM 기준 남은 필요량을 전부 투입한다. */
  lines?: IssueLineIn[] | null;
  /** 재고가 모자라도 투입 기록(재고가 음수가 된다). 기본 false. */
  allow_negative?: boolean;
}

export interface StockRow {
  item_code: string;
  name: string | null;
  item_type: string | null;
  unit: string | null;
  on_hand: number;
  safety_stock: number;
  below_safety: boolean;
  last_txn_at: string | null;
}

export interface StockTxnOut {
  id: number;
  ts: string;
  item_code: string;
  lot: string | null;
  /** 부호 있음: 입고·생산 +, 투입·출하 −. */
  qty: number;
  /** 서버 응답 모델은 str — 알려진 값은 TxnType. */
  txn_type: string;
  wo_id: number | null;
  wo_no: string | null;
  shipment_id: number | null;
  note: string | null;
  created_by: string | null;
}

export interface ReceiptIn {
  item_code: string;
  qty: number;
  lot?: string | null;
  note?: string | null;
}

export interface AdjustIn {
  item_code: string;
  /** 더하거나 빼는 양(0 제외). */
  qty: number;
  lot?: string | null;
  /** 조정 사유(필수). */
  note: string;
}

/* ---------------- 요청 ---------------- */

function send<T>(method: "POST" | "PATCH" | "PUT", path: string, body?: unknown): Promise<T> {
  return requestMes<T>(path, {
    method,
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
}

/** 경로에 들어가는 코드(품목·공정·설비). 공통 라우팅 `*` 는 그대로 남는다. */
const seg = (code: string): string => encodeURIComponent(code);

/* ---------------- 품목·BOM ---------------- */
export interface ItemQuery {
  item_type?: ItemType;
  active?: boolean;
}

/** GET /items?item_type=&active= — 품목 목록. */
export function fetchMesItems(q: ItemQuery = {}): Promise<ItemOut[]> {
  return requestMes<ItemOut[]>(`/items${toQuery({ ...q })}`);
}

/** POST /items — 등록(quality+, 중복 409). */
export function createMesItem(body: ItemIn): Promise<ItemOut> {
  return send<ItemOut>("POST", "/items", body);
}

/** PATCH /items/{item_code} — 수정·사용 중지(quality+). */
export function patchMesItem(itemCode: string, body: ItemPatch): Promise<ItemOut> {
  return send<ItemOut>("PATCH", `/items/${seg(itemCode)}`, body);
}

/** GET /bom?parent= — BOM 줄(parent 비우면 전체). */
export function fetchBom(parent?: string): Promise<BomLineOut[]> {
  return requestMes<BomLineOut[]>(`/bom${toQuery({ parent })}`);
}

/** PUT /bom/{parent_code} — 그 제품의 BOM 을 통째로 교체(quality+, 빈 목록 = 삭제). */
export function putBom(parentCode: string, lines: BomLineIn[]): Promise<BomLineOut[]> {
  const body: BomIn = { lines };
  return send<BomLineOut[]>("PUT", `/bom/${seg(parentCode)}`, body);
}

/* ---------------- 공정·라우팅 ---------------- */
/** GET /processes — 공정 목록(순서대로). */
export function fetchProcesses(): Promise<ProcessOut[]> {
  return requestMes<ProcessOut[]>("/processes");
}

/** POST /processes — 등록(quality+). */
export function createProcess(body: ProcessIn): Promise<ProcessOut> {
  return send<ProcessOut>("POST", "/processes", body);
}

/** PATCH /processes/{code} — 수정·사용 중지(quality+). */
export function patchProcess(code: string, body: ProcessPatch): Promise<ProcessOut> {
  return send<ProcessOut>("PATCH", `/processes/${seg(code)}`, body);
}

/** GET /routings/{item_code} — 그 품목에 적용되는 라우팅. `*` = 공통. */
export function fetchRouting(itemCode: string): Promise<RoutingOut> {
  return requestMes<RoutingOut>(`/routings/${seg(itemCode)}`);
}

/** PUT /routings/{item_code} — 통째로 교체(quality+, 빈 목록 = 전용 라우팅 삭제). */
export function putRouting(itemCode: string, steps: RoutingStepIn[]): Promise<RoutingOut> {
  const body: RoutingIn = { steps };
  return send<RoutingOut>("PUT", `/routings/${seg(itemCode)}`, body);
}

/* ---------------- 설비·가동 ---------------- */
/** GET /equipment — 설비 목록 + 현재 상태. */
export function fetchEquipment(): Promise<EquipmentOut[]> {
  return requestMes<EquipmentOut[]>("/equipment");
}

/** POST /equipment — 등록(quality+). */
export function createEquipment(body: EquipmentIn): Promise<EquipmentOut> {
  return send<EquipmentOut>("POST", "/equipment", body);
}

/** PATCH /equipment/{code} — 수정·사용 중지(quality+). */
export function patchEquipment(code: string, body: EquipmentPatch): Promise<EquipmentOut> {
  return send<EquipmentOut>("PATCH", `/equipment/${seg(code)}`, body);
}

/** POST /equipment/{code}/state — 상태 전환(operator+). DOWN·PM 은 사유 필수. */
export function setEquipmentState(code: string, body: EquipmentStateIn): Promise<EquipmentOut> {
  return send<EquipmentOut>("POST", `/equipment/${seg(code)}/state`, body);
}

/** GET /equipment/{code}/events?limit= — 상태 이력(최근 순). */
export function fetchEquipmentEvents(code: string, limit?: number): Promise<EquipmentEventOut[]> {
  return requestMes<EquipmentEventOut[]>(`/equipment/${seg(code)}/events${toQuery({ limit })}`);
}

/** GET /equipment-utilization?date_from=&date_to= — 가동률(KST 날짜, 비우면 오늘). */
export function fetchUtilization(dateFrom?: string, dateTo?: string): Promise<UtilizationOut> {
  return requestMes<UtilizationOut>(
    `/equipment-utilization${toQuery({ date_from: dateFrom, date_to: dateTo })}`,
  );
}

/* ---------------- 공정 진행 ---------------- */
/** GET /work-orders/{id}/operations — 공정 목록(없으면 서버가 라우팅에서 만든다). */
export function fetchOperations(woId: number): Promise<OperationOut[]> {
  return requestMes<OperationOut[]>(`/work-orders/${woId}/operations`);
}

/** POST …/operations/{op_id}/start — 공정 시작(operator+). */
export function startOperation(woId: number, opId: number, body: OpStartIn = {}): Promise<OperationOut[]> {
  return send<OperationOut[]>("POST", `/work-orders/${woId}/operations/${opId}/start`, body);
}

/** POST …/operations/{op_id}/complete — 공정 완료(operator+). 검사 공정이 아니면 good_qty 필수. */
export function completeOperation(
  woId: number,
  opId: number,
  body: OpCompleteIn = {},
): Promise<OperationOut[]> {
  return send<OperationOut[]>("POST", `/work-orders/${woId}/operations/${opId}/complete`, body);
}

/** POST …/operations/{op_id}/skip — 건너뛰기(quality+). */
export function skipOperation(woId: number, opId: number): Promise<OperationOut[]> {
  return send<OperationOut[]>("POST", `/work-orders/${woId}/operations/${opId}/skip`);
}

/** GET /work-orders/{id}/materials — 자재 소요·투입·현재고·부족. */
export function fetchMaterials(woId: number): Promise<MaterialsOut> {
  return requestMes<MaterialsOut>(`/work-orders/${woId}/materials`);
}

/** POST /work-orders/{id}/materials/issue — 자재 투입(operator+). 재고 부족이면 409. */
export function issueMaterials(woId: number, body: MaterialIssueIn = {}): Promise<MaterialsOut> {
  return send<MaterialsOut>("POST", `/work-orders/${woId}/materials/issue`, body);
}

/* ---------------- 재고 ---------------- */
/** GET /stock?item_type= — 품목별 현재고. */
export function fetchStock(itemType?: ItemType): Promise<StockRow[]> {
  return requestMes<StockRow[]>(`/stock${toQuery({ item_type: itemType })}`);
}

export interface StockTxnQuery {
  item_code?: string;
  wo_id?: number;
  limit?: number;
}

/** GET /stock/txns?item_code=&wo_id=&limit= — 수불 이력(최근 순). */
export function fetchStockTxns(q: StockTxnQuery = {}): Promise<StockTxnOut[]> {
  return requestMes<StockTxnOut[]>(`/stock/txns${toQuery({ ...q })}`);
}

/** POST /stock/receipts — 입고(operator+). 품목 기준정보에 없는 품목은 404. */
export function createReceipt(body: ReceiptIn): Promise<StockTxnOut> {
  return send<StockTxnOut>("POST", "/stock/receipts", body);
}

/** POST /stock/adjustments — 조정(quality+, 부호 있는 수량, 사유 필수, 0 은 422). */
export function createAdjustment(body: AdjustIn): Promise<StockTxnOut> {
  return send<StockTxnOut>("POST", "/stock/adjustments", body);
}
