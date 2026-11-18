function stockCanWrite(){return false}
async function api(path,data){
 const response=await fetch('/inventory'+path,data?{
  method:'POST',headers:{'Content-Type':'application/json','X-Desk-Request':'1'},body:JSON.stringify(data),
 }:{});
 const value=await response.json();
 if(!response.ok)throw Error(value.error||'在庫を確認できませんでした。');
 return value;
}
(async()=>{
 try{
  const response=await fetch('/api/me');
  const value=await response.json();
  if(!value.user||value.user.must_change){
   document.getElementById('main').innerHTML='<h1>在庫QR読み取り</h1><p>ログインしてから、このページを開いてください。</p><a class="login" href="/">ログイン画面へ</a>';
   return;
  }
  document.getElementById('who').textContent=value.user.name;
  QrRead.mobilePage();
 }catch(error){
  document.getElementById('main').textContent=error.message;
 }
})();
