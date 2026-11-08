/** 共通のQR読み取り。在庫数量は変えず、入庫・出庫は既存画面で確定する。 */
(function(){
  const MAX_LEN=400
  let stream=null
  let timer=0
  let lastText=''
  let lastAt=0
  let decoding=false
  let result=null
  let selectedLot=''
  let busy=false
  let error=''

  function esc(s){
    return String(s==null?'':s)
      .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
      .replace(/"/g,'&quot;')
  }

  function canWrite(){
    return typeof stockCanWrite==='function'?stockCanWrite():true
  }

  function stop(){
    if(timer){cancelAnimationFrame(timer);timer=0}
    if(stream){
      stream.getTracks().forEach(track=>{try{track.stop()}catch(_){}})
      stream=null
    }
    const video=document.getElementById('qrCamVideo')
    if(video)video.srcObject=null
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

  async function lookup(text){
    const raw=String(text||'').replace(/\r/g,'').replace(/\n/g,'').trim()
    error=''
    result=null
    selectedLot=''
    if(!raw){error='QRの文字列を入力してください。';paintResult();return}
    if(raw.length>MAX_LEN){error='QRの文字列が長すぎます。';paintResult();return}
    if(suspicious(raw)){error='URLや実行用の文字列は在庫QRとして扱いません。';paintResult();return}
    const now=Date.now()
    if(raw===lastText&&now-lastAt<2500)return
    lastText=raw
    lastAt=now
    busy=true
    paintResult()
    try{
      result=await api('/api/stock-qr/read',{qr:raw})
    }catch(e){
      error=(e&&e.message)||'QRを確認できませんでした。'
      result=null
    }
    busy=false
    paintResult()
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
    const write=canWrite()
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
      </div>`:'<p class="excelhint">閲覧専用のため、在庫の確認だけできます。入庫・出庫はできません。</p>'}
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
    timer=requestAnimationFrame(tick)
    if(decoding)return
    const video=document.getElementById('qrCamVideo')
    if(!video||video.readyState<2)return
    decoding=true
    try{
      let text=''
      if(window.BarcodeDetector){
        const detector=tick.detector||(tick.detector=new BarcodeDetector({formats:['qr_code']}))
        const codes=await detector.detect(video)
        if(codes&&codes[0])text=codes[0].rawValue||''
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
      if(text)await lookup(text)
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
    try{
      stream=await navigator.mediaDevices.getUserMedia({
        audio:false,
        video:{facingMode:{ideal:'environment'}},
      })
    }catch(e){
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

  function page(){
    const main=document.getElementById('main')
    if(!main)return
    main.innerHTML=`<section class="panel qr-read-page">
      <h2>QR読み取り</h2>
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
    </section>`
    const form=document.getElementById('qrScanForm')
    const input=document.getElementById('qrScanInput')
    if(form)form.onsubmit=ev=>{ev.preventDefault();lookup(input&&input.value)}
    if(input)input.focus()
    const start=document.getElementById('qrCamStart')
    const stopBtn=document.getElementById('qrCamStop')
    if(start)start.onclick=()=>startCamera()
    if(stopBtn)stopBtn.onclick=()=>{stop();const note=document.getElementById('qrCamNote');if(note)note.textContent='カメラを止めました。'}
    paintResult()
  }

  window.addEventListener('pagehide',stop)
  document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='hidden')stop()})
  window.QrRead={page,stop,lookup}
})()
