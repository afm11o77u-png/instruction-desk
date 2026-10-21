'use strict'
/**
 * Headless Chrome で定型書式 HTML を PDF 化し、ページ数・MediaBox・画像書式を検証する。
 * 使い方: node tests/measure_fixed_label_pdf.js
 */
const assert = require('assert')
const fs = require('fs')
const os = require('os')
const path = require('path')
const { spawnSync } = require('child_process')
const { fixedLabelPreviewHtml, FIXED_LABEL_FORMATS } = require(path.join(__dirname, '../fixed_label.js'))

const PT_PER_MM = 72 / 25.4
const A4_LANDSCAPE_W_PT = 297 * PT_PER_MM
const A4_LANDSCAPE_H_PT = 210 * PT_PER_MM
const TOL_PT = 1.5
const ROOT = path.resolve(__dirname, '..')

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

/** 一時ディレクトリに HTML（と page-image なら画像）を展開し、file:// で確実に読めるようにする。 */
function materializePreview(formatId, sheets) {
  const format = FIXED_LABEL_FORMATS.find((f) => f.id === formatId)
  assert.ok(format, formatId)
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'fixed-label-'))
  let html
  if (format.kind === 'page-image') {
    const destName = path.basename(format.imageFile)
    fs.copyFileSync(path.join(ROOT, format.imageFile), path.join(dir, destName))
    html = fixedLabelPreviewHtml(formatId, sheets).replace(format.imageUrl, destName)
  } else {
    html = fixedLabelPreviewHtml(formatId, sheets)
  }
  const htmlPath = path.join(dir, 'fixed.html')
  fs.writeFileSync(htmlPath, html, 'utf8')
  return { dir, htmlPath, html, format }
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

function dumpDomMetrics(htmlPath, kind) {
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
        const imgs=[...document.querySelectorAll('img.sheetimg')];
        const r=page.getBoundingClientRect();
        return {
          kind:${JSON.stringify(kind)},
          pagePx:{w:r.width,h:r.height},
          pageMm:{w:mm(r.width),h:mm(r.height)},
          cellCount:cells.length,
          cellsMm:cells.map(c=>{const b=c.getBoundingClientRect();return {w:mm(b.width),h:mm(b.height)}}),
          titles:[...document.querySelectorAll('.title')].map(el=>el.textContent),
          checksOnFirst:cells[0]?[...cells[0].querySelectorAll('.check')].map(el=>el.textContent.trim()):[],
          darumaStack:cells[0]?(()=>{
            const no=cells[0].querySelector('.no');
            const sama=cells[0].querySelector('.sama');
            const nameRow=cells[0].querySelector('.name-row');
            if(!no||!sama||!nameRow) return null;
            const nb=no.getBoundingClientRect();
            const sb=sama.getBoundingClientRect();
            const nr=nameRow.getBoundingClientRect();
            const mm=v=>+(v*25.4/96).toFixed(2);
            return {
              stacked: sb.top > nb.bottom - 2,
              gapMm: mm(sb.top - nb.bottom),
              samaRightOfMid: sb.left > nr.left + nr.width * 0.55,
              nameRowWmm: mm(nr.width),
            };
          })():null,
          imageCount:imgs.length,
          images:imgs.map(img=>{
            const b=img.getBoundingClientRect();
            return {
              naturalW:img.naturalWidth,
              naturalH:img.naturalHeight,
              complete:img.complete,
              mm:{w:mm(b.width),h:mm(b.height)},
              src:(img.getAttribute('src')||'').slice(-48),
            };
          }),
        };
      }
      function done(out){document.getElementById('metrics').textContent=JSON.stringify(out)}
      const imgs=[...document.querySelectorAll('img.sheetimg')];
      if(!imgs.length) done(collect());
      else {
        let finished=false;
        const finish=()=>{if(finished)return;finished=true;done(collect())};
        Promise.all(imgs.map(img=>img.complete&&img.naturalWidth?Promise.resolve():new Promise(res=>{
          img.addEventListener('load',()=>res(),{once:true});
          img.addEventListener('error',()=>res(),{once:true});
        }))).then(finish);
        setTimeout(finish,3000);
      }
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
      '--virtual-time-budget=10000',
      '--dump-dom',
      `file://${probePath}`,
    ],
    { encoding: 'utf8', timeout: 90000, maxBuffer: 40 * 1024 * 1024 }
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

function measure(formatId, sheets) {
  const { htmlPath, html, format } = materializePreview(formatId, sheets)
  assert.strictEqual((html.match(/class="page/g) || []).length, sheets)
  if (format.kind === 'page-image') {
    assert.strictEqual((html.match(/class="cell"/g) || []).length, 0)
    assert.strictEqual((html.match(/class="sheetimg"/g) || []).length, sheets)
  } else {
    assert.strictEqual((html.match(/class="cell"/g) || []).length, sheets * 4)
  }
  const outDir = path.join(__dirname, '../artifacts')
  fs.mkdirSync(outDir, { recursive: true })
  const pdfPath = path.join(outDir, `fixed-label-${formatId}-${sheets}.pdf`)
  printHtmlToPdf(htmlPath, pdfPath)
  const info = inspectPdf(pdfPath)
  assert.strictEqual(
    info.pageCount,
    sheets,
    `${formatId} ${sheets} sheets: expected ${sheets} PDF pages, got ${info.pageCount}`
  )
  assert.ok(info.boxes.length >= 1, `${formatId} ${sheets} sheets: no MediaBox`)
  assertA4Landscape(info.boxes[0], `${formatId} ${sheets} sheets`)
  if (format.kind === 'page-image') {
    assert.ok(info.bytes > 8000 * sheets, `${formatId}: PDF too small (${info.bytes} bytes)`)
  }
  const widthMm = +(info.boxes[0].w / PT_PER_MM).toFixed(2)
  const heightMm = +(info.boxes[0].h / PT_PER_MM).toFixed(2)
  return {
    formatId,
    kind: format.kind || 'cells',
    sheets,
    pageCount: info.pageCount,
    cellsPerPage: format.kind === 'page-image' ? 0 : 4,
    imagesPerPage: format.kind === 'page-image' ? 1 : 0,
    widthMm: Math.round(widthMm),
    heightMm: Math.round(heightMm),
    pdfBytes: info.bytes,
    pdf: pdfPath,
  }
}

const darumaResults = [1, 3, 10].map((n) => measure('daruma-zenibako', n))
const imageFormats = FIXED_LABEL_FORMATS.filter((f) => f.kind === 'page-image')
const imageResults = []
for (const f of imageFormats) {
  imageResults.push(measure(f.id, 1))
  imageResults.push(measure(f.id, 3))
}

const darumaMat = materializePreview('daruma-zenibako', 1)
const darumaDom = dumpDomMetrics(darumaMat.htmlPath, 'cells')
assert.strictEqual(darumaDom.cellCount, 4)
assert.ok(Math.abs(darumaDom.pageMm.w - 297) < 2, `page width mm ${darumaDom.pageMm.w}`)
assert.ok(Math.abs(darumaDom.pageMm.h - 210) < 2, `page height mm ${darumaDom.pageMm.h}`)
for (const c of darumaDom.cellsMm) {
  assert.ok(Math.abs(c.w - 148.5) < 2, `cell w ${c.w}`)
  assert.ok(Math.abs(c.h - 105) < 2, `cell h ${c.h}`)
}
assert.ok(darumaDom.checksOnFirst.length === 13)
assert.ok(darumaDom.titles.every((t) => t === 'だるま食品銭函工場'))
assert.ok(darumaDom.darumaStack, 'daruma NO/様 stack metrics missing')
assert.ok(darumaDom.darumaStack.stacked, 'NO. and 様 must be vertically stacked')
assert.ok(darumaDom.darumaStack.gapMm >= 8, `name space gap too small: ${darumaDom.darumaStack.gapMm}mm`)
assert.ok(darumaDom.darumaStack.samaRightOfMid, '様 must sit at lower-right')
assert.ok(darumaDom.darumaStack.nameRowWmm >= 100, `name-row width ${darumaDom.darumaStack.nameRowWmm}`)

const imageDom = {}
for (const f of imageFormats) {
  const mat = materializePreview(f.id, 1)
  const dom = dumpDomMetrics(mat.htmlPath, 'page-image')
  assert.strictEqual(dom.cellCount, 0, `${f.id}: cells must be 0`)
  assert.strictEqual(dom.imageCount, 1, `${f.id}: one full-page image`)
  assert.ok(dom.images[0].naturalW > 1000, `${f.id}: naturalW ${dom.images[0].naturalW}`)
  assert.ok(dom.images[0].naturalH > 700, `${f.id}: naturalH ${dom.images[0].naturalH}`)
  assert.ok(Math.abs(dom.pageMm.w - 297) < 2, `${f.id}: page width ${dom.pageMm.w}`)
  assert.ok(Math.abs(dom.pageMm.h - 210) < 2, `${f.id}: page height ${dom.pageMm.h}`)
  assert.ok(Math.abs(dom.images[0].mm.w - dom.pageMm.w) < 3, `${f.id}: image fills page width`)
  assert.ok(Math.abs(dom.images[0].mm.h - dom.pageMm.h) < 3, `${f.id}: image fills page height`)
  imageDom[f.id] = {
    pageMm: dom.pageMm,
    imageMm: dom.images[0].mm,
    natural: { w: dom.images[0].naturalW, h: dom.images[0].naturalH },
    sourcePageRotate: f.sourcePageRotate || 0,
  }
}

console.log(
  JSON.stringify(
    {
      ok: true,
      results: [...darumaResults, ...imageResults],
      darumaDom: {
        pageMm: darumaDom.pageMm,
        cellsMm: darumaDom.cellsMm,
        checkCount: darumaDom.checksOnFirst.length,
        checksOnFirst: darumaDom.checksOnFirst,
        stack: darumaDom.darumaStack,
      },
      imageDom,
    },
    null,
    2
  )
)
