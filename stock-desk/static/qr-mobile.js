function stockCanWrite(){return false}
async function logoutMobile(){
 const button=document.getElementById('mobile-logout');
 const error=document.getElementById('logout-error');
 button.disabled=true;error.hidden=true;
 try{
  QrRead.stop();
  const response=await fetch('/api/logout',{
   method:'POST',headers:{'Content-Type':'application/json','X-Desk-Request':'1'},body:'{}',
  });
  if(!response.ok&&response.status!==401)throw Error('ログアウトできませんでした。もう一度お試しください。');
  location.replace('/');
 }catch(err){
  error.textContent=err.message;error.hidden=false;button.disabled=false;
 }
}
async function api(path,data){
 const response=await fetch('/inventory'+path,data?{
  method:'POST',headers:{'Content-Type':'application/json','X-Stock-Request':'1'},body:JSON.stringify(data),
 }:{});
 const value=await response.json();
 if(!response.ok){const error=Error(value.error||'在庫を確認できませんでした。');error.status=response.status;throw error;}
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
  const logout=document.getElementById('mobile-logout');
  logout.hidden=false;logout.onclick=logoutMobile;
  QrRead.mobilePage();
 }catch(error){
  document.getElementById('main').textContent=error.message;
 }
})();
