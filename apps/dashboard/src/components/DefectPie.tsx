import { Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import type { DefectDist } from "@/lib/stats";

const COLORS: Record<string, string> = {
  LEN: "#2563eb",
  OIL: "#d97706",
  DIS: "#9333ea",
  SCR: "#dc2626",
  MULTI: "#0f766e",
};

/** 불량유형 분포 파이 (Recharts 요약 차트, M11). */
export function DefectPie({ data }: { data: DefectDist[] }): JSX.Element {
  if (data.length === 0) {
    return <Empty label="불량 데이터 없음" />;
  }
  return (
    <div data-testid="defect-pie" style={{ width: "100%", height: 260 }}>
      <ResponsiveContainer>
        <PieChart>
          <Pie
            data={data}
            dataKey="count"
            nameKey="code"
            cx="50%"
            cy="50%"
            outerRadius={90}
            label={(d) => `${d.code} ${d.count}`}
          >
            {data.map((d) => (
              <Cell key={d.code} fill={COLORS[d.code] ?? "#64748b"} />
            ))}
          </Pie>
          <Tooltip />
          <Legend />
        </PieChart>
      </ResponsiveContainer>
    </div>
  );
}

function Empty({ label }: { label: string }): JSX.Element {
  return (
    <div className="flex h-[260px] items-center justify-center text-sm text-slate-400">
      {label}
    </div>
  );
}
