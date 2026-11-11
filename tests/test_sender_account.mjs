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
      if(response)queueMicrotask(()=>request.onload({status,responseText:JSON.stringify(response)}));
    },
    ResizeObserver:class{observe(){}disconnect(){}},MutationObserver:class{observe(){}disconnect(){}},addEventListener(){},requestAnimationFrame(fn){return 1;},
    setInterval(fn,delay){const id=++nextTimer;intervals.set(id,{fn,delay});return id;},clearInterval(id){intervals.delete(id);},
    setTimeout(fn,ms){const id=setTimeout(()=>{timeouts.delete(id);fn();},ms);timeouts.add(id);return id;},clearTimeout(id){clearTimeout(id);timeouts.delete(id);}};
  runInNewContext(code,ctx);
  return {requests,elements,sent,intervals,page,storage,pageWindow,doc,get cookieCalls(){return cookieCalls;},setCookies(values){currentCookies=values;},setCaptcha(value){captchaShown=value;},denyCookies(){cookieError='denied';},whenClaim(fn){onClaim=fn;},denyPermit(){permitAllowed=false;},cancelOnServer(){serverCanceled=true;},
    find(text){return elements.find(element=>element.textContent===text);},dispose(){for(const id of timeouts)clearTimeout(id);timeouts.clear();intervals.clear();}};
}
const path=r=>new URL(r.url).pathname;
const req=(b,method,url)=>b.requests.findLast(r=>r.method===method&&path(r)===url);
const json=r=>JSON.parse(r.data);

// Explicit account binding: UID remains local and only a keyed fingerprint is posted.
const bind=browser(`https://www.douyin.com/#library-comment-account=${nonce}`);
await until(()=>req(bind,'POST',`/api/comment-accounts/bind/${nonce}`),'没有完成模拟账号绑定');
assert.equal(bind.page.hash,'','绑定 nonce 必须先从地址移除');
assert.deepEqual(json(req(bind,'POST',`/api/comment-accounts/bind/${nonce}`)),{fingerprint:expected});
assert.ok(!JSON.stringify(bind.requests).includes('test-uid-private'));
assert.ok(!JSON.stringify(bind.requests).includes('test-session-private'));
assert.ok(!bind.requests.some(r=>path(r).includes('/comments/browser')),'绑定不会启动评论任务');
bind.dispose();
for(const invalid of [[cookie('uid_tt','a'),cookie('uid_tt_ss','b'),cookie('sessionid','s')],[cookie('uid_tt','a')],[cookie('sessionid','s')],[cookie('uid_tt','a'),cookie('uid_tt','b'),cookie('sessionid','s')]]){
  const b=browser(`https://www.douyin.com/#library-comment-account=${nonce}`,invalid);
  await until(()=>b.cookieCalls>0,'应读取模拟身份');await wait();
  assert.equal(req(b,'POST',`/api/comment-accounts/bind/${nonce}`),undefined,'缺失或冲突身份不得绑定');b.dispose();
}

const deniedCookie=browser(`https://www.douyin.com/#library-comment-account=${nonce}`,goodCookies(),{cookieError:'denied'});
await until(()=>deniedCookie.cookieCalls>0,'应尝试读取模拟 Cookie');await wait();
assert.equal(req(deniedCookie,'POST',`/api/comment-accounts/bind/${nonce}`),undefined,'读取权限失败不得绑定');deniedCookie.dispose();
const duplicateSame=browser(`https://www.douyin.com/#library-comment-account=${nonce}`,[...goodCookies(),cookie('uid_tt','test-uid-private')]);
await until(()=>req(duplicateSame,'POST',`/api/comment-accounts/bind/${nonce}`),'相同 UID 重复值应可绑定');
assert.equal(json(req(duplicateSame,'POST',`/api/comment-accounts/bind/${nonce}`)).fingerprint,expected);duplicateSame.dispose();

const used=browser(`https://www.douyin.com/#library-comment-account=${nonce}`,goodCookies(),{bindUsed:true});
await until(()=>used.elements.some(element=>element.textContent.includes('已使用')),'已用绑定链接应提示已使用');
assert.equal(used.cookieCalls,0,'已使用的绑定链接不得读取 Cookie');
assert.equal(req(used,'POST',`/api/comment-accounts/bind/${nonce}`),undefined,'已使用的绑定链接不得重复提交');used.dispose();

const idle=browser(`https://www.douyin.com/video/${work}`);await wait();
assert.equal(idle.cookieCalls,0,'没有评论任务时不轮询 Cookie');idle.dispose();

// An already-visible challenge is reported globally without claiming or querying Douyin.
const challenged=browser(`https://www.douyin.com/video/${work}#library-comments=${token}`,goodCookies(),{captcha:true});
await until(()=>req(challenged,'POST','/api/comment-accounts/blocked'),'已有验证未上报全局受阻');
assert.equal(json(req(challenged,'POST','/api/comment-accounts/blocked')).fingerprint,expected);
assert.equal(req(challenged,'POST',`/api/works/${work}/comments/browser/${token}/claim`),undefined);
assert.equal(challenged.sent.length,0,'已有验证时不得发采集请求');challenged.dispose();

// The local safety latch survives offline reporting, refresh, and automatic entry.
const offline=browser(`https://www.douyin.com/video/${work}#library-comments=${token}`,goodCookies(),{captcha:true,blockedFail:true});
await until(()=>req(offline,'POST','/api/comment-accounts/blocked'),'未尝试受限上报');
const latchKeys=()=>Object.keys(offline.storage).filter(key=>key.startsWith('library_comment_block_'));
await until(()=>latchKeys().length===1,'离线上报后必须保留本地受阻记录');
assert.equal(offline.storage[latchKeys()[0]].sent,false);const savedStore=offline.storage;offline.dispose();
const retry=browser(`https://www.douyin.com/video/${work}`,goodCookies(),{storage:savedStore,paused:true});
retry.find('保存评论').click();
await until(()=>req(retry,'GET','/api/comment-accounts'),'恢复连接后应先上报再读取暂停状态');
assert.ok(retry.requests.indexOf(req(retry,'POST','/api/comment-accounts/blocked'))<retry.requests.indexOf(req(retry,'GET','/api/comment-accounts')));
assert.equal(savedStore[latchKeys()[0]].sent,true);assert.equal(req(retry,'POST',`/api/works/${work}/comments/browser`),undefined,'网站仍暂停时不得继续');retry.dispose();
const autoEntry=browser(`https://www.douyin.com/video/${work}#library-comments=${token}`,goodCookies(),{storage:savedStore});
await until(()=>req(autoEntry,'GET','/api/comment-accounts'),'自动入口未读取账号状态');await wait();
assert.equal(latchKeys().length,1,'hash 自动入口不能清除本地暂停记录');
assert.equal(req(autoEntry,'POST',`/api/works/${work}/comments/browser/${token}/claim`),undefined);autoEntry.dispose();
const bindingEntry=browser(`https://www.douyin.com/#library-comment-account=${nonce}`,goodCookies(),{storage:savedStore});
await until(()=>req(bindingEntry,'POST',`/api/comment-accounts/bind/${nonce}`),'已上报的暂停不应阻止绑定');
assert.equal(latchKeys().length,1,'绑定入口不能清除本地暂停记录');bindingEntry.dispose();
const acknowledged=browser(`https://www.douyin.com/video/${work}`,goodCookies(),{storage:savedStore});acknowledged.find('保存评论').click();
await until(()=>req(acknowledged,'POST',`/api/works/${work}/comments/browser`),'解除网站暂停后手动开始未恢复');
assert.equal(latchKeys().length,0,'上报且网站解除暂停后，显式开始才能清理本地记录');acknowledged.dispose();

// A successful synthetic page must carry its reserved permit, never raw cookies.
const complete=browser(`https://www.douyin.com/video/${work}#library-comments=${token}`);
await until(()=>req(complete,'GET',`/api/works/${work}/comments/browser/${token}`),'未读取会话');
let natural=new complete.pageWindow.XMLHttpRequest();natural.open('GET',`https://www.douyin.com/aweme/v1/web/comment/list/?aweme_id=${work}`);natural.send();
await until(()=>complete.sent.length===2&&complete.sent[1].sent,'合成评论请求未发起');
const reading=complete.sent[1];reading.status=200;reading.responseText=JSON.stringify({status_code:0,has_more:0,cursor:1,total:0,comments:[]});reading.onload();
await until(()=>req(complete,'POST',`/api/works/${work}/comments/browser/${token}/page`),'未提交合成评论页');
const submitted=req(complete,'POST',`/api/works/${work}/comments/browser/${token}/page`);
assert.equal(json(submitted).permit,permit);assert.equal(json(submitted).root_id,'');assert.equal(json(submitted).cursor,0);
assert.ok(!JSON.stringify(complete.requests).includes('test-uid-private'));assert.ok(!JSON.stringify(complete.requests).includes('test-session-private'));
complete.dispose();

// An unknown platform error reports the persistent global block before finishing.
const failed=browser(`https://www.douyin.com/video/${work}#library-comments=${token}`);
await until(()=>req(failed,'GET',`/api/works/${work}/comments/browser/${token}`),'未读取会话');
natural=new failed.pageWindow.XMLHttpRequest();natural.open('GET',`https://www.douyin.com/aweme/v1/web/comment/list/?aweme_id=${work}`);natural.send();
await until(()=>failed.sent.length===2&&failed.sent[1].sent,'合成评论请求未发起');
failed.sent[1].onerror();
await until(()=>req(failed,'POST',`/api/works/${work}/comments/browser/${token}/finish`),'读取失败未结束会话');
const report=req(failed,'POST','/api/comment-accounts/blocked'),finish=req(failed,'POST',`/api/works/${work}/comments/browser/${token}/finish`);
assert.equal(json(report).fingerprint,expected);assert.equal(json(finish).status,'blocked');
assert.ok(failed.requests.indexOf(report)<failed.requests.indexOf(finish),'先持久上报受阻再结束会话');failed.dispose();

// Non-JSON responses are also reported and do not upload a page.
const badJson=browser(`https://www.douyin.com/video/${work}#library-comments=${token}`);
await until(()=>req(badJson,'GET',`/api/works/${work}/comments/browser/${token}`),'未读取会话');
natural=new badJson.pageWindow.XMLHttpRequest();natural.open('GET',`https://www.douyin.com/aweme/v1/web/comment/list/?aweme_id=${work}`);natural.send();
await until(()=>badJson.sent.length===2&&badJson.sent[1].sent,'合成评论请求未发起');
badJson.sent[1].status=200;badJson.sent[1].responseText='not json';badJson.sent[1].onload();
await until(()=>req(badJson,'POST','/api/comment-accounts/blocked'),'非 JSON 未上报全局暂停');
assert.equal(req(badJson,'POST',`/api/works/${work}/comments/browser/${token}/page`),undefined);badJson.dispose();

// A delayed error after cancellation still reports a risk signal independently of the old token.
const late=browser(`https://www.douyin.com/video/${work}#library-comments=${token}`);
await until(()=>req(late,'GET',`/api/works/${work}/comments/browser/${token}`),'未读取会话');
natural=new late.pageWindow.XMLHttpRequest();natural.open('GET',`https://www.douyin.com/aweme/v1/web/comment/list/?aweme_id=${work}`);natural.send();
await until(()=>late.sent.length===2&&late.sent[1].sent,'合成评论请求未发起');
late.cancelOnServer();late.sent[1].onerror();
await until(()=>req(late,'POST','/api/comment-accounts/blocked'),'取消后的迟到风险信号未上报');
assert.equal(json(req(late,'POST','/api/comment-accounts/blocked')).fingerprint,expected);
await until(()=>req(late,'POST',`/api/works/${work}/comments/browser/${token}/finish`),'已取消会话未尝试结束');late.dispose();

// A nonzero platform response must report blocked synchronously, even if the user immediately stops.
const nonzero=browser(`https://www.douyin.com/video/${work}#library-comments=${token}`,goodCookies(),{blockedFail:true});
await until(()=>req(nonzero,'GET',`/api/works/${work}/comments/browser/${token}`),'未读取会话');
natural=new nonzero.pageWindow.XMLHttpRequest();natural.open('GET',`https://www.douyin.com/aweme/v1/web/comment/list/?aweme_id=${work}`);natural.send();
await until(()=>nonzero.sent.length===2&&nonzero.sent[1].sent,'合成评论请求未发起');
nonzero.sent[1].status=200;nonzero.sent[1].responseText=JSON.stringify({status_code:8,comments:[],has_more:0,cursor:0});nonzero.sent[1].onload();
nonzero.find('停止补抓').click();
assert.ok(Object.keys(nonzero.storage).some(key=>key.startsWith('library_comment_block_')),'立即停止不能丢失本地受阻记录');
assert.ok(req(nonzero,'POST','/api/comment-accounts/blocked'),'立即停止不能丢失独立受阻上报');
assert.equal(req(nonzero,'POST',`/api/works/${work}/comments/browser/${token}/page`),undefined);nonzero.dispose();

// A normal stop or an expired server session is not a platform risk event.
const normal=browser(`https://www.douyin.com/video/${work}#library-comments=${token}`);
await until(()=>req(normal,'GET',`/api/works/${work}/comments/browser/${token}`),'未读取会话');
natural=new normal.pageWindow.XMLHttpRequest();natural.open('GET',`https://www.douyin.com/aweme/v1/web/comment/list/?aweme_id=${work}`);natural.send();
await until(()=>normal.sent.length===2&&normal.sent[1].sent,'合成评论请求未发起');normal.find('停止补抓').click();await wait();
assert.equal(req(normal,'POST','/api/comment-accounts/blocked'),undefined,'用户主动停止不等同风控');
assert.ok(!Object.keys(normal.storage).some(key=>key.startsWith('library_comment_block_')));normal.dispose();
const conflict=browser(`https://www.douyin.com/video/${work}#library-comments=${token}`,goodCookies(),{claimConflict:true});
await until(()=>req(conflict,'POST',`/api/works/${work}/comments/browser/${token}/claim`),'未请求领取会话');await wait();
assert.equal(req(conflict,'POST','/api/comment-accounts/blocked'),undefined,'网站 409 不等同平台风控');
assert.ok(!Object.keys(conflict.storage).some(key=>key.startsWith('library_comment_block_')));conflict.dispose();

// Once a hash session is claimed, a page cannot issue XHR without a permit.
const denied=browser(`https://www.douyin.com/video/${work}#library-comments=${token}`);denied.denyPermit();
await until(()=>req(denied,'GET',`/api/works/${work}/comments/browser/${token}`),'未读取已领取的会话');
let original=new denied.pageWindow.XMLHttpRequest();original.open('GET',`https://www.douyin.com/aweme/v1/web/comment/list/?aweme_id=${work}`);original.send();
await until(()=>req(denied,'POST',`/api/works/${work}/comments/browser/${token}/permit`),'未请求预留许可');
assert.equal(denied.sent.length,1,'没有许可不能发采集 XHR');
const claim=req(denied,'POST',`/api/works/${work}/comments/browser/${token}/claim`), claimBody=json(claim);
assert.equal(claimBody.fingerprint,expected);assert.match(claimBody.client_id,/^[a-f0-9]{32}$/);
const permitRequest=req(denied,'POST',`/api/works/${work}/comments/browser/${token}/permit`);
assert.equal(permitRequest.headers['X-Cloudrift-Comment-Client'],claimBody.client_id);
assert.equal(permitRequest.headers['X-Cloudrift-Comment-Identity'],expected);
assert.deepEqual(json(permitRequest),{root_id:'',cursor:0});
assert.ok(denied.requests.indexOf(claim)<denied.requests.indexOf(permitRequest));
assert.ok(!JSON.stringify(denied.requests).includes('test-session-private'));
denied.dispose();

// In-flight identity changes must abort and prevent uploading the late page.
const changed=browser(`https://www.douyin.com/video/${work}#library-comments=${token}`);
await until(()=>req(changed,'GET',`/api/works/${work}/comments/browser/${token}`),'未读取已领取的会话');
original=new changed.pageWindow.XMLHttpRequest();original.open('GET',`https://www.douyin.com/aweme/v1/web/comment/list/?aweme_id=${work}`);original.send();
await until(()=>changed.sent.length===2&&changed.sent[1].sent,'许可后未开始合成请求');
changed.doc.visibilityState='hidden';
changed.setCookies([cookie('uid_tt','different-account'),cookie('sessionid','different-session')]);
for(const {fn,delay} of changed.intervals.values())if(delay>=900&&delay<=1100)fn();
await until(()=>changed.sent[1].aborted,'切换账号未中止在途请求');
assert.equal(req(changed,'POST',`/api/works/${work}/comments/browser/${token}/page`),undefined,'切号后不可上传迟到评论');
changed.dispose();
const hiddenChallenge=browser(`https://www.douyin.com/video/${work}#library-comments=${token}`);
await until(()=>req(hiddenChallenge,'GET',`/api/works/${work}/comments/browser/${token}`),'未读取已领取的会话');
original=new hiddenChallenge.pageWindow.XMLHttpRequest();original.open('GET',`https://www.douyin.com/aweme/v1/web/comment/list/?aweme_id=${work}`);original.send();
await until(()=>hiddenChallenge.sent.length===2&&hiddenChallenge.sent[1].sent,'合成评论请求未发起');
hiddenChallenge.doc.visibilityState='hidden';hiddenChallenge.setCaptcha(true);
for(const {fn,delay} of hiddenChallenge.intervals.values())if(delay===250)fn();
await until(()=>hiddenChallenge.sent[1].aborted,'后台检测到验证时未中止在途请求');
assert.ok(req(hiddenChallenge,'POST','/api/comment-accounts/blocked'),'后台验证必须上报全局暂停');
assert.equal(req(hiddenChallenge,'POST',`/api/works/${work}/comments/browser/${token}/page`),undefined,'验证后不可上传迟到评论');
hiddenChallenge.dispose();
console.log('PASS: 账号绑定、许可及切号保护');
