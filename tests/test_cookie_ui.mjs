import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source=readFileSync(new URL('../app/static/app.js',import.meta.url),'utf8');
const start=source.indexOf('  function showCookieSync('),end=source.indexOf('  function paintSound()',start);
assert.ok(start>=0&&end>start,'网站应提供一键同步的实际处理流程');
const html=readFileSync(new URL('../app/static/index.html',import.meta.url),'utf8');
assert.match(html,/id="cookie-sync-button"/,'网站应显示一键同步按钮');
const nonce='A'.repeat(43),tick=()=>new Promise(resolve=>setImmediate(resolve));

function makeApp({blocked=false}={}){
  const nodes=Object.fromEntries(['cookie-sync-button','cookie-sync-status','cookie-sync-message','cookie-sync-link','cookie-state','cookie-account','clear-cookie'].map(id=>[id,{hidden:false,disabled:false,textContent:'',className:'',removeAttribute(name){delete this[name]},addEventListener(name,fn){this[name]=fn}}]));
  const state={token:'private-test-token',cookieSyncGeneration:0},requests=[],popups=[];
  let getResponse=()=>Promise.resolve({status:'success'}),created={id:nonce,status:'waiting'};
  const context=vm.createContext({state,$:id=>nodes[id],Date,URL,
    setTimeout:fn=>{queueMicrotask(fn);return 1},clearTimeout(){},
    loadSettings:async()=>({}),
    window:{focus(){},open(){if(blocked)return null;const tab={opener:{},location:{href:'about:blank'},closed:false,close(){this.closed=true}};popups.push(tab);return tab},confirm:()=>false},
    api:async(path,method='GET')=>{requests.push({path,method});if(method==='POST'){assert.ok(blocked||popups.length===1,'必须在异步请求前打开窗口');return created}return getResponse()},
  });
  vm.runInContext(source.slice(start,end)+'\nglobalThis.syncBrowserCookie=syncBrowserCookie;',context);
  return {nodes,state,requests,popups,context,setResponse(fn){getResponse=fn},setCreated(value){created=value}};
}

{
  const b=makeApp();let step=0;b.setResponse(()=>Promise.resolve([{status:'validating'},{status:'waiting_apply'},{status:'success'}][step++]));
  await b.context.syncBrowserCookie();
  assert.equal(b.popups[0].opener,null,'抖音窗口不能控制收藏库页面');
  assert.equal(b.popups[0].location.href,`https://www.douyin.com/jingxuan#library-cookie-sync=${nonce}`,'目标只包含一次性标识');
  assert.equal(b.requests.filter(r=>r.method==='POST').length,1);
  assert.equal(b.nodes['cookie-state'].textContent,'配置生效中','验证成功后刷新状态');
  assert.equal(b.nodes['cookie-sync-button'].disabled,false);
  assert.doesNotMatch(b.popups[0].location.href,/private-test-token/,'地址不能包含管理员密钥');
}
{
  const b=makeApp();b.setCreated({id:nonce,status:'waiting_apply'});await b.context.syncBrowserCookie();
  assert.equal(b.popups[0].closed,true,'已有进行中的任务应关闭预打开的空白页');
  assert.equal(b.popups[0].location.href,'about:blank','已有进行中任务不能重复打开抖音读取 Cookie');
  assert.equal(b.requests.filter(r=>r.method==='GET').length,1,'继续查看已有任务的结果');
}
{
  const b=makeApp({blocked:true});let resolve;const waiting=new Promise(r=>resolve=r);b.setResponse(()=>waiting);
  const first=b.context.syncBrowserCookie();await tick();const second=b.context.syncBrowserCookie();await tick();
  assert.equal(b.requests.filter(r=>r.method==='POST').length,1,'重复点击不能创建第二个请求');
  assert.equal(b.nodes['cookie-sync-link'].hidden,false,'弹窗受阻时提供可点击入口');
  assert.equal(b.nodes['cookie-sync-link'].href,`https://www.douyin.com/jingxuan#library-cookie-sync=${nonce}`);
  resolve({status:'failed',message:'sessionid=secret-must-not-be-rendered'});await Promise.all([first,second]);
  assert.doesNotMatch(b.nodes['cookie-sync-message'].textContent,/secret-must-not-be-rendered/,'错误响应不能回显 Cookie');
  assert.notEqual(b.nodes['cookie-state'].textContent,'配置生效中','验证失败不得显示已保存');
}
{
  const b=makeApp();let resolve;const waiting=new Promise(r=>resolve=r);b.setResponse(()=>waiting);
  const job=b.context.syncBrowserCookie();await tick();b.state.token='another-token';resolve({status:'success'});await job;
  assert.notEqual(b.nodes['cookie-state'].textContent,'配置生效中','切换管理员密钥后旧结果不能更新界面');
}
{
  let resolve,disconnections=0;const pending=new Promise(r=>resolve=r),state={token:'old-test-token'};
  const context=vm.createContext({state,AbortSignal,fetch:()=>pending,disconnect:()=>{disconnections++}});
  vm.runInContext(source.slice(source.indexOf('  async function api('),source.indexOf('  function empty('))+'\nglobalThis.api=api;',context);
  const request=context.api('/api/cookie-sync/old');state.token='new-test-token';resolve({ok:false,status:401,json:async()=>({detail:'请重新登录'})});
  await assert.rejects(request);assert.equal(disconnections,0,'旧密钥请求的迟到401不能断开新登录');
}
console.log('Cookie sync UI checks passed: success, blocked popup, failure, duplicate and stale session.');
