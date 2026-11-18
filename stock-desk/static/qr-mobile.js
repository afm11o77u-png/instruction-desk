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
  if(window.QrLocal)await QrLocal.clearProfile();
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
  const user=await QrLocal.init(null,true);
  document.getElementById('who').textContent=user.name;
  const logout=document.getElementById('mobile-logout');
  logout.hidden=false;logout.onclick=logoutMobile;
  QrRead.mobilePage();
 }catch(error){
  document.getElementById('main').textContent=error.message;
  const login=document.createElement('a');login.href='/';login.className='login';login.textContent='事務所のWi-Fiでログイン画面へ';document.getElementById('main').append(login);
 }
})();
