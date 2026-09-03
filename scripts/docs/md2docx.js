// 사용 준비: npm install docx  (루트 워크스페이스와 무관, 문서 변환 전용)
// 예: node scripts/docs/md2docx.js docs/DATA_DEFINITION.md out/AIVIS_AI솔루션_데이터정의서_v1.0.docx --break-h2 --header "AIVIS — AI솔루션 데이터 정의서"
// md2docx.js — Markdown(제한 문법) → DOCX 변환기 (docx-js)
// 지원: #~#### 제목, 문단, **굵게**, `코드`, [텍스트](링크)→텍스트, 글머리(-)/번호(1.) 목록,
//       | 표 |(헤더+구분행), ``` 코드블록 ```, > 인용, --- 구분선, 【확인】 강조.
// 레이아웃: A4, 여백 상하좌우 20mm(전남TP 양식과 동일). 표·코드·인용은 본문과 같은 좌우 폭.
//           열이 6개 이상인 넓은 표는 양식처럼 가로(landscape) 구간에 배치한다.
// 글꼴: 맑은 고딕(영문 Malgun Gothic / 한글 맑은 고딕) 단일. --font, --font-ea 로 변경 가능.
// 사용: node md2docx.js input.md output.docx [--break-h2] [--header "머리글"] [--font "Malgun Gothic"] [--font-ea "맑은 고딕"]
const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, HeadingLevel, Table, TableRow, TableCell, WidthType,
  ShadingType, AlignmentType, BorderStyle, PageBreak, LevelFormat, Header, Footer, PageNumber,
  VerticalAlign, TableLayoutType, PageOrientation,
} = require("docx");

const args = process.argv.slice(2);
const input = args[0], output = args[1];
const opt = { breakH2: args.includes("--break-h2"), header: "", font: "Malgun Gothic", fontEa: "맑은 고딕" };
for (let i = 2; i < args.length; i++) {
  if (args[i] === "--header") opt.header = args[++i];
  if (args[i] === "--font") { opt.font = args[++i]; opt.fontEa = opt.font; }
  if (args[i] === "--font-ea") opt.fontEa = args[++i];
}
// 모든 런에 동일 적용: 영문/숫자(ascii, hAnsi), 한글(eastAsia), 기타(cs)
const FONTS = { ascii: opt.font, hAnsi: opt.font, eastAsia: opt.fontEa, cs: opt.font };
const LANG = { value: "ko-KR", eastAsia: "ko-KR" };
const BODY = 20;      // 10pt (half-points)
const TABLE = 17;     // 8.5pt
const CODE = 17;      // 8.5pt
const A4_W = 11906, A4_H = 16838;
const MARGIN = 1134;                 // 20mm
const HEADER_DIST = 567;             // 10mm
const CELL_PAD = 70;                 // 표 셀 좌우 안쪽 여백(DXA)
const WIDE_COLS = 6;                 // 이 이상이면 가로 구간
const CONTENT_W = { portrait: A4_W - 2 * MARGIN, landscape: A4_H - 2 * MARGIN }; // 9638 / 14570

const run = (text, extra = {}) => new TextRun({ text, font: FONTS, language: LANG, size: BODY, ...extra });

// ---------- 인라인 파서 ----------
function inline(text, base = {}) {
  const runs = [];
  const re = /(\*\*(?:(?!\*\*).)+?\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\)|【확인】)/g;
  let last = 0, m;
  const size = base.size || BODY;
  const push = (t, extra = {}) => { if (t) runs.push(run(t, { size, bold: base.bold, color: base.color, ...extra })); };
  while ((m = re.exec(text)) !== null) {
    push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("**")) push(tok.slice(2, -2), { bold: true });
    else if (tok.startsWith("`")) push(tok.slice(1, -1), { shading: { type: ShadingType.CLEAR, fill: "EDEDED" } });
    else if (tok.startsWith("[")) push(tok.slice(1, tok.indexOf("]")), { color: "1F4E79" });
    else if (tok === "【확인】") push("【확인】", { bold: true, color: "C00000" });
    last = m.index + tok.length;
  }
  push(text.slice(last));
  return runs;
}

// ---------- 블록 파서 ----------
function splitCells(line) {
  const s = line.trim().replace(/^\|/, "").replace(/\|$/, "");
  const cells = []; let cur = "";
  for (let i = 0; i < s.length; i++) {
    if (s[i] === "\\" && s[i + 1] === "|") { cur += "|"; i++; continue; }
    if (s[i] === "|") { cells.push(cur.trim()); cur = ""; continue; }
    cur += s[i];
  }
  cells.push(cur.trim());
  return cells;
}

function makeTable(rows, contentW) {
  const ncol = Math.max(...rows.map(r => r.length));
  const norm = rows.map(r => { const c = r.slice(); while (c.length < ncol) c.push(""); return c; });
  // 열 너비: 내용 길이 가중(하한 6, 상한 60). 합계 = 본문 폭(좌우 여백과 일치)
  const weights = [];
  for (let c = 0; c < ncol; c++) {
    let mx = 0;
    for (const r of norm) mx = Math.max(mx, Math.min(60, [...r[c]].length));
    weights.push(Math.max(6, mx));
  }
  const sum = weights.reduce((a, b) => a + b, 0);
  const widths = weights.map(w => Math.floor(contentW * w / sum));
  widths[ncol - 1] += contentW - widths.reduce((a, b) => a + b, 0);
  const border = { style: BorderStyle.SINGLE, size: 4, color: "999999" };
  const trows = norm.map((r, ri) => new TableRow({
    tableHeader: ri === 0,
    cantSplit: true,
    children: r.map((cell, ci) => new TableCell({
      width: { size: widths[ci], type: WidthType.DXA },
      shading: ri === 0 ? { type: ShadingType.CLEAR, fill: "DCE6F1" } : (ci === 0 && ncol <= 4 ? { type: ShadingType.CLEAR, fill: "F7F7F7" } : undefined),
      verticalAlign: VerticalAlign.CENTER,
      margins: { top: 40, bottom: 40, left: CELL_PAD, right: CELL_PAD },
      children: [new Paragraph({ spacing: { before: 0, after: 0 }, children: inline(cell, { size: TABLE, bold: ri === 0 }) })],
    })),
  }));
  return new Table({
    width: { size: contentW, type: WidthType.DXA },
    columnWidths: widths,
    layout: TableLayoutType.FIXED,
    // Word 는 셀 안쪽 여백만큼 표를 왼쪽으로 내보내므로, 그만큼 들여써서 표 테두리를 본문 좌우 여백선에 맞춘다.
    indent: { size: CELL_PAD, type: WidthType.DXA },
    borders: { top: border, bottom: border, left: border, right: border, insideHorizontal: border, insideVertical: border },
    rows: trows,
  });
}

function codeBlock(lines) {
  return lines.map(l => new Paragraph({
    spacing: { before: 0, after: 0, line: 240 },
    shading: { type: ShadingType.CLEAR, fill: "F4F4F4" },
    children: [run(l.length ? l : " ", { size: CODE })],
  }));
}

// ---------- 문서 → 블록 목록 ----------
// 각 블록: { kind: "p"|"h"|"table"|"code"|"quote"|"hr"|"list", wide?: bool, els: [docx 요소...] }
const md = fs.readFileSync(input, "utf-8").replace(/\r\n/g, "\n").split("\n");
const blocks = [];
let i = 0, firstH1 = true, para = [];
const flushPara = () => {
  if (!para.length) return;
  blocks.push({ kind: "p", els: [new Paragraph({ spacing: { before: 60, after: 100, line: 276 }, alignment: AlignmentType.JUSTIFIED, children: inline(para.join(" ")) })] });
  para = [];
};
while (i < md.length) {
  const line = md[i];
  const t = line.trim();
  if (t.startsWith("```")) {
    flushPara();
    const buf = []; i++;
    while (i < md.length && !md[i].trim().startsWith("```")) { buf.push(md[i]); i++; }
    i++;
    blocks.push({ kind: "code", els: [new Paragraph({ spacing: { before: 60, after: 0 } }), ...codeBlock(buf), new Paragraph({ spacing: { before: 0, after: 100 } })] });
    continue;
  }
  if (t.startsWith("|")) {
    flushPara();
    const rows = [];
    while (i < md.length && md[i].trim().startsWith("|")) {
      const cells = splitCells(md[i]);
      if (!cells.every(c => /^:?-{2,}:?$/.test(c))) rows.push(cells);
      i++;
    }
    if (rows.length) {
      const ncol = Math.max(...rows.map(r => r.length));
      const wide = ncol >= WIDE_COLS;
      blocks.push({ kind: "table", wide, els: [makeTable(rows, wide ? CONTENT_W.landscape : CONTENT_W.portrait), new Paragraph({ spacing: { before: 0, after: 120 } })] });
    }
    continue;
  }
  const h = /^(#{1,4})\s+(.*)$/.exec(t);
  if (h) {
    flushPara();
    const level = h[1].length;
    const text = h[2].trim();
    if (level === 1 && firstH1) {
      firstH1 = false;
      blocks.push({ kind: "title", els: [
        new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 200, after: 120 }, children: [run(text, { size: 40, bold: true })] }),
        new Paragraph({ spacing: { before: 0, after: 200 }, border: { bottom: { style: BorderStyle.SINGLE, size: 12, color: "1F4E79", space: 1 } } }),
      ] });
    } else {
      const els = [];
      const hasBreak = level === 2 && opt.breakH2 && blocks.length > 0;
      if (hasBreak) els.push(new Paragraph({ children: [new PageBreak()] }));
      const hl = { 1: HeadingLevel.HEADING_1, 2: HeadingLevel.HEADING_1, 3: HeadingLevel.HEADING_2, 4: HeadingLevel.HEADING_3 }[level];
      const size = { 1: 32, 2: 30, 3: 24, 4: 21 }[level];
      els.push(new Paragraph({ heading: hl, spacing: { before: level <= 2 ? 320 : 220, after: 120 }, children: [run(text, { size, bold: true, color: level <= 2 ? "1F4E79" : "17375E" })] }));
      blocks.push({ kind: "h", level, hasBreak, els });
    }
    i++; continue;
  }
  if (t === "---") { flushPara(); blocks.push({ kind: "hr", els: [new Paragraph({ spacing: { before: 120, after: 120 }, border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: "BBBBBB", space: 1 } } })] }); i++; continue; }
  if (t.startsWith(">")) {
    flushPara();
    const buf = [];
    while (i < md.length && md[i].trim().startsWith(">")) { buf.push(md[i].trim().replace(/^>\s?/, "")); i++; }
    blocks.push({ kind: "quote", els: [new Paragraph({ spacing: { before: 60, after: 120, line: 264 }, shading: { type: ShadingType.CLEAR, fill: "EEF3F8" }, alignment: AlignmentType.JUSTIFIED, children: inline(buf.join(" "), { size: BODY - 1, color: "404040" }) })] });
    continue;
  }
  const li = /^(\s*)([-*]|\d+[.)])\s+(.*)$/.exec(line);
  if (li) {
    flushPara();
    const indentLevel = Math.min(2, Math.floor(li[1].replace(/\t/g, "  ").length / 2));
    const numbered = /\d/.test(li[2]);
    blocks.push({ kind: "list", els: [new Paragraph({ numbering: { reference: numbered ? "num" : "bul", level: indentLevel }, spacing: { before: 20, after: 40, line: 264 }, children: inline(li[3]) })] });
    i++; continue;
  }
  if (t === "") { flushPara(); i++; continue; }
  para.push(t); i++;
}
flushPara();

// ---------- 구간(세로/가로) 배치 ----------
// 넓은 표는 가로 구간으로. 표 바로 앞의 안내문·제목(표 이후 처음 나온 문단들)은 표와 함께 옮긴다.
// 가로 구간은 다음 제목 또는 일반 표가 나오면 세로로 돌아온다.
const sections = [];
let cur = { orient: "portrait", blocks: [] };
const startSection = (orient) => { if (cur.blocks.length) sections.push(cur); cur = { orient, blocks: [] }; };
for (const b of blocks) {
  if (b.kind === "table" && b.wide) {
    if (cur.orient !== "landscape") {
      // 앞선 리드인(마지막 표/코드/구분선 이후의 제목·문단, 최대 4개)을 떼어 가로 구간으로
      const lead = [];
      while (cur.blocks.length && lead.length < 4 && ["h", "p", "list"].includes(cur.blocks[cur.blocks.length - 1].kind)) lead.unshift(cur.blocks.pop());
      // 직전 구간이 가로이고 지금 세로 구간이 비었으면(제목만 있다가 리드인으로 빠짐) 가로 구간을 이어 쓴다.
      if (!cur.blocks.length && sections.length && sections[sections.length - 1].orient === "landscape") cur = sections.pop();
      else startSection("landscape");
      cur.blocks.push(...lead);
    }
    cur.blocks.push(b);
    continue;
  }
  if (cur.orient === "landscape" && (b.kind === "h" || b.kind === "table" || b.kind === "hr")) startSection("portrait");
  cur.blocks.push(b);
}
if (cur.blocks.length) sections.push(cur);

const headerOf = () => new Header({ children: [new Paragraph({ alignment: AlignmentType.RIGHT, children: [run(opt.header, { size: 16, color: "808080" })] })] });
const footerOf = () => new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ children: ["- ", PageNumber.CURRENT, " -"], font: FONTS, language: LANG, size: 16, color: "808080" })] })] });
const pageOf = (orient) => orient === "landscape"
  ? { size: { width: A4_W, height: A4_H, orientation: PageOrientation.LANDSCAPE }, margin: { top: MARGIN, bottom: MARGIN, left: MARGIN, right: MARGIN, header: HEADER_DIST, footer: HEADER_DIST } }
  : { size: { width: A4_W, height: A4_H, orientation: PageOrientation.PORTRAIT }, margin: { top: MARGIN, bottom: MARGIN, left: MARGIN, right: MARGIN, header: HEADER_DIST, footer: HEADER_DIST } };

const doc = new Document({
  creator: "AIVIS",
  styles: {
    default: {
      document: { run: { font: FONTS, size: BODY, language: LANG } },
      heading1: { run: { font: FONTS } }, heading2: { run: { font: FONTS } }, heading3: { run: { font: FONTS } },
    },
  },
  numbering: {
    config: [
      { reference: "bul", levels: [0, 1, 2].map(l => ({ level: l, format: LevelFormat.BULLET, text: ["•", "–", "·"][l], alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 420 + 360 * l, hanging: 240 } }, run: { font: FONTS } } })) },
      { reference: "num", levels: [0, 1, 2].map(l => ({ level: l, format: LevelFormat.DECIMAL, text: "%" + (l + 1) + ".", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 480 + 360 * l, hanging: 300 } }, run: { font: FONTS } } })) },
    ],
  },
  sections: sections.map(s => {
    // 세로 구간 첫 블록이 "## 페이지 나눔" 이면 구간 전환이 이미 새 페이지이므로 중복 나눔 제거
    const els = s.blocks.flatMap((b, bi) => (bi === 0 && b.kind === "h" && b.hasBreak) ? b.els.slice(1) : b.els);
    return { properties: { page: pageOf(s.orient) }, headers: { default: headerOf() }, footers: { default: footerOf() }, children: els };
  }),
});

Packer.toBuffer(doc).then(buf => {
  fs.writeFileSync(output, buf);
  console.log("written", output, buf.length, "bytes;", "sections:", sections.map(s => s.orient + "(" + s.blocks.length + ")").join(" "));
});
