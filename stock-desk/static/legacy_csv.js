/* 旧システムCSV一括取込 — 入口は「旧CSV一括取り込み」へ統合済み。
 * 旧タブ「旧CSV取込」からの参照が残っていても、新モーダルへ誘導する。
 */
(function(){
  window.legacyCsvPage=function legacyCsvPage(){
    const main=document.getElementById('main')
    if(main){
      main.innerHTML=`<section class="panel">
        <p class="notice">「旧CSV取込」は <strong>旧CSV一括取り込み</strong> に統合されました。</p>
        <p class="excelhint">登録・設定画面の右上（「表示」の左）のボタンから、商品登録と初期在庫登録を段階的に取り込めます。</p>
        <div class="bar"><button type="button" class="primary" id="legacyCsvRedirectOpen">旧CSV一括取り込みを開く</button></div>
      </section>`
      const btn=document.getElementById('legacyCsvRedirectOpen')
      if(btn)btn.onclick=()=>{
        if(typeof ProductLedgerCsv!=='undefined'&&ProductLedgerCsv.open){
          ProductLedgerCsv.open({
            api:typeof api==='function'?api:null,
            toast:typeof toast==='function'?toast:(m=>alert(m)),
            boot:typeof boot==='function'?boot:null,
            stockCanWrite:typeof stockCanWrite==='function'?stockCanWrite:null,
            actorName:(typeof state!=='undefined'&&state.data&&state.data.actorName)||'',
          })
        }else if(typeof toast==='function')toast('旧CSV一括取り込み画面を読み込めませんでした。')
      }
    }
  }
})()
