'use strict'
/**
 * Headless Chrome で定型書式 HTML を PDF 化し、1/3/10 枚のページ数と MediaBox を検証する。
 * 使い方: node tests/measure_fixed_label_pdf.js
 */
const assert = require('assert')
const fs = require('fs')
const os = require('os')
const path = require('path')
const { spawnSync } = require('child_process')
const { fixedLabelPreviewHtml } = require(path.join(__dirname, '../fixed_label.js'))

const PT_PER_MM = 72 / 25.4
const A4_LANDSCAPE_W_PT = 297 * PT_PER_MM
const A4_LANDSCAPE_H_PT = 210 * PT_PER_MM
const TOL_PT = 1.5
const FORMAT_ID = 'daruma-zenibako'

function findChrome() {
  const candidates = [
    process.env.CHROME_PATH,
    '/usr/bin/google-chrome-stable',
    '/usr/bin/google-chrome',
    '/usr/local/bin/google-chrome',
  ].filter(Boolean)
  for (const c of candidates) {
    if (fs.existsSync(c)) return c
  }
  throw new Error('Chrome not found')
}

function printHtmlToPdf(html, pdfPath) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'fixed-label-'))
  const htmlPath = path.join(dir, 'fixed.html')
  fs.writeFileSync(htmlPath, html, 'utf8')
  const chrome = findChrome()
  const r = spawnSync(
    chrome,
    [
      '--headless=new',
      '--disable-gpu',
      '--no-sandbox',
      '--allow-file-access-from-files',
      '--no-pdf-header-footer',
      `--print-to-pdf=${pdfPath}`,
      `file://${htmlPath}`,
    ],
    { encoding: 'utf8', timeout: 90000 }
  )
  if (r.status !== 0 || !fs.existsSync(pdfPath)) {
    throw new Error(`Chrome print failed: ${r.stderr || r.stdout || r.status}`)
  }
  return pdfPath
}

function inspectPdf(pdfPath) {
  const buf = fs.readFileSync(pdfPath)
  const text = buf.toString('latin1')
  const boxes = [...text.matchAll(/\/MediaBox\s*\[\s*([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)\s*\]/g)].map(
    (m) => ({
      x0: +m[1],
      y0: +m[2],
      x1: +m[3],
      y1: +m[4],
      w: +m[3] - +m[1],
      h: +m[4] - +m[2],
    })
  )
  const pageCount = (text.match(/\/Type\s*\/Page(?![sA-Za-z])/g) || []).length
  return { pageCount, boxes, bytes: buf.length }
}

function assertA4Landscape(box, label) {
  const wOk = Math.abs(box.w - A4_LANDSCAPE_W_PT) < TOL_PT
  const hOk = Math.abs(box.h - A4_LANDSCAPE_H_PT) < TOL_PT
  assert.ok(
    wOk && hOk,
    `${label}: MediaBox ${box.w.toFixed(2)}×${box.h.toFixed(2)} pt ` +
      `(${(box.w / PT_PER_MM).toFixed(2)}×${(box.h / PT_PER_MM).toFixed(2)} mm) ` +
      `expected ≈297×210 mm`
  )
}

function dumpDomMetrics(html) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'fixed-label-dom-'))
  const htmlPath = path.join(dir, 'fixed.html')
  const outPath = path.join(dir, 'metrics.json')
  const probe = html.replace(
    '</body>',
    `<pre id="metrics"></pre><script>
      const page=document.querySelector('.page');
      const cells=[...document.querySelectorAll('.cell')];
      const mm=v=>+(v*25.4/96).toFixed(2);
      const r=page.getBoundingClientRect();
      const out={
        pagePx:{w:r.width,h:r.height},
        pageMm:{w:mm(r.width),h:mm(r.height)},
        cellCount:cells.length,
        cellsMm:cells.map(c=>{const b=c.getBoundingClientRect();return {w:mm(b.width),h:mm(b.height)}}),
        titles:[...document.querySelectorAll('.title')].map(el=>el.textContent),
        checksOnFirst:[...cells[0].querySelectorAll('.check')].map(el=>el.textContent.trim()),
      };
      document.getElementById('metrics').textContent=JSON.stringify(out);
    </script></body>`
  )
  fs.writeFileSync(htmlPath, probe, 'utf8')
  const chrome = findChrome()
  const r = spawnSync(
    chrome,
    [
      '--headless=new',
      '--disable-gpu',
      '--no-sandbox',
      '--allow-file-access-from-files',
      '--virtual-time-budget=3000',
      '--dump-dom',
      `file://${htmlPath}`,
    ],
    { encoding: 'utf8', timeout: 60000, maxBuffer: 20 * 1024 * 1024 }
  )
  if (r.status !== 0) throw new Error(`dump-dom failed: ${r.stderr || r.status}`)
  const m = r.stdout.match(/<pre id="metrics">([\s\S]*?)<\/pre>/)
  if (!m) throw new Error('metrics block missing')
  const raw = m[1]
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&')
  const json = JSON.parse(raw)
  fs.writeFileSync(outPath, JSON.stringify(json, null, 2))
  return json
}

function measure(sheets) {
  const html = fixedLabelPreviewHtml(FORMAT_ID, sheets)
  assert.strictEqual((html.match(/class="page"/g) || []).length, sheets)
  assert.strictEqual((html.match(/class="cell"/g) || []).length, sheets * 4)
  const outDir = path.join(__dirname, '../artifacts')
  fs.mkdirSync(outDir, { recursive: true })
  const pdfPath = path.join(outDir, `fixed-label-${sheets}.pdf`)
  printHtmlToPdf(html, pdfPath)
  const info = inspectPdf(pdfPath)
  assert.strictEqual(
    info.pageCount,
    sheets,
    `${sheets} sheets: expected ${sheets} PDF pages, got ${info.pageCount}`
  )
  assert.ok(info.boxes.length >= 1, `${sheets} sheets: no MediaBox`)
  assertA4Landscape(info.boxes[0], `${sheets} sheets`)
  const widthMm = +(info.boxes[0].w / PT_PER_MM).toFixed(2)
  const heightMm = +(info.boxes[0].h / PT_PER_MM).toFixed(2)
  return {
    sheets,
    pageCount: info.pageCount,
    cellsPerPage: 4,
    widthMm: Math.round(widthMm),
    heightMm: Math.round(heightMm),
    pdf: pdfPath,
  }
}

const results = [1, 3, 10].map(measure)
const dom = dumpDomMetrics(fixedLabelPreviewHtml(FORMAT_ID, 1))
assert.strictEqual(dom.cellCount, 4)
assert.ok(Math.abs(dom.pageMm.w - 297) < 2, `page width mm ${dom.pageMm.w}`)
assert.ok(Math.abs(dom.pageMm.h - 210) < 2, `page height mm ${dom.pageMm.h}`)
for (const c of dom.cellsMm) {
  assert.ok(Math.abs(c.w - 148.5) < 2, `cell w ${c.w}`)
  assert.ok(Math.abs(c.h - 105) < 2, `cell h ${c.h}`)
}
assert.ok(dom.checksOnFirst.length === 13)
assert.ok(dom.titles.every((t) => t === 'だるま食品銭函工場'))

console.log(
  JSON.stringify(
    {
      ok: true,
      results,
      dom: {
        pageMm: dom.pageMm,
        cellsMm: dom.cellsMm,
        checkCount: dom.checksOnFirst.length,
        checksOnFirst: dom.checksOnFirst,
      },
    },
    null,
    2
  )
)
