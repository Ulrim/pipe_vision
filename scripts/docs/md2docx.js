// 사용 준비: npm install docx  (루트 워크스페이스와 무관, 문서 변환 전용)
// 예: node scripts/docs/md2docx.js docs/DATA_DEFINITION.md out/AIVIS_AI솔루션_데이터정의서_v1.0.docx --break-h2 --header "AIVIS — AI솔루션 데이터 정의서"
// md2docx.js — Markdown(제한 문법) → DOCX 변환기 (docx-js)
// 지원: #~#### 제목, 문단, **굵게**, `코드`, [텍스트](링크)→텍스트, 글머리(-)/번호(1.) 목록,
//       | 표 |(헤더+구분행), ``` 코드블록 ```, > 인용, --- 구분선, 【확인】 강조.
// 사용: node md2docx.js input.md output.docx [--break-h2] [--header "머리글"] [--font "Malgun Gothic"]
const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, HeadingLevel, Table, TableRow, TableCell, WidthType,
  ShadingType, AlignmentType, BorderStyle, PageBreak, LevelFormat, Header, Footer, PageNumber,
  VerticalAlign,
} = require("docx");

const args = process.argv.slice(2);
const input = args[0], output = args[1];
const opt = { breakH2: args.includes("--break-h2"), header: "", font: "Malgun Gothic" };
for (let i = 2; i < args.length; i++) {
  if (args[i] === "--header") opt.header = args[++i];
  if (args[i] === "--font") opt.font = args[++i];
}
const FONT = opt.font;
const MONO = "Consolas";
const BODY = 20;      // 10pt (half-points)
const TABLE = 17;     // 8.5pt
const CODE = 16;      // 8pt
const PAGE_W = 11906, MARGIN = 1134; // A4, 2.0cm
const CONTENT_W = PAGE_W - 2 * MARGIN; // 9638 DXA

// ---------- 인라인 파서 ----------
function inline(text, base = {}) {
  const runs = [];
  // 토큰: **bold**, `code`, [text](url), 【확인】
  const re = /(\*\*(?:(?!\*\*).)+?\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\)|【확인】)/g;
  let last = 0, m;
  const push = (t, extra = {}) => {
    if (!t) return;
    runs.push(new TextRun({ text: t, font: FONT, size: base.size || BODY, bold: base.bold, color: base.color, ...extra }));
  };
  while ((m = re.exec(text)) !== null) {
    push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("**")) push(tok.slice(2, -2), { bold: true });
    else if (tok.startsWith("`")) runs.push(new TextRun({ text: tok.slice(1, -1), font: MONO, size: (base.size || BODY) - 1, shading: { type: ShadingType.CLEAR, fill: "F1F1F1" } }));
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

function makeTable(rows) {
  const ncol = Math.max(...rows.map(r => r.length));
  const norm = rows.map(r => { const c = r.slice(); while (c.length < ncol) c.push(""); return c; });
  // 열 너비: 내용 길이 가중(하한 6, 상한 60)
  const weights = [];
  for (let c = 0; c < ncol; c++) {
    let mx = 0;
    for (const r of norm) mx = Math.max(mx, Math.min(60, [...r[c]].length));
    weights.push(Math.max(6, mx));
  }
  const sum = weights.reduce((a, b) => a + b, 0);
  const widths = weights.map(w => Math.floor(CONTENT_W * w / sum));
  widths[ncol - 1] += CONTENT_W - widths.reduce((a, b) => a + b, 0);
  const border = { style: BorderStyle.SINGLE, size: 4, color: "999999" };
  const trows = norm.map((r, ri) => new TableRow({
    tableHeader: ri === 0,
    cantSplit: true,
    children: r.map((cell, ci) => new TableCell({
      width: { size: widths[ci], type: WidthType.DXA },
      shading: ri === 0 ? { type: ShadingType.CLEAR, fill: "DCE6F1" } : (ci === 0 && ncol <= 4 ? { type: ShadingType.CLEAR, fill: "F7F7F7" } : undefined),
      verticalAlign: VerticalAlign.CENTER,
      margins: { top: 40, bottom: 40, left: 70, right: 70 },
      children: [new Paragraph({ spacing: { before: 0, after: 0 }, children: inline(cell, { size: TABLE, bold: ri === 0 }) })],
    })),
  }));
  return new Table({
    width: { size: CONTENT_W, type: WidthType.DXA },
    columnWidths: widths,
    borders: { top: border, bottom: border, left: border, right: border, insideHorizontal: border, insideVertical: border },
    rows: trows,
  });
}

function codeBlock(lines) {
  return lines.map(l => new Paragraph({
    spacing: { before: 0, after: 0, line: 240 },
    shading: { type: ShadingType.CLEAR, fill: "F4F4F4" },
    indent: { left: 120, right: 120 },
    children: [new TextRun({ text: l.length ? l : " ", font: MONO, size: CODE })],
  }));
}

const md = fs.readFileSync(input, "utf-8").replace(/\r\n/g, "\n").split("\n");
const children = [];
let i = 0, firstH1 = true, para = [];
const flushPara = () => {
  if (!para.length) return;
  children.push(new Paragraph({ spacing: { before: 60, after: 100, line: 276 }, alignment: AlignmentType.JUSTIFIED, children: inline(para.join(" ")) }));
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
    children.push(new Paragraph({ spacing: { before: 60, after: 0 } }));
    children.push(...codeBlock(buf));
    children.push(new Paragraph({ spacing: { before: 0, after: 100 } }));
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
    if (rows.length) { children.push(makeTable(rows)); children.push(new Paragraph({ spacing: { before: 0, after: 120 } })); }
    continue;
  }
  const h = /^(#{1,4})\s+(.*)$/.exec(t);
  if (h) {
    flushPara();
    const level = h[1].length;
    const text = h[2].trim();
    if (level === 1 && firstH1) {
      firstH1 = false;
      children.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 200, after: 120 }, children: [new TextRun({ text, font: FONT, size: 40, bold: true })] }));
      children.push(new Paragraph({ spacing: { before: 0, after: 200 }, border: { bottom: { style: BorderStyle.SINGLE, size: 12, color: "1F4E79", space: 1 } } }));
    } else {
      if (level === 2 && opt.breakH2 && children.length) children.push(new Paragraph({ children: [new PageBreak()] }));
      const hl = { 1: HeadingLevel.HEADING_1, 2: HeadingLevel.HEADING_1, 3: HeadingLevel.HEADING_2, 4: HeadingLevel.HEADING_3 }[level];
      const size = { 1: 32, 2: 30, 3: 24, 4: 21 }[level];
      children.push(new Paragraph({ heading: hl, spacing: { before: level <= 2 ? 320 : 220, after: 120 }, children: [new TextRun({ text, font: FONT, size, bold: true, color: level <= 2 ? "1F4E79" : "17375E" })] }));
    }
    i++; continue;
  }
  if (t === "---") { flushPara(); children.push(new Paragraph({ spacing: { before: 120, after: 120 }, border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: "BBBBBB", space: 1 } } })); i++; continue; }
  if (t.startsWith(">")) {
    flushPara();
    const buf = [];
    while (i < md.length && md[i].trim().startsWith(">")) { buf.push(md[i].trim().replace(/^>\s?/, "")); i++; }
    children.push(new Paragraph({ indent: { left: 360 }, spacing: { before: 60, after: 120 }, border: { left: { style: BorderStyle.SINGLE, size: 18, color: "9DC3E6", space: 8 } }, children: inline(buf.join(" "), { size: BODY - 1, color: "404040" }) }));
    continue;
  }
  const li = /^(\s*)([-*]|\d+[.)])\s+(.*)$/.exec(line);
  if (li) {
    flushPara();
    const indentLevel = Math.min(2, Math.floor(li[1].replace(/\t/g, "  ").length / 2));
    const numbered = /\d/.test(li[2]);
    children.push(new Paragraph({ numbering: { reference: numbered ? "num" : "bul", level: indentLevel }, spacing: { before: 20, after: 40, line: 264 }, children: inline(li[3]) }));
    i++; continue;
  }
  if (t === "") { flushPara(); i++; continue; }
  para.push(t); i++;
}
flushPara();

const doc = new Document({
  creator: "AIVIS",
  styles: { default: { document: { run: { font: FONT, size: BODY } } } },
  numbering: {
    config: [
      { reference: "bul", levels: [0, 1, 2].map(l => ({ level: l, format: LevelFormat.BULLET, text: ["•", "–", "·"][l], alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 420 + 360 * l, hanging: 240 } } } })) },
      { reference: "num", levels: [0, 1, 2].map(l => ({ level: l, format: LevelFormat.DECIMAL, text: "%" + (l + 1) + ".", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 480 + 360 * l, hanging: 300 } } } })) },
    ],
  },
  sections: [{
    properties: { page: { size: { width: PAGE_W, height: 16838 }, margin: { top: MARGIN, bottom: MARGIN, left: MARGIN, right: MARGIN } } },
    headers: { default: new Header({ children: [new Paragraph({ alignment: AlignmentType.RIGHT, children: [new TextRun({ text: opt.header, font: FONT, size: 16, color: "808080" })] })] }) },
    footers: { default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ children: ["- ", PageNumber.CURRENT, " -"], font: FONT, size: 16, color: "808080" })] })] }) },
    children,
  }],
});

Packer.toBuffer(doc).then(buf => { fs.writeFileSync(output, buf); console.log("written", output, buf.length, "bytes"); });
