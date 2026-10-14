/** MES 화면 테스트용 응답 팩토리 — services/mes/mes_app/schemas.py 모양 그대로. */
import type {
  ClaimOut,
  IntakeStatus,
  MonthlyKpi,
  ProductionOut,
  QualityOut,
  SalesOrderOut,
  ShipmentOut,
  WorkOrderOut,
} from "@/api/mes";

export function salesOrder(over: Partial<SalesOrderOut> = {}): SalesOrderOut {
  return {
    id: 7,
    so_no: "SO-20261014-001",
    customer: "한라공조",
    item_code: "HP12",
    qty: 500,
    order_date: "2026-10-10",
    due_date: "2026-10-20",
    status: "OPEN",
    note: null,
    created_by: "qa",
    created_at: "2026-10-10T00:00:00Z",
    shipped_qty: 0,
    work_order_count: 1,
    ...over,
  };
}

export function workOrder(over: Partial<WorkOrderOut> = {}): WorkOrderOut {
  return {
    id: 1,
    wo_no: "WO-20261014-001",
    so_id: 7,
    so_no: "SO-20261014-001",
    customer: "한라공조",
    item_code: "HP12",
    lot: "L-20261014-01",
    plan_qty: 500,
    plan_date: "2026-10-14",
    status: "PLANNED",
    started_at: null,
    finished_at: null,
    note: null,
    created_by: "qa",
    created_at: "2026-10-14T00:00:00Z",
    good_qty: 120,
    defect_total: 4,
    inspected_rows: 124,
    progress_pct: 24,
    last_at: "2026-10-14T02:00:00Z",
    ...over,
  };
}

export function production(over: Partial<ProductionOut> = {}): ProductionOut {
  return {
    inspected_rows: 250,
    stages: [
      {
        stage: "CUT_LENGTH", label: "길이 검사", inspected: 130, ai_ng: 6, final_ng: 4,
        reviewed: 3, good: 126, by_code: { LEN: 4 },
      },
      {
        stage: "POST_WASH_SURFACE", label: "표면 검사", inspected: 120, ai_ng: 2, final_ng: 3,
        reviewed: 2, good: 117, by_code: { OIL: 2, MANUAL: 1 },
      },
    ],
    defect_total: 7,
    by_code: { LEN: 4, OIL: 2, MANUAL: 1 },
    good_qty: 117,
    good_source: "stage:POST_WASH_SURFACE",
    crate_count: 0,
    crate_tubes: 0,
    crate_mismatch: 0,
    first_at: "2026-10-14T00:10:00Z",
    last_at: "2026-10-14T02:00:00Z",
    ...over,
  };
}

export function shipment(over: Partial<ShipmentOut> = {}): ShipmentOut {
  return {
    id: 31,
    ship_no: "SH-20261014-001",
    so_id: 7,
    so_no: "SO-20261014-001",
    lot: "L-20261014-01",
    item_code: "HP12",
    customer: "한라공조",
    qty: 200,
    ship_date: "2026-10-14",
    note: null,
    created_by: "qa",
    created_at: "2026-10-14T05:00:00Z",
    lead_time_days: 4,
    on_time: true,
    ...over,
  };
}

export function claim(over: Partial<ClaimOut> = {}): ClaimOut {
  return {
    id: 41,
    claim_no: "CL-20261012-001",
    customer: "한라공조",
    lot: "L-20261001-02",
    item_code: "HP12",
    defect_qty: 3,
    defect_type: "SCR",
    occurred_date: "2026-10-12",
    description: "표면 긁힘",
    status: "OPEN",
    closed_at: null,
    created_by: "qa",
    created_at: "2026-10-12T00:00:00Z",
    ...over,
  };
}

export function monthlyKpi(over: Partial<MonthlyKpi> = {}): MonthlyKpi {
  return {
    period: "2026-10",
    shipment_count: 3,
    shipped_qty: 1200,
    lead_time_days: 4.5,
    lead_time_samples: 2,
    on_time_rate_pct: 50,
    on_time_samples: 2,
    claim_count: 1,
    leak_defect_qty: 3,
    leak_ppm: 2500,
    ...over,
  };
}

export function intakeStatus(over: Partial<IntakeStatus> = {}): IntakeStatus {
  return {
    table_enabled: true,
    poller_running: true,
    poll_interval_s: 10,
    last_poll_at: new Date().toISOString(),
    last_result: { scanned: 4, stored: 4, updated: 0, duplicate: 0, marked: 4, error: null },
    interface_pending: 0,
    interface_error: null,
    rest_token_required: false,
    records_total: 1520,
    records_today: 88,
    last_received_at: new Date().toISOString(),
    runs: [
      { ts: "2026-10-14T02:00:00Z", source: "table", received: 4, updated: 0, error: null },
      { ts: "2026-10-14T01:59:50Z", source: "rest", received: 0, updated: 1, error: "timeout" },
    ],
    ...over,
  };
}

export function qualityRecord(over: Partial<QualityOut> = {}): QualityOut {
  return {
    id: 901,
    idem_key: "L1|HP12|2026-10-14T02:00:00Z|PI-CAM1|2",
    inspection_id: 5501,
    lot: "L-20261014-01",
    work_order: "WO-20261014-001",
    item_code: "HP12",
    cam_id: "PI-CAM1",
    inspection_stage: "CUT_LENGTH",
    tube_index: 2,
    inspected_at: "2026-10-14T02:00:00Z",
    final_verdict: "NG",
    defect_codes: ["LEN"],
    meas_length_mm: 250.18,
    deviation_mm: 0.18,
    manual_verdict: null,
    count_detected: null,
    count_expected: null,
    source: "table",
    received_at: "2026-10-14T02:00:05Z",
    updated_at: null,
    ...over,
  };
}
