/** MES 범위 확대분 화면 테스트용 응답 팩토리 — schemas.py "2026-11-15 범위 확대" 모양 그대로. */
import type {
  BomLineOut,
  EquipmentEventOut,
  EquipmentOut,
  ItemOut,
  MaterialLine,
  MaterialsOut,
  OperationOut,
  ProcessOut,
  RoutingOut,
  StockRow,
  StockTxnOut,
  UtilizationOut,
} from "@/api/mesPlant";

export function mesItem(over: Partial<ItemOut> = {}): ItemOut {
  return {
    item_code: "HP12",
    name: "헤더파이프12",
    item_type: "FG",
    unit: "EA",
    safety_stock: 0,
    spec: null,
    active: true,
    created_at: "2026-11-15T00:00:00Z",
    ...over,
  };
}

export function bomLine(over: Partial<BomLineOut> = {}): BomLineOut {
  return {
    parent_code: "HP12",
    component_code: "AL-TUBE",
    component_name: "알루미늄 튜브",
    unit: "M",
    qty_per: 0.25,
    scrap_pct: 2,
    note: null,
    ...over,
  };
}

export function process(over: Partial<ProcessOut> = {}): ProcessOut {
  return { code: "CUT", name: "절단", seq: 10, auto_from_inspection: false, active: true, ...over };
}

export const PROCESSES: ProcessOut[] = [
  process(),
  process({ code: "WASH", name: "세척", seq: 30 }),
  process({ code: "INSP", name: "검사", seq: 40, auto_from_inspection: true }),
];

export function routing(over: Partial<RoutingOut> = {}): RoutingOut {
  return {
    item_code: "*",
    source: "processes",
    steps: [
      { seq: 10, process_code: "CUT", process_name: "절단", equipment_code: null, std_sec_per_ea: null },
      { seq: 20, process_code: "WASH", process_name: "세척", equipment_code: null, std_sec_per_ea: null },
      { seq: 30, process_code: "INSP", process_name: "검사", equipment_code: null, std_sec_per_ea: null },
    ],
    ...over,
  };
}

export function equipment(over: Partial<EquipmentOut> = {}): EquipmentOut {
  return {
    code: "SAW-1",
    name: "절단기 1호",
    process_code: "CUT",
    note: null,
    active: true,
    state: "IDLE",
    state_since: "2026-11-15T00:00:00Z",
    state_reason: null,
    wo_id: null,
    wo_no: null,
    ...over,
  };
}

export function equipmentEvent(over: Partial<EquipmentEventOut> = {}): EquipmentEventOut {
  return {
    id: 1,
    equipment_code: "SAW-1",
    state: "RUN",
    reason: null,
    started_at: "2026-11-15T00:00:00Z",
    ended_at: "2026-11-15T02:05:00Z",
    wo_id: 1,
    created_by: "kim",
    ...over,
  };
}

export function utilization(over: Partial<UtilizationOut> = {}): UtilizationOut {
  return {
    date_from: "2026-11-15",
    date_to: "2026-11-15",
    rows: [
      {
        equipment_code: "SAW-1", name: "절단기 1호", run_s: 21600, idle_s: 3600, down_s: 1800, pm_s: 1800,
        availability_pct: 80, down_count: 2, mttr_min: 15, mtbf_min: 180,
      },
      {
        equipment_code: "WASH-1", name: "세척기", run_s: 0, idle_s: 0, down_s: 0, pm_s: 0,
        availability_pct: null, down_count: 0, mttr_min: null, mtbf_min: null,
      },
    ],
    ...over,
  };
}

export function operation(over: Partial<OperationOut> = {}): OperationOut {
  return {
    id: 11,
    wo_id: 1,
    seq: 10,
    process_code: "CUT",
    process_name: "절단",
    equipment_code: null,
    status: "PENDING",
    started_at: null,
    ended_at: null,
    good_qty: null,
    defect_qty: null,
    worker: null,
    note: null,
    auto: false,
    auto_good_qty: null,
    auto_defect_qty: null,
    ...over,
  };
}

export function materialLine(over: Partial<MaterialLine> = {}): MaterialLine {
  return {
    item_code: "AL-TUBE",
    name: "알루미늄 튜브",
    unit: "M",
    qty_per: 0.25,
    scrap_pct: 2,
    required: 127.5,
    issued: 0,
    on_hand: 200,
    shortage: 0,
    ...over,
  };
}

export function materials(over: Partial<MaterialsOut> = {}): MaterialsOut {
  return { wo_id: 1, plan_qty: 500, lines: [materialLine()], ...over };
}

export function stockRow(over: Partial<StockRow> = {}): StockRow {
  return {
    item_code: "AL-TUBE",
    name: "알루미늄 튜브",
    item_type: "RAW",
    unit: "M",
    on_hand: 200,
    safety_stock: 100,
    below_safety: false,
    last_txn_at: "2026-11-15T01:00:00Z",
    ...over,
  };
}

export function stockTxn(over: Partial<StockTxnOut> = {}): StockTxnOut {
  return {
    id: 1,
    ts: "2026-11-15T01:00:00Z",
    item_code: "AL-TUBE",
    lot: "RM-001",
    qty: 200,
    txn_type: "RECEIPT",
    wo_id: null,
    wo_no: null,
    shipment_id: null,
    note: null,
    created_by: "kim",
    ...over,
  };
}
