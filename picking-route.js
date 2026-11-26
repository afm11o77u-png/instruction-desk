/* 石狩倉庫2F のルート順でピッキングの石狩倉庫明細を並べるための名前合わせ。ishikari_route.py と同じ規則。 */
(function(root){
const CORP=/株式会社|有限会社|合同会社|合資会社|合名会社|\((?:株|有|同|資|名)\)/g;
const BRACKETS=/\([^()]*\)|\[[^\[\]]*\]|【[^【】]*】|「[^「」]*」|『[^『』]*』|〔[^〔〕]*〕|<[^<>]*>|《[^《》]*》|〈[^〈〉]*〉/g;
function routeKey(value){let s=String(value??'').normalize('NFKC').toLowerCase();s=s.replace(CORP,'');for(let i=0;i<3;i++)s=s.replace(BRACKETS,'');s=s.replace(/[()\[\]【】「」『』〔〕<>《》〈〉]/g,'');return s.replace(/[\s・\-‐‑–—―]+/g,'')}
function routeMatch(customer,info){const k=routeKey(customer);const none={status:'unmatched',name:'',rank:null,candidates:[],alias:false};if(!k||!info)return none;const a=(info.aliases||{})[k];if(a)return{status:'matched',name:a.name,rank:a.rank,candidates:[],alias:true};const hits=(info.candidates||[]).filter(c=>c.key===k||(c.key.length>=2&&k.startsWith(c.key))||(c.key.length>=3&&k.includes(c.key)));if(!hits.length)return none;const best=Math.max(...hits.map(c=>c.key.length));const top=hits.filter(c=>c.key.length===best);if(top.length>1)return{status:'ambiguous',name:'',rank:null,candidates:top.map(c=>c.name),alias:false};return{status:'matched',name:top[0].name,rank:top[0].rank,candidates:[],alias:false}}
function routeSort(rows,info,field='customer'){if(!info||!(info.route||[]).length)return rows.slice();const cache=new Map();const rank=r=>{const v=String(r[field]??'');if(!cache.has(v))cache.set(v,routeMatch(v,info).rank);return cache.get(v)};return rows.map((r,i)=>[rank(r),i,r]).sort((a,b)=>((a[0]==null)-(b[0]==null))||((a[0]||0)-(b[0]||0))||(a[1]-b[1])).map(x=>x[2])}
const api={routeKey,routeMatch,routeSort};
if(typeof module!=='undefined'&&module.exports)module.exports=api;else Object.assign(root,api);
})(typeof window!=='undefined'?window:globalThis);
