import assert from 'node:assert/strict';
import { createHmac, webcrypto } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';

const code=readFileSync(new URL('../userscripts/douyin-send.user.js',import.meta.url),'utf8');
const work='1234567890123456789', salt='ab'.repeat(32), nonce='N'.repeat(43), token='b'.repeat(32), permit='c'.repeat(32);
const cookie=(name,value)=>({name,value,domain:'.douyin.com',path:'/'});
const goodCookies=()=>[cookie('uid_tt','test-uid-private'),cookie('uid_tt_ss','test-uid-private'),cookie('sessionid','test-session-private')];
const expected=createHmac('sha256',Buffer.from(salt,'hex')).update('test-uid-private').digest('hex');
const session={work_id:work,token,account_role:'secondary',expires_at:Math.floor(Date.now()/1000)+1200,next:{root_id:'',cursor:0},status:{operation:'browser',total:0}};
const wait=()=>new Promise(resolve=>setTimeout(resolve,5));
async function until(fn,message){for(let i=0;i<400;i++){if(fn())return;await wait();}assert.fail(message);}
function browser(url,cookies=goodCookies(),options={}){
  const page=new URL(url), requests=[], elements=[], sent=[], intervals=new Map(),timeouts=new Set();
  let currentCookies=cookies, cookieCalls=0,cookieError=options.cookieError||'', nextTimer=0, onClaim=null, permitAllowed=true,captchaShown=Boolean(options.captcha),serverCanceled=false;
  const storage=options.storage||{library_server:'http://nas.example:8090',library_token:'test-admin-secret'};
  function node(tag){
    const listeners={},attributes={};
    const el={tag,style:{},dataset:{},classList:{toggle(){},add(){},remove(){}},children:[],hidden:false,open:false,textContent:'',value:'',disabled:false,offsetHeight:300,
      append(...values){for(const item of values){item.parentElement=this;this.children.push(item);}},setAttribute(key,value){attributes[key]=String(value);},getAttribute(key){return attributes[key]??null;},
      addEventListener(name,fn){(listeners[name]??=[]).push(fn);},click(){if(!this.disabled)for(const fn of listeners.click||[])fn({target:this,preventDefault(){}});},focus(){},contains(item){return item===this||this.children.some(child=>child.contains?.(item));},
      matches(){return false;},closest(){return null;},querySelector(){return null;},querySelectorAll(){return [];},getBoundingClientRect(){return {width:0,height:0,top:0,bottom:0,left:0};},attachShadow(){return node('shadow');}};
    elements.push(el);return el;
  }
  const captcha={getBoundingClientRect:()=>({width:100,height:100})};
  const doc={body:node('body'),visibilityState:'visible',createElement:node,querySelector(){return null;},querySelectorAll(selector){return selector==='#captcha_container, [class*=captcha_verify_container]'&&captchaShown?[captcha]:[];},addEventListener(){}};
  class XHR {constructor(){this.events={};sent.push(this);}open(method,url){this.method=method;this.url=url;}setRequestHeader(){}addEventListener(name,fn){(this.events[name]??=[]).push(fn);}send(){this.sent=true;}abort(){this.aborted=true;this.onabort?.();}}
  const pageWindow={document:doc,XMLHttpRequest:XHR,Headers,localStorage:{getItem(){return '';}},fetch:async()=>({url:''}),getComputedStyle:()=>({display:'block',visibility:'visible'})};
  const ctx={URL,document:doc,unsafeWindow:pageWindow,location:page,history:{state:null,replaceState(_s,_t,u){page.href=new URL(u,page.href).href;}},innerWidth:1200,innerHeight:800,crypto:webcrypto,TextEncoder,
    GM_getValue(key,fallback){return storage[key]??fallback;},GM_setValue(key,value){storage[key]=value;},GM_listValues(){return Object.keys(storage);},GM_deleteValue(key){delete storage[key];},GM_registerMenuCommand(){},
    GM_cookie(_method,_options,cb){cookieCalls++;cb(currentCookies,cookieError);},
    GM_xmlhttpRequest(request){
      requests.push(request);const p=new URL(request.url).pathname,method=request.method;
      if(p==='/api/comment-accounts/blocked'&&options.blockedFail){queueMicrotask(()=>request.onerror());return;}
      let response,status=200;
      if(p===`/api/comment-accounts/bind/${nonce}`&&method==='GET')response={status:options.bindUsed?'bound':'waiting',role:'primary',identity_salt:salt,expires_at:session.expires_at};
      else if(p===`/api/comment-accounts/bind/${nonce}`&&method==='POST')response={status:'bound',role:'primary'};
      else if(p==='/api/comment-accounts'&&method==='GET')response={default_role:'secondary',identity_salt:salt,paused:Boolean(options.paused),primary:{bound:true},secondary:{bound:true},can_bind_secondary:true};
      else if(p===`/api/works/${work}/comments/browser`&&method==='POST')response=session;
      else if(p===`/api/works/${work}/comments/browser/${token}/claim`&&method==='POST'){if(options.claimConflict){response={detail:'浏览器会话已失效'};status=409;}else{onClaim?.();response=session;}}
      else if(p===`/api/works/${work}/comments/browser/${token}`&&method==='GET')response=session;
      else if(p===`/api/works/${work}/comments/browser/${token}/permit`&&method==='POST')response=permitAllowed?{ready:true,permit}:{ready:false,retry_after:3600};
      else if(p===`/api/works/${work}/comments/browser/${token}/page`&&method==='POST')response={...session,next:null};
      else if(p===`/api/works/${work}/comments/browser/${token}/finish`&&method==='POST'){response=serverCanceled?{detail:'已取消'}:{operation:'stopped'};if(serverCanceled)status=409;}
      else if(p==='/api/comment-accounts/blocked'&&method==='POST')response={paused:true};
      if(options.respond){const override=options.respond(p,method,request);if(override){response=override.response;status=override.status||200;}}
      if(response)queueMicrotask(()=>request.onload({status,responseText:JSON.stringify(response)}));
    },
    ResizeObserver:class{observe(){}disconnect(){}},MutationObserver:class{observe(){}disconnect(){}},addEventListener(){},requestAnimationFrame(fn){return 1;},
    setInterval(fn,delay){const id=++nextTimer;intervals.set(id,{fn,delay});return id;},clearInterval(id){intervals.delete(id);},
    setTimeout(fn,ms){const id=setTimeout(()=>{timeouts.delete(id);fn();},ms===5000?10:ms);timeouts.add(id);return id;},clearTimeout(id){clearTimeout(id);timeouts.delete(id);}};
  runInNewContext(code,ctx);
  return {requests,elements,sent,intervals,page,storage,pageWindow,doc,get cookieCalls(){return cookieCalls;},setCookies(values){currentCookies=values;},setCaptcha(value){captchaShown=value;},denyCookies(){cookieError='denied';},whenClaim(fn){onClaim=fn;},denyPermit(){permitAllowed=false;},cancelOnServer(){serverCanceled=true;},
    find(text){return elements.find(element=>element.textContent===text);},dispose(){for(const id of timeouts)clearTimeout(id);timeouts.clear();intervals.clear();}};
}
const path=r=>new URL(r.url).pathname;
const req=(b,method,url)=>b.requests.findLast(r=>r.method===method&&path(r)===url);
const json=r=>JSON.parse(r.data);

const batchId='d'.repeat(32), batchPath=`/api/comment-batches/${batchId}`, nextWork='1234567890123456788', nextToken='e'.repeat(32);
const batchURL=`https://www.douyin.com/video/${work}#library-comments=${token}&library-comment-batch=${batchId}`;
{
  let finished=false, batchReads=0;
  const b=browser(batchURL,goodCookies(),{respond(p,method){
    if(p===`${batchPath}/browser`){batchReads++;return {response:{batch:{id:batchId,channel:'browser',status:'running'},work:finished?{work_id:nextWork,token:nextToken}:{work_id:work,token}}};}
    if(p.endsWith('/claim')&&method==='POST')return {response:{...session,next:null}};
    if(p.endsWith('/finish')&&method==='POST'){finished=true;return {response:{operation:'queued'}};}
  }});
  await until(()=>b.page.pathname===`/video/${nextWork}`,'完成后未接续下一作品');
  assert.ok(batchReads>=2,'领取前和续接前读取批次');
  assert.equal(b.page.hash,`#library-comments=${nextToken}&library-comment-batch=${batchId}`);
  assert.equal(b.requests.filter(r=>path(r).endsWith('/finish')).length,1,'只提交一次 finish');
  assert.ok(!JSON.stringify(b.storage).includes(token)&&!JSON.stringify(b.storage).includes(nextToken),'任务令牌不能写入持久存储');
  b.dispose();
}
{
  const b=browser(batchURL,goodCookies(),{respond(p,method){
    if(p===`${batchPath}/browser`)return {response:{batch:{id:batchId,channel:'browser',status:'paused'},work:{work_id:work,token}}};
    if(p===`${batchPath}/stop`&&method==='POST')return {response:{}};
  }});
  await until(()=>req(b,'GET',`${batchPath}/browser`),'未查询批次');await wait();
  assert.equal(req(b,'POST',`/api/works/${work}/comments/browser/${token}/claim`),undefined,'暂停批次不能领取');
  b.dispose();
}
{
  let stopped=false;
  const b=browser(batchURL,goodCookies(),{respond(p,method){
    if(p===`${batchPath}/browser`)return {response:{batch:{id:batchId,channel:'browser',status:'running'},work:{work_id:work,token}}};
    if(p===`${batchPath}/stop`&&method==='POST'){stopped=true;return {response:{}};}
  }});
  await until(()=>req(b,'POST',`/api/works/${work}/comments/browser/${token}/claim`),'未领取会话');
  b.find('停止补抓').click();await until(()=>stopped,'手动停止未停止批次');
  assert.equal(req(b,'POST',`${batchPath}/pause`),undefined,'手动停止不应变成暂停');
  assert.equal(b.page.pathname,`/video/${work}`); b.dispose();
}
{
  let stopped=false;
  const b=browser(batchURL,goodCookies(),{respond(p,method){
    if(p===`${batchPath}/browser`)return {response:{batch:{id:batchId,channel:'browser',status:'running'},work:{work_id:nextWork,token}}};
    if(p===`${batchPath}/pause`&&method==='POST'){stopped=true;return {response:{}};}
  }});
  await until(()=>stopped,'入口作品不匹配应暂停批次');
  assert.equal(req(b,'POST',`${batchPath}/stop`),undefined,'异常只暂停，不永久停止');
  assert.equal(req(b,'POST',`/api/works/${work}/comments/browser/${token}/claim`),undefined);
  b.dispose();
}
{
  let stopped=false;
  const b=browser(batchURL,goodCookies(),{respond(p,method){
    if(p===`${batchPath}/browser`)return {response:{batch:{id:batchId,channel:'browser',status:'running'},work:{work_id:work,token}}};
    if(p.endsWith('/claim')&&method==='POST')return {response:{...session,next:null}};
    if(p.endsWith('/finish')&&method==='POST')return {response:{detail:'网络故障'},status:503};
    if(p===`${batchPath}/pause`&&method==='POST'){stopped=true;return {response:{}};}
  }});
  await until(()=>stopped,'结束请求失败时应暂停批次');
  assert.equal(b.requests.filter(r=>path(r).endsWith('/finish')).length,1,'不能重复结束请求');
  assert.equal(b.page.pathname,`/video/${work}`);b.dispose();
}
{
  let finished=false, stopped=false;
  const b=browser(batchURL,goodCookies(),{respond(p,method){
    if(p===`${batchPath}/browser`)return {response:{batch:{id:batchId,channel:'browser',status:'running'},work:{work_id:work,token:finished?nextToken:token}}};
    if(p.endsWith('/claim')&&method==='POST')return {response:{...session,next:null}};
    if(p.endsWith('/finish')&&method==='POST'){finished=true;return {response:{operation:'queued'}};}
    if(p===`${batchPath}/pause`&&method==='POST'){stopped=true;return {response:{}};}
  }});
  await until(()=>stopped,'相同作品的新会话应停止');
  assert.equal(b.page.pathname,`/video/${work}`);b.dispose();
}
{
  let stopped=false;
  const b=browser(batchURL,goodCookies(),{respond(p,method){
    if(p===`${batchPath}/browser`)return {response:{batch:{id:batchId,channel:'browser',status:'running'},work:{work_id:work,token}}};
    if(p.endsWith('/claim')&&method==='POST')return {response:{...session,account_role:'primary',next:null}};
    if(p===`${batchPath}/pause`&&method==='POST'){stopped=true;return {response:{}};}
  }});
  await until(()=>stopped,'批次不能使用大号');
  assert.equal(b.sent.length,0,'大号不得向平台发评论请求');
  assert.equal(b.page.pathname,`/video/${work}`);b.dispose();
}
{
  let stopped=false;
  const b=browser(batchURL,goodCookies(),{captcha:true,respond(p,method){
    if(p===`${batchPath}/browser`)return {response:{batch:{id:batchId,channel:'browser',status:'running'},work:{work_id:work,token}}};
    if(p===`${batchPath}/pause`&&method==='POST'){stopped=true;return {response:{}};}
  }});
  await until(()=>stopped,'遇验证应停止批次');
  assert.ok(req(b,'POST','/api/comment-accounts/blocked'),'验证需持久上报');
  assert.equal(req(b,'POST',`${batchPath}/stop`),undefined,'验证时保留批次断点');
  assert.equal(req(b,'POST',`/api/works/${work}/comments/browser/${token}/claim`),undefined);b.dispose();
}
{
  let paused=false;
  const b=browser(batchURL,goodCookies(),{respond(p,method){
    if(p===`${batchPath}/browser`)return {response:{batch:{id:batchId,channel:'browser',status:'running'},work:{work_id:work,token}}};
    if(p===`${batchPath}/pause`&&method==='POST'){paused=true;return {response:{}};}
  }});
  b.whenClaim(()=>b.setCookies([cookie('uid_tt','different'),cookie('sessionid','different')]));
  await until(()=>req(b,'POST',`/api/works/${work}/comments/browser/${token}/claim`),'未领取批次');
  const natural=new b.pageWindow.XMLHttpRequest();natural.open('GET',`https://www.douyin.com/aweme/v1/web/comment/list/?aweme_id=${work}`);natural.send();
  await until(()=>paused,'切换账号应暂停批次');
  assert.equal(req(b,'POST',`${batchPath}/stop`),undefined,'切换账号不能永久停止批次');
  assert.equal(req(b,'POST',`/api/works/${work}/comments/browser/${token}/finish`),undefined,'批次暂停由服务端保留断点');
  b.dispose();
}
{
  let paused=false;
  const b=browser(batchURL,goodCookies(),{respond(p,method){
    if(p===`${batchPath}/browser`)return {response:{batch:{id:batchId,channel:'browser',status:'running'},work:{work_id:work,token}}};
    if(p===`${batchPath}/pause`&&method==='POST'){paused=true;return {response:{}};}
  }});
  await until(()=>req(b,'POST',`/api/works/${work}/comments/browser/${token}/claim`),'未领取批次');
  b.doc.visibilityState='hidden';b.page.pathname=`/video/${nextWork}`;
  const monitor=[...b.intervals.values()].find(item=>item.delay===1500);assert.ok(monitor,'缺少页面切换检查');monitor.fn();
  await until(()=>paused,'后台切换作品应暂停批次');
  assert.equal(req(b,'POST',`${batchPath}/stop`),undefined,'后台切换只暂停批次');
  b.dispose();
}
console.log('助手批次接续模拟检查通过');
