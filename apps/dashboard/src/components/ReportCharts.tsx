/**
 * 월간 리포트 미리보기용 차트 (M12 "월별 추이 시각화").
 *
 * PDF 리포트와 같은 그림을 화면에서 먼저 확인하기 위한 것이므로 형태·색을
 * PDF(core/report.py)와 맞춘다:
 * - 단일 시리즈이므로 카테고리 슬롯 1 색 하나만 쓴다(범례 없음 — 제목이 계열을 지칭).
 * - 불량유형 막대는 명목 카테고리라 **모든 막대 같은 색**. 값이 클수록 진하게
 *   칠하는 값-램프는 막대 길이가 이미 보여주는 정보를 색으로 중복 인코딩한다.
 * - 목표선은 점선 + 문자 라벨을 함께 달아 색 없이도 의미가 전달되게 한다.
 * - 축/격자는 뒤로 물리고(연한 회색) 문자는 텍스트 잉크를 쓴다(시리즈 색을 글자에 쓰지 않음).
 */
import {
  Bar,
  BarChart,
  CartesianGrid,
  LabelList,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { ReportDaily, ReportDefect } from "@/api/endpoints";

/** 카테고리 슬롯 1(단일 시리즈 색) — 대비/색각 검증 통과 값. */
export const SERIES_1 = "#2a78d6";
const AXIS_INK = "#52514e";
const GRID_INK = "#d8d7d3";
const CRITICAL = "#d03b3b";

/** §1.1 공정불량률 목표(ppm). PDF 리포트와 동일 기준. */
export const PPM_TARGET = 600;

function Empty({ label }: { label: string }): JSX.Element {
  return (
    <div className="flex h-[240px] items-center justify-center text-sm text-slate-400">
      {label}
    </div>
  );
}

/** 일자별 공정불량률(ppm) 추세 + 목표선(600ppm). */
export function DailyPpmTrend({ data }: { data: ReportDaily[] }): JSX.Element {
  if (data.length === 0) return <Empty label="해당 기간 검사 데이터 없음" />;
  const rows = data.map((d) => ({ ...d, day: d.date.slice(8) }));
  return (
    <div data-testid="daily-ppm-trend" style={{ width: "100%", height: 240 }}>
      <ResponsiveContainer>
        <LineChart data={rows} margin={{ top: 8, right: 16, bottom: 4, left: 4 }}>
          <CartesianGrid stroke={GRID_INK} strokeDasharray="3 3" vertical={false} />
          <XAxis
            dataKey="day"
            tick={{ fontSize: 11, fill: AXIS_INK }}
            stroke={GRID_INK}
            interval="preserveStartEnd"
            minTickGap={18}
          />
          <YAxis
            tick={{ fontSize: 11, fill: AXIS_INK }}
            stroke={GRID_INK}
            width={56}
          />
          <Tooltip
            formatter={(v: number) => [`${Math.round(v).toLocaleString()} ppm`, "공정불량률"]}
            labelFormatter={(d) => `${d}일`}
          />
          <ReferenceLine
            y={PPM_TARGET}
            stroke={CRITICAL}
            strokeDasharray="4 3"
            label={{
              value: `목표 ${PPM_TARGET}ppm`,
              position: "insideTopLeft",
              fontSize: 11,
              fill: AXIS_INK,
            }}
          />
          <Line
            type="monotone"
            dataKey="ppm"
            stroke={SERIES_1}
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 4 }}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

/** 불량유형별 건수 막대(단일 색 + 직접 값 라벨). */
export function DefectBar({ data }: { data: ReportDefect[] }): JSX.Element {
  if (data.length === 0) return <Empty label="불량 데이터 없음" />;
  return (
    <div data-testid="defect-bar" style={{ width: "100%", height: 240 }}>
      <ResponsiveContainer>
        <BarChart data={data} margin={{ top: 20, right: 16, bottom: 4, left: 4 }}>
          <CartesianGrid stroke={GRID_INK} strokeDasharray="3 3" vertical={false} />
          <XAxis
            dataKey="label"
            tick={{ fontSize: 11, fill: AXIS_INK }}
            stroke={GRID_INK}
          />
          <YAxis tick={{ fontSize: 11, fill: AXIS_INK }} stroke={GRID_INK} width={44} />
          <Tooltip formatter={(v: number) => [`${v}건`, "건수"]} />
          <Bar dataKey="count" fill={SERIES_1} radius={[4, 4, 0, 0]} isAnimationActive={false}>
            <LabelList
              dataKey="count"
              position="top"
              style={{ fontSize: 11, fill: AXIS_INK }}
            />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
