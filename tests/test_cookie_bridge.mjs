import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';

const source=readFileSync(new URL('../userscripts/douyin-send.user.js',import.meta.url),'utf8');
const nonce='B'.repeat(43),tick=()=>new Promise(resolve=>setImmediate(resolve));
function browser(hash=`#library-cookie-sync=${nonce}`,withCookie=true){
  const requests=[],reads=[],elements=[],closes=[];
  function node(tag){
    const el={tag,style:{},dataset:{},classList:{toggle(){},add(){},remove(){}},children:[],hidden:false,open:false,textContent:'',value:'',disabled:false,parentElement:null,offsetHeight:300,
      append(...items){for(const item of items){item.parentElement=this;this.children.push(item)}},replaceChildren(...items){this.children=[];this.append(...items)},
      setAttribute(){},addEventListener(){},contains(){return false},matches(){return false},closest(){return null},querySelector(){return null},querySelectorAll(){return []},
      getBoundingClientRect(){return {width:0,height:0,top:0,bottom:0,left:0}},attachShadow(){return node('shadow')},focus(){}};
    elements.push(el);return el;
  }
  const page=new URL(`https://www.douyin.com/jingxuan${hash}`),doc={body:node('body'),createElement:node,querySelector(){return null},querySelectorAll(){return []},addEventListener(){}};
  const context={URL,location:page,document:doc,history:{state:null,replaceState(_s,_t,url){page.href=new URL(url,page.href).href}},innerWidth:1200,innerHeight:800,
    GM_getValue:(key,fallback)=>({library_server:'http://nas.example:8090',library_token:'test-key'}[key]??fallback),GM_setValue(){},GM_registerMenuCommand(){},
    GM_xmlhttpRequest(request){requests.push(request)},ResizeObserver:class{observe(){}disconnect(){}},MutationObserver:class{observe(){}disconnect(){}},
    addEventListener(){},requestAnimationFrame(){return 1},cancelAnimationFrame(){},setTimeout(){return 1},clearTimeout(){},setInterval(){}};
  context.window={close(){closes.push(true)}};
  if(withCookie)context.GM_cookie=(action,details,done)=>reads.push({action,details,done});
  runInNewContext(source,context);
  return {page,requests,reads,elements,closes,respond(req,data,status=200){req.onload({status,responseText:JSON.stringify(data)})}};
}
const body=req=>JSON.parse(req.data);

{
  const b=browser();
  assert.equal(b.requests.length,1,'同步入口先查询服务端等待状态');
  assert.equal(b.requests[0].method,'GET');
  assert.equal(new URL(b.requests[0].url).pathname,`/api/cookie-sync/${nonce}`);
  assert.equal(b.reads.length,0,'尚未确认一次性请求时不能读取 Cookie');
  assert.equal(b.page.hash,'','同步标识读取后立即从地址栏移除');
  b.respond(b.requests[0],{status:'waiting'});await tick();
  assert.equal(b.reads.length,1);assert.equal(b.reads[0].action,'list');assert.equal(b.reads[0].details.url,'https://www.douyin.com/');
  b.reads[0].done([{name:'sid_tt',value:'fixture-token',domain:'.douyin.com',path:'/',httpOnly:true},{name:'extra',value:'ok',domain:'www.douyin.com',path:'/',httpOnly:false}],undefined);await tick();
  assert.equal(b.requests.length,2);assert.equal(b.requests[1].method,'POST');
  assert.deepEqual(body(b.requests[1]),{cookie:'sid_tt=fixture-token; extra=ok'},'包含 HttpOnly 且保持返回顺序');
  assert.ok(!b.requests[1].url.includes('fixture-token'),'Cookie 不能出现在 URL');
  b.respond(b.requests[1],{status:'validating'});await tick();
  assert.equal(b.closes.length,1,'提交成功后自动关闭同步页，不再显示横幅');
}
for(const status of [{status:'expired'},{status:'validating'},{status:'done'}]){
  const b=browser();b.respond(b.requests[0],status);await tick();assert.equal(b.reads.length,0,'请求不在等待状态不能读取');assert.equal(b.requests.length,1,'无有效请求不能发送 Cookie');
}
{
  const b=browser();b.respond(b.requests[0],{detail:'not allowed'},401);await tick();assert.equal(b.reads.length,0,'未授权不能读取');assert.equal(b.requests.length,1);
}
{
  const b=browser();b.respond(b.requests[0],{status:'waiting'});await tick();b.reads[0].done(undefined,new Error('permission denied'));await tick();
  assert.deepEqual(body(b.requests[1]),{error:'permission_denied'},'拒绝授权仅传固定错误');
}
{
  const b=browser();b.respond(b.requests[0],{status:'waiting'});await tick();b.reads[0].done(undefined,'permission denied');await tick();
  assert.deepEqual(body(b.requests[1]),{error:'permission_denied'},'字符串授权错误也要明确通知');
}
{
  const b=browser('#library-cookie-sync='+nonce,false);b.respond(b.requests[0],{status:'waiting'});await tick();
  assert.deepEqual(body(b.requests[1]),{error:'read_failed'},'缺少 API 时通知网站而不是假成功');
}
for(const cookies of [[],[{name:'not-session',value:'value',domain:'.douyin.com'}],[{name:'sessionid',value:'',domain:'.douyin.com'}]]){
  const b=browser();b.respond(b.requests[0],{status:'waiting'});await tick();b.reads[0].done(cookies);await tick();assert.deepEqual(body(b.requests[1]),{error:'not_logged_in'});
}
{
  const b=browser();b.respond(b.requests[0],{status:'waiting'});await tick();b.reads[0].done([{name:'sessionid',value:'ok\nInjected: header',domain:'.douyin.com'}]);await tick();assert.ok(!('cookie' in body(b.requests[1])),'非法控制符不能传入请求头');
}
{
  const b=browser();b.respond(b.requests[0],{status:'waiting'});await tick();b.reads[0].done([{name:'sessionid',value:'x'.repeat(30001),domain:'.douyin.com'}]);await tick();assert.deepEqual(body(b.requests[1]),{error:'read_failed'},'过大 Cookie 不发送');
}
{
  const b=browser('#library-cookie-sync=short');assert.equal(b.reads.length,0,'非法同步标识不读取 Cookie');assert.equal(b.requests.length,0);
}
console.log('Cookie bridge checks passed: preflight, HttpOnly, failures and scoped data.');
