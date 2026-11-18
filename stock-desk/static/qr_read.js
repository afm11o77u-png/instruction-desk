/** 共通のQR読み取り。在庫数量は変えず、入庫・出庫は既存画面で確定する。 */
(function(){
  const MAX_LEN=400
  let stream=null
  let timer=0
  let decoding=false
  let result=null
  let selectedLot=''
  let busy=false
  let reading=null
  let error=''
  let mobile=false
  let cameraStarting=false
  let cameraEpoch=0
  let heldCode=''
  let emptySince=null

  function cameraButton(){
    const button=document.getElementById('qrCamToggle')
    if(button){button.textContent=stream||cameraStarting?'カメラOFF':'カメラON';button.setAttribute('aria-pressed',String(!!stream||cameraStarting))}
  }

  function acceptFrame(text,now){
    if(!text){
      if(emptySince===null)emptySince=now
      if(now-emptySince>=600)heldCode=''
      return false
    }
    emptySince=null
    if(text===heldCode)return false
    heldCode=text
    return true
  }

  function esc(s){
    return String(s==null?'':s)
      .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
      .replace(/"/g,'&quot;')
  }

  function canWrite(){
    return typeof stockCanWrite==='function'?stockCanWrite():true
  }

  function stop(){
    cameraEpoch++
    cameraStarting=false
    if(timer){cancelAnimationFrame(timer);timer=0}
    if(stream){
      stream.getTracks().forEach(track=>{try{track.stop()}catch(_){}})
      stream=null
    }
    const video=document.getElementById('qrCamVideo')
    if(video)video.srcObject=null
    cameraButton()
  }

  function noteForCamera(){
    if(!window.isSecureContext){
      return 'カメラは HTTPS、またはこのパソコンの localhost だけで使えます。今のアドレスでは起動できません。QRリーダーか手入力を使ってください。'
    }
    if(!navigator.mediaDevices||!navigator.mediaDevices.getUserMedia){
      return 'このブラウザはカメラ読取に対応していません。QRリーダーか手入力を使ってください。'
    }
    return '背面カメラを優先します。権限を拒否した場合は、QRリーダーか手入力を使ってください。専用機器のSDKには対応していません。'
  }

  function suspicious(text){
    const low=String(text||'').trim().toLowerCase()
    return low.startsWith('http://')||low.startsWith('https://')||low.startsWith('javascript:')||low.startsWith('data:')
  }

  function lookup(text){
    if(reading)return reading
    const task=lookupCore(text).finally(()=>{if(reading===task)reading=null})
    reading=task
    return task
  }

  async function lookupCore(text){
    if(busy)return
    const raw=String(text||'').replace(/\r/g,'').replace(/\n/g,'').trim()
    error=''
    result=null
    selectedLot=''
    if(!raw){error='QRの文字列を入力してください。';paintResult();return}
    if(raw.length>MAX_LEN){error='QRの文字列が長すぎます。';paintResult();return}
    if(suspicious(raw)){error='URLや実行用の文字列は在庫QRとして扱いません。';paintResult();return}
    busy=true
    paintResult()
    try{
      result=window.QrLocal?await QrLocal.lookup(raw):await api('/api/stock-qr/read',{qr:raw})
      if(!result?.found)throw Error('このQRは商品情報と照合できません。枚数には加算していません。「倉庫用の準備」を更新し、QRの対応登録を確認してください。')
      if(window.QrWork){
        await QrWork.record(raw)
      }
    }catch(e){
      error=(e&&e.message)||'QRを確認できませんでした。'
      result=null
    }
    busy=false
    paintResult()
    if(mobile){
      const note=document.getElementById('qrCamNote')
      if(note)note.textContent=error?'読取エラー：'+error:'読み取り内容を端末に保存しました。一覧に表示しています。'
      if(error)stop()
    }
    return !error
  }

  function lotKey(lot){return String(lot.lotId)}

  function chosenLot(){
    if(!result||!result.lots)return null
    return result.lots.find(lot=>lotKey(lot)===selectedLot)||null
  }

  function targetFrom(lot){
    const link=result.link||result
    const condition=lot?lot.condition:(link.condition||result.condition||'')
    return {
      customerProductId:link.customerProductId||result.customerProductId,
      customerId:link.customerId||result.customerId,
      customerName:link.customerName||result.customerName||'',
      code:link.itemNum||result.code||'',
      productName:link.itemName||result.productName||'',
      size:lot?lot.size:(link.sizeLabel||result.size||''),
      locationId:lot?lot.locationId:(link.locationId!=null?link.locationId:null),
      locationLabel:lot?lot.locationLabel:'',
      condition:condition,
      conditionLabel:lot?lot.conditionLabel:'',
      embroideryLabel:result.embroideryLabel||'',
      qty:lot?lot.qtyRemaining:0,
    }
  }

  function paintResult(){
    const box=document.getElementById('qrScanResult')
    if(!box)return
    if(busy){box.innerHTML='<p>照合しています。在庫数量は変えていません。</p>';return}
    if(error){box.innerHTML=`<p class="notice" role="alert">${esc(error)}</p>`;return}
    if(!result){box.innerHTML='';return}
    if(mobile&&window.QrWork){box.innerHTML='';return}
    const kind=result.qrKind==='new'?'新QR':'旧QR'
    if(!result.found){
      box.innerHTML=`<p class="notice">一致する在庫QRはありません（${esc(kind)}として照合しました）。0件を1件には決めません。</p>`
      return
    }
    const link=result.link||result
    const name=`${link.itemNum||result.code||''} ${link.itemName||result.productName||''}`.trim()
    const lots=result.lots||[]
    const rows=lots.map(lot=>{
      const key=lotKey(lot)
      return `<tr>
        <td><label><input type="radio" name="qrLot" value="${esc(key)}" ${selectedLot===key?'checked':''}> 確認</label></td>
        <td>${esc(lot.size||'')}</td>
        <td>${esc(lot.conditionLabel||lot.condition||'未設定')}</td>
        <td>${esc(lot.locationLabel||'場所未設定')}</td>
        <td>${esc(lot.qtyRemaining)}</td>
      </tr>`
    }).join('')
    const write=canWrite()&&!window.QrWork
    const picked=!!chosenLot()
    const inbound=write&&(picked||(!lots.length&&(link.customerProductId||result.customerProductId)))
    const outbound=write&&picked
    box.innerHTML=`<section class="qr-read-result">
      <p><strong>${esc(kind)}</strong>　${esc(result.customerName||link.customerName||'')}　${esc(name)}</p>
      <p>在庫ロット ${lots.length} 件。読み取りだけでは数量を変えず、入庫・出庫も確定しません。</p>
      ${lots.length?`<div class="tablewrap"><table class="qr-read-lots"><thead><tr><th></th><th>サイズ</th><th>状態</th><th>場所</th><th>残数</th></tr></thead><tbody>${rows}</tbody></table></div>`:
        '<p class="notice">この区分の在庫ロットは0件です。出庫できるロットはありません。</p>'}
      ${write?`<div class="bar">
        <button type="button" id="qrGoIn" ${inbound?'':'disabled'}>${lots.length?'選んだロットで入庫画面を開く':'在庫はありません。入庫画面を開く'}</button>
        <button type="button" id="qrGoOut" ${outbound?'':'disabled'}>選んだロットで出庫画面を開く</button>
      </div>`:window.QrWork?'<p>数量は下の作業一覧で確認・編集してください。完了後、PCで在庫への反映を確定します。</p>':'<p class="excelhint">閲覧専用のため、在庫の確認だけできます。入庫・出庫はできません。</p>'}
    </section>`
    box.querySelectorAll('input[name="qrLot"]').forEach(input=>{
      input.onchange=()=>{selectedLot=input.value;paintResult()}
    })
    const goIn=document.getElementById('qrGoIn')
    const goOut=document.getElementById('qrGoOut')
    if(goIn)goIn.onclick=()=>{
      const lot=chosenLot()
      if(lots.length&&!lot)return
      if(typeof openInboundDialog==='function')openInboundDialog(targetFrom(lot))
    }
    if(goOut)goOut.onclick=()=>{
      const lot=chosenLot()
      if(!lot)return
      if(typeof openOutboundDialog==='function')openOutboundDialog(targetFrom(lot))
    }
  }

  async function tick(){
    if(!stream)return
    const run=cameraEpoch
    timer=requestAnimationFrame(tick)
    if(decoding)return
    const video=document.getElementById('qrCamVideo')
    if(!video||video.readyState<2)return
    decoding=true
    try{
      let text=''
      if(window.BarcodeDetector){
        try{
        const detector=tick.detector||(tick.detector=new BarcodeDetector({formats:['qr_code']}))
        const codes=await detector.detect(video)
        if(codes&&codes[0])text=codes[0].rawValue||''
        }catch(_){ /* Use jsQR when the browser detector is unsupported. */ }
      }
      if(!text&&typeof jsQR==='function'){
        const canvas=document.getElementById('qrCamCanvas')
        if(canvas){
          const w=video.videoWidth||0
          const h=video.videoHeight||0
          if(w&&h){
            canvas.width=w
            canvas.height=h
            const ctx=canvas.getContext('2d',{willReadFrequently:true})
            ctx.drawImage(video,0,0,w,h)
            const image=ctx.getImageData(0,0,w,h)
            const found=jsQR(image.data,w,h,{inversionAttempts:'dontInvert'})
            if(found&&found.data)text=found.data
          }
        }
      }
      if(stream&&run===cameraEpoch&&((!mobile&&text)||(mobile&&acceptFrame(text,Date.now())))){
        if(!mobile)stop()
        const note=document.getElementById('qrCamNote')
        if(note)note.textContent='QRを読み取りました。在庫を照合しています。'
        await lookup(text)
      }
    }catch(_){}
    decoding=false
  }

  async function startCamera(){
    const note=document.getElementById('qrCamNote')
    if(!window.isSecureContext){
      if(note)note.textContent=noteForCamera()
      return
    }
    stop()
    const run=++cameraEpoch
    cameraStarting=true
    cameraButton()
    heldCode='';emptySince=null
    try{
      const acquired=await navigator.mediaDevices.getUserMedia({
        audio:false,
        video:{facingMode:{ideal:'environment'}},
      })
      if(run!==cameraEpoch){acquired.getTracks().forEach(track=>track.stop());return}
      stream=acquired
      cameraStarting=false
      cameraButton()
    }catch(e){
      if(run!==cameraEpoch)return
      cameraStarting=false;cameraButton()
      if(note)note.textContent='カメラを開始できませんでした。権限を拒否したか、このブラウザが対応していません。QRリーダーか手入力を使ってください。'
      return
    }
    const video=document.getElementById('qrCamVideo')
    if(!video){stop();return}
    video.srcObject=stream
    try{await video.play()}catch(_){}
    if(note)note.textContent='カメラで読み取っています。止めるとカメラはオフになります。'
    timer=requestAnimationFrame(tick)
  }

  function mobilePage(){
    mobile=true
    stop()
    result=null
    error=''
    document.getElementById('main').innerHTML=`<section class="qr-read-page">
      <h1>在庫QR読み取り</h1>
      <div class="qr-read-camera mobile-camera">
        <button type="button" id="qrCamToggle" class="primary" aria-pressed="false">カメラON</button>
        <p id="qrCamNote" role="status"></p>
        <video id="qrCamVideo" playsinline muted autoplay></video>
        <canvas id="qrCamCanvas" hidden></canvas>
      </div>
      <details><summary>QRリーダー・手入力で探す</summary>
        <form id="qrScanForm"><label>QR文字列<input id="qrScanInput" maxlength="${MAX_LEN}" autocomplete="off"></label>
        <button type="submit">この文字列で探す</button></form>
      </details>
      <div id="qrScanResult" aria-live="polite"></div>
    </section>`
    document.getElementById('qrCamToggle').onclick=()=>{
      if(stream||cameraStarting){stop();document.getElementById('qrCamNote').textContent='カメラはOFFです。'}else startCamera()
    }
    document.getElementById('qrScanForm').onsubmit=event=>{
      event.preventDefault()
      lookup(document.getElementById('qrScanInput').value)
    }
    const note=document.getElementById('qrCamNote')
    note.textContent=noteForCamera()
    if(window.QrWork)QrWork.mount(true)
  }

  function page(){
    mobile=false
    stop()
    const main=document.getElementById('main')
    if(!main)return
    main.innerHTML=`<section class="panel qr-read-page">
      <h2>QR読み取り</h2>
      <div id="qrReplBanner" style="display:none; margin:10px 0 16px 0; background:#fffbe6; border:2px solid #faad14; padding:12px 16px; border-radius:8px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px;">
        <div>
          <strong style="color:#d46b08; font-size:15px; display:flex; align-items:center; gap:6px;">
            <span>⚠️</span> <span>QRコードの貼り替えが必要な商品が <span id="qrReplCount">56</span> 件あります</span>
          </strong>
          <span style="font-size:12px; color:#595959;">旧データで複数色が混在していた商品を規格・色ごとに分けました。現物の新QR貼り替え対象を確認できます。</span>
        </div>
        <button type="button" id="qrReplOpenBtn" class="primary" style="font-size:13px; padding:6px 14px; white-space:nowrap; cursor:pointer;">貼り替え一覧・印刷を開く</button>
      </div>
      <p>WindowsのQRリーダーは、読み取った文字列を下の欄へ入力します。Enterで検索します。手入力も同じ欄です。AndroidとiOSのブラウザは、下のカメラで読めます。別アプリには分けていません。</p>
      <p class="excelhint">${esc(noteForCamera())} 読み取った文字列は外部のQRサービスへ送りません。URLを開いたり、在庫数量を自動では変えません。</p>
      <form id="qrScanForm" class="qr-read-form">
        <label>QR文字列<input id="qrScanInput" autocomplete="off" spellcheck="false" maxlength="${MAX_LEN}" inputmode="text" placeholder="リーダーの入力、または手入力"></label>
        <button type="submit" class="primary">この文字列で探す</button>
      </form>
      <div class="qr-read-camera">
        <div class="bar">
          <button type="button" id="qrCamStart">カメラで読む</button>
          <button type="button" id="qrCamStop">カメラを止める</button>
        </div>
        <p id="qrCamNote" class="excelhint"></p>
        <video id="qrCamVideo" playsinline muted autoplay></video>
        <canvas id="qrCamCanvas" hidden></canvas>
      </div>
      <div id="qrScanResult"></div>
      <details class="qr-access-guide">
        <summary>スマホ・画面付き端末からの開き方／おすすめ端末</summary>
        <h3>スマホ・端末から使えます</h3><p><a href="qr-mobile.html">スマホ専用のQR読み取りを開く</a></p>
        <ol>
          <li>パソコンと同じ社内Wi-Fiにつなぎます。</li>
          <li>ブラウザで、管理者から案内されたシステムのアドレスを開きます。</li>
          <li>ログインして「在庫管理」→「QR読み取り」を開きます。</li>
        </ol>
        <p>アドレスの例：<code>http://192.168.1.10:8765</code>（実際のパソコンのIPアドレスに置き換えてください）。別端末からは <code>127.0.0.1</code> や <code>localhost</code> では接続できません。開けない場合は、社内ネットワークからの接続設定を管理者に確認してください。</p>
        <h3>読み取り方法</h3>
        <ul>
          <li><strong>スマホのカメラ：</strong>「カメラで読む」を押して利用を許可します。スマホのカメラ利用には原則HTTPSが必要です。上のHTTPアドレス例は画面への接続用です。</li>
          <li><strong>スキャナー内蔵端末：</strong>QR文字列の入力欄を選び、本体のボタンで読み取ります。読み取った文字列をブラウザの入力欄へ送る設定と、必要に応じてEnterを送る設定が必要です。</li>
        </ul>
        <h3>おすすめの導入候補（メーカー公式）</h3>
        <ul>
          <li><a href="https://www.zebra.com/us/en/products/mobile-computers/handheld/tc2x-series.html" target="_blank" rel="noopener noreferrer">Zebra TC22／TC27</a>：画面で商品や数量を確認する運用の候補。社内Wi-Fi中心ならTC22。</li>
          <li><a href="https://www.denso-wave.com/ja/adcd/product/handy_terminal/bht-m80.html" target="_blank" rel="noopener noreferrer">デンソーウェーブ BHT-M80</a>：5インチ画面で情報を確認したい場合の候補。</li>
          <li><a href="https://www.denso-wave.com/ja/adcd/product/handy_terminal/bht-m60.html" target="_blank" rel="noopener noreferrer">デンソーウェーブ BHT-M60</a>：物理キーで数量を入力したい場合の候補。</li>
        </ul>
        <p>このシステムでの実機動作は未確認です。購入前に貸出機などで、ブラウザ・QR入力・社内Wi-Fi接続を確認してください。</p>
        <p><strong>現在は通信しながら利用します。</strong>通信なしでの読みため・後から一括反映する機能は未実装です。読み取っただけでは在庫数は変わりません。</p>
      </details>
    </section>`
    const form=document.getElementById('qrScanForm')
    const input=document.getElementById('qrScanInput')
    if(form)form.onsubmit=ev=>{ev.preventDefault();lookup(input&&input.value)}
    if(input)input.focus()
    const start=document.getElementById('qrCamStart')
    const stopBtn=document.getElementById('qrCamStop')
    if(start)start.onclick=()=>startCamera()
    if(stopBtn)stopBtn.onclick=()=>{stop();const note=document.getElementById('qrCamNote');if(note)note.textContent='カメラを止めました。'}
    // QR貼り替えバナーの初期化
    const replBanner=document.getElementById('qrReplBanner')
    const replBtn=document.getElementById('qrReplOpenBtn')
    if(replBanner&&window.QrReplacementModal){
      window.QrReplacementModal.loadData().then(data=>{
        if(data&&data.totalCount>0){
          const countEl=document.getElementById('qrReplCount')
          if(countEl)countEl.textContent=String(data.totalCount)
          replBanner.style.display='flex'
          if(replBtn)replBtn.onclick=()=>window.QrReplacementModal.open()
        }
      }).catch(()=>{})
    }
    paintResult()
    if(window.QrWork)QrWork.mount(false)
  }

  window.addEventListener('pagehide',stop)
  document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='hidden')stop()})
  window.QrRead={page,mobilePage,stop,lookup,waitForRead:()=>reading||Promise.resolve(true)}
})()
