import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import vm from 'node:vm';
const root=process.cwd();
const source=readFileSync(root+'/app/static/app.js','utf8');
const tick=()=>new Promise(r=>setImmediate(r));
const {createCommentsPanel}=createRequire(import.meta.url)(root+'/app/static/app.js');
class Node {
  constructor(tag, cls='', text=''){Object.assign(this,{tagName:tag.toUpperCase(),className:cls,textContent:text,children:[],events:{},hidden:false});}
  append(...nodes){for(const n of nodes){n.parent=this;this.children.push(n);}}
  replaceChildren(...nodes){for(const n of this.children)n.parent=null;this.children=[];this.append(...nodes);}
  remove(){if(this.parent)this.parent.children=this.parent.children.filter(n=>n!==this);this.parent=null;}
  setAttribute(k,v){this[k]=v;}
  addEventListener(k,v){this.events[k]=v;}
  focus(){}
  click(){return this.events.click?.();}
}
const flatten=n=>[n,...n.children.flatMap(flatten)];
const pending=[],made=[],revoked=[];
const panel=createCommentsPanel({document:{body:new Node('body')},element:(...a)=>new Node(...a),api:()=>new Promise(resolve=>pending.push(resolve)),binary:async()=>({size:20*1024*1024}),objectURL:{createObjectURL(){const id='blob:'+made.length;made.push(id);return id;},revokeObjectURL:u=>revoked.push(u)},formatDate:()=>''});
const card=new Node('article'),trigger=new Node('button');
const result={can_save:true,status:{integrity:'finished-visible',operation:'finished',total:1,replies:0},items:[{id:'1',text:'image comment',attachments:[{id:'a',kind:'image',status:'saved'}]}],next_offset:null};
panel.open({id:'1234567890123456789'},card,trigger);pending.shift()(result);await tick();
for(let i=0;i<3;i++){const op=flatten(card).find(n=>n.textContent==='更新评论').click();pending.shift()({});await tick();pending.shift()(result);await op;await tick();}
assert.equal(flatten(card).filter(n=>n.tagName==='IMG').length,1);
assert.equal(made.length,4);assert.equal(revoked.length,3,'每次重载应释放旧图片');

panel.close();assert.equal(revoked.length,4);

{
  const replies=[], images=[], made2=[], panel2=createCommentsPanel({document:{body:new Node('body')},element:(...a)=>new Node(...a),api:()=>new Promise(resolve=>replies.push(resolve)),binary:()=>new Promise(resolve=>images.push(resolve)),objectURL:{createObjectURL(){made2.push('blob:late');return 'blob:late';},revokeObjectURL(){}},formatDate:()=>''});
  const second=new Node('article');panel2.open({id:'1234567890123456789'},second,trigger);
  replies.shift()({...result,items:[{id:'1',text:'old',reply_count:1,attachments:[{id:'a',kind:'image',status:'saved'}]}]});await tick();
  flatten(second).find(n=>n.textContent==='查看 1 条回复').click();const oldReply=replies.shift();
  flatten(second).find(n=>n.textContent==='更新评论').click();replies.shift()({});await tick();
  replies.shift()({...result,items:[{id:'1',text:'new',attachments:[]}]});await tick();
  oldReply({items:[{id:'2',text:'old reply',attachments:[]}],next_offset:null});images.shift()({});await tick();
  assert.equal(made2.length,0,'旧页迟到图片不得生成新 URL');
  assert.ok(!flatten(second).some(n=>n.textContent==='old reply'),'旧页迟到回复不能加入新页');panel2.close();
}
{
  const requests=[], binaryRequests=[], revokedExport=[], scheduled=[], oldSetTimeout=globalThis.setTimeout;
  globalThis.setTimeout=fn=>{scheduled.push(fn);return 1;};
  const panel3=createCommentsPanel({document:{body:new Node('body')},element:(...a)=>new Node(...a),api:()=>new Promise(resolve=>requests.push(resolve)),binary:()=>new Promise(resolve=>binaryRequests.push(resolve)),objectURL:{createObjectURL:()=> 'blob:export',revokeObjectURL:url=>revokedExport.push(url)},formatDate:()=>''});
  const third=new Node('article');panel3.open({id:'1234567890123456789'},third,trigger);requests.shift()({...result,items:[]});await tick();
  const exportButton=flatten(third).find(n=>n.textContent==='导出含图片 ZIP');exportButton.click();exportButton.click();
  assert.equal(binaryRequests.length,1,'导出未完成时不能重复发请求');binaryRequests.shift()({});await tick();panel3.close();
  assert.equal(revokedExport.length,0,'关闭面板不能立即中断已启动下载');scheduled.shift()();assert.deepEqual(revokedExport,['blob:export']);globalThis.setTimeout=oldSetTimeout;
}

const storage=new Map([['library_access_key','test-old-key']]),nodes={};
const node=id=>nodes[id]??=Object.assign(new Node('div'),{value:'',reset(){},replaceChildren(){}});
node('new-admin-key').value='test-new-key';node('repeat-admin-key').value='test-new-key';
const state={token:'test-old-key',generation:0,navigation:0,auth:{setup_required:false}};
let resolveKey;
const ctx=vm.createContext({state,$:node,document:{querySelectorAll:()=>[]},switchView(){},taskView:{update(){}},connectionMode(){},keyError:()=>'',api:()=>new Promise(r=>resolveKey=r),loadSettings:async()=>{},notice(){},localStorage:{getItem:k=>storage.get(k),removeItem:k=>storage.delete(k),setItem:(k,v)=>storage.set(k,v)}});
vm.runInContext(source.slice(source.indexOf('  function disconnect('),source.indexOf('  function connectionMode('))+source.slice(source.indexOf("  $('admin-key-form').addEventListener"),source.indexOf("  $('reveal-token').addEventListener"))+';globalThis.disconnect=disconnect;',ctx);
const submit=node('admin-key-form').events.submit({preventDefault(){},target:{querySelector:()=>({disabled:false}),reset(){}}});
ctx.disconnect();assert.equal(state.token,'');assert.equal(storage.get('library_access_key'),undefined);
resolveKey({});await submit;
assert.equal(state.token,'','退出后迟到的改密响应不能重新登录');assert.equal(storage.get('library_access_key'),undefined);
state.token='test-old-key';storage.set('library_access_key','test-old-key');node('new-admin-key').value='test-new-key';node('repeat-admin-key').value='test-new-key';
const again=node('admin-key-form').events.submit({preventDefault(){},target:{querySelector:()=>({disabled:false}),reset(){}}});
ctx.disconnect();state.token='test-old-key';storage.set('library_access_key','test-old-key');resolveKey({});await again;
assert.equal(state.token,'test-old-key','退出后使用同一密钥重新登录仍不能被旧请求覆盖');
{
  const saved=new Map([['library_access_key','old-key']]), n={};
  const get=id=>n[id]??=Object.assign(new Node('div'),{value:'',reset(){},replaceChildren(){}});
  get('new-admin-key').value=get('repeat-admin-key').value='new-key';
  const s={token:'old-key',generation:0,navigation:0,auth:{setup_required:false}};let resolvePut,resolveGet;
  const concurrent=vm.createContext({state:s,$:get,document:{querySelectorAll:()=>[]},switchView(){},taskView:{update(){}},connectionMode(){},keyError:()=>'',loadSettings:async()=>{},notice(){},AbortSignal,
    localStorage:{getItem:k=>saved.get(k),removeItem:k=>saved.delete(k),setItem:(k,v)=>saved.set(k,v)},
    fetch:(path,options)=>new Promise(resolve=>{if(options.method==='PUT')resolvePut=resolve;else resolveGet=resolve;})});
  vm.runInContext(source.slice(source.indexOf('  async function api('),source.indexOf('  function empty('))+source.slice(source.indexOf('  function disconnect('),source.indexOf('  function connectionMode('))+source.slice(source.indexOf("  $('admin-key-form').addEventListener"),source.indexOf("  $('reveal-token').addEventListener"))+';globalThis.api=api;',concurrent);
  const changing=get('admin-key-form').events.submit({preventDefault(){},target:{querySelector:()=>({disabled:false}),reset(){}}});
  const stale=concurrent.api('/api/status').catch(()=>{});
  resolveGet({ok:false,status:401,json:async()=>({detail:'登录已失效'})});await stale;
  resolvePut({ok:true,status:200,json:async()=>({})});await changing;
  assert.equal(s.token,'');assert.equal(saved.get('library_access_key'),undefined,'并发 401 登出后迟到改密结果不能重新登录');
}
console.log('UI resource and authentication race checks passed.');
