'use strict'
/**
 * Headless Chrome で手書き宛名 HTML を PDF 化し、ページ数・MediaBox・DOM を検証する。
 * 使い方: node tests/measure_handwrite_label_pdf.js
 */
const assert = require('assert')
const fs = require('fs')
const os = require('os')
const path = require('path')
const { spawnSync } = require('child_process')
const { handwriteLabelPreviewHtml } = require(path.join(__dirname, '../handwrite_label.js'))

const PT_PER_MM = 72 / 25.4
const A4_LANDSCAPE_W_PT = 297 * PT_PER_MM
const A4_LANDSCAPE_H_PT = 210 * PT_PER_MM
const TOL_PT = 1.5
const CUSTOMER = '日総 らくら砂川'

function findChrome() {
  const candidates = [
    process.env.CHROME_PATH,
    '/usr/bin/google-chrome-stable',
    '/usr/bin/google-chrome',
    '/usr/local/bin/google-chrome',
    '/usr/bin/chromium-browser',
    '/usr/bin/chromium',
  ].filter(Boolean)
  for (const c of candidates) {
    if (fs.existsSync(c)) return c
  }
  throw new Error('Chrome not found')
}

function materializePreview(customerName, sheets) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'handwrite-label-'))
  const html = handwriteLabelPreviewHtml(customerName, sheets)
  const htmlPath = path.join(dir, 'handwrite.html')
  fs.writeFileSync(htmlPath, html, 'utf8')
  return { dir, htmlPath, html }
}

function printHtmlToPdf(htmlPath, pdfPath) {
  const chrome = findChrome()
  const r = spawnSync(
    chrome,
    [
      '--headless=new',
      '--disable-gpu',
      '--no-sandbox',
      '--allow-file-access-from-files',
      '--window-size=1400,1000',
      '--no-pdf-header-footer',
      `--print-to-pdf=${pdfPath}`,
      `file://${htmlPath}`,
    ],
    { encoding: 'utf8', timeout: 120000 }
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

function dumpDomMetrics(htmlPath) {
  const chrome = findChrome()
  const probePath = htmlPath.replace(/\.html$/, '.probe.html')
  const original = fs.readFileSync(htmlPath, 'utf8')
  const probe = original.replace(
    '</body>',
    `<pre id="metrics"></pre><script>
      const mm=v=>+(v*25.4/96).toFixed(2);
      function collect(){
        const page=document.querySelector('.page');
        const cells=[...document.querySelectorAll('.cell')];
        const r=page.getBoundingClientRect();
        return {
          pageMm:{w:mm(r.width),h:mm(r.height)},
          cellCount:cells.length,
          cellsMm:cells.map(c=>{const b=c.getBoundingClientRect();return {w:mm(b.width),h:mm(b.height)}}),
          customers:[...document.querySelectorAll('.customer')].map(el=>el.textContent),
          samas:[...document.querySelectorAll('.sama')].map(el=>el.textContent),
          guideCount:document.querySelectorAll('.guide').length,
          handAreaText:[...document.querySelectorAll('.hand-area')].map(el=>el.textContent.trim()),
        };
      }
      document.getElementById('metrics').textContent=JSON.stringify(collect());
    </script></body>`
  )
  fs.writeFileSync(probePath, probe, 'utf8')
  const r = spawnSync(
    chrome,
    [
      '--headless=new',
      '--disable-gpu',
      '--no-sandbox',
      '--allow-file-access-from-files',
      '--window-size=1400,1000',
      '--virtual-time-budget=8000',
      '--dump-dom',
      `file://${probePath}`,
    ],
    { encoding: 'utf8', timeout: 90000, maxBuffer: 20 * 1024 * 1024 }
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
  return JSON.parse(raw)
}

function measure(sheets) {
  const { htmlPath, html } = materializePreview(CUSTOMER, sheets)
  assert.strictEqual((html.match(/class="page"/g) || []).length, sheets)
  assert.strictEqual((html.match(/class="cell"/g) || []).length, sheets * 4)
  const outDir = path.join(__dirname, '../artifacts')
  fs.mkdirSync(outDir, { recursive: true })
  const pdfPath = path.join(outDir, `handwrite-label-${sheets}.pdf`)
  printHtmlToPdf(htmlPath, pdfPath)
  const info = inspectPdf(pdfPath)
  assert.strictEqual(info.pageCount, sheets, `${sheets} sheets: expected ${sheets} PDF pages, got ${info.pageCount}`)
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
    pdfBytes: info.bytes,
    pdf: pdfPath,
    hasCustomerName: html.includes(CUSTOMER),
    hasSama: (html.match(/>様</g) || []).length === sheets * 4,
    personalNameBlank: ![...html.matchAll(/class="hand-area"[^>]*>([\s\S]*?)<\/div>/g)].some((m) =>
      m[1].replace(/<[^>]+>/g, '').replace(/様/g, '').trim()
    ),
  }
}

const results = [1, 3, 10].map((n) => measure(n))
const mat = materializePreview(CUSTOMER, 1)
const dom = dumpDomMetrics(mat.htmlPath)
assert.strictEqual(dom.cellCount, 4)
assert.ok(Math.abs(dom.pageMm.w - 297) < 2, `page width mm ${dom.pageMm.w}`)
assert.ok(Math.abs(dom.pageMm.h - 210) < 2, `page height mm ${dom.pageMm.h}`)
for (const c of dom.cellsMm) {
  assert.ok(Math.abs(c.w - 148.5) < 2, `cell w ${c.w}`)
  assert.ok(Math.abs(c.h - 105) < 2, `cell h ${c.h}`)
}
assert.ok(dom.customers.every((t) => t === CUSTOMER))
assert.ok(dom.samas.every((t) => t === '様'))
assert.strictEqual(dom.guideCount, 4)
assert.ok(dom.handAreaText.every((t) => t === '様'))

console.log(
  JSON.stringify(
    {
      ok: true,
      results,
      dom: {
        pageMm: dom.pageMm,
        cellsMm: dom.cellsMm,
        customers: dom.customers,
        samas: dom.samas,
        guideCount: dom.guideCount,
        handAreaText: dom.handAreaText,
      },
      sampleRotation: {
        sourcePageRotate: 180,
        cssReRotate: false,
        note: '見本PDFは/Rotate 180。HTML再現は正立レイアウトのみ。二重回転なし。',
      },
    },
    null,
    2
  )
)
