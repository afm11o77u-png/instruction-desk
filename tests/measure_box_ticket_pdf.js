'use strict'
/**
 * Headless Chrome で管理票 HTML を PDF 化し、ページ数と MediaBox（mm）を検証する。
 * 使い方: node tests/measure_box_ticket_pdf.js
 */
const assert = require('assert')
const fs = require('fs')
const os = require('os')
const path = require('path')
const { spawnSync } = require('child_process')
const {
  boxTicketPages,
  boxTicketPreviewHtml,
} = require(path.join(__dirname, '../box_ticket.js'))

const PT_PER_MM = 72 / 25.4
const A4_LANDSCAPE_W_PT = 297 * PT_PER_MM // ≈841.89
const A4_LANDSCAPE_H_PT = 210 * PT_PER_MM // ≈595.28
const TOL_PT = 1.5

function sampleTicket(i) {
  return boxTicketPages({
    customer: `顧客名サンプル${i}`,
    employee: `従業員名${i}`,
    number: `SI-${String(1000 + i).padStart(6, '0')}`,
    request: '消耗・破損交換',
    markShipDate: '2026-10-20',
    shortages: i % 3 === 0
      ? [
          { name: 'シャツ', sku: 'A1', size: 'M', qty: 1 },
          { name: 'ズボン', sku: 'B2', size: 'L', qty: 2 },
          { name: '帽子', sku: 'C3', size: 'F', qty: 1 },
        ]
      : [],
  })[0]
}

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
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'box-ticket-'))
  const htmlPath = path.join(dir, 'ticket.html')
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
    { encoding: 'utf8', timeout: 60000 }
  )
  if (r.status !== 0 || !fs.existsSync(pdfPath)) {
    throw new Error(`Chrome print failed: ${r.stderr || r.stdout || r.status}`)
  }
  return pdfPath
}

/** Parse MediaBox and page count from a simple Chrome PDF. */
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
  // Count page objects (Chrome uses /Type /Page with optional whitespace)
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

function measure(count) {
  const tickets = Array.from({ length: count }, (_, i) => sampleTicket(i + 1))
  const html = boxTicketPreviewHtml(tickets)
  const expectedPages = Math.ceil(count / 8) || 0
  assert.strictEqual((html.match(/class="page"/g) || []).length, expectedPages || 0)
  const outDir = path.join(__dirname, '../artifacts')
  fs.mkdirSync(outDir, { recursive: true })
  const pdfPath = path.join(outDir, `box-ticket-${count}.pdf`)
  printHtmlToPdf(html, pdfPath)
  const info = inspectPdf(pdfPath)
  assert.strictEqual(
    info.pageCount,
    expectedPages,
    `${count} tickets: expected ${expectedPages} PDF pages, got ${info.pageCount}`
  )
  assert.ok(info.boxes.length >= 1, `${count} tickets: no MediaBox`)
  for (const box of info.boxes) assertA4Landscape(box, `${count} tickets`)
  const wMm = info.boxes[0].w / PT_PER_MM
  const hMm = info.boxes[0].h / PT_PER_MM
  return {
    count,
    expectedPages,
    pageCount: info.pageCount,
    widthMm: +wMm.toFixed(2),
    heightMm: +hMm.toFixed(2),
    pdfPath,
  }
}

const results = [1, 8, 9].map(measure)
console.log(JSON.stringify({ ok: true, a4LandscapeMm: [297, 210], cellMm: [148.5, 52.5], results }, null, 2))
