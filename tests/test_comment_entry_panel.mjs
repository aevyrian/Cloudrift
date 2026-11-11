import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
const { createCommentsPanel } = createRequire(import.meta.url)('../app/static/app.js');
class Node {
  constructor(tag, cls = '', text = '') { this.tagName = tag.toUpperCase(); this.className = cls; this.textContent = text; this.children = []; this.events = {}; }
  append(...nodes) { for (const node of nodes) { node.parent = this; this.children.push(node); } }
  replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(node => node !== this); }
  setAttribute(name, value) { this[name] = value; }
  addEventListener(name, fn) { this.events[name] = fn; }
  focus() {}
  click() { return this.events.click?.(); }
}
const flat = node => [node, ...node.children.flatMap(flat)];
const tick = () => new Promise(resolve => setImmediate(resolve));
for (const channel of ['core', 'browser']) {
  const calls = [], id = '7431234567890123456'; let settings = 0;
  const api = async (path, method = 'GET', body) => {
    calls.push({path, method, body});
    if (path === '/api/comment-accounts') return {paused:false,primary:{bound:true},secondary:{bound:true}};
    if (path.endsWith('/browser')) return {work_id:id,token:'a'.repeat(32)};
    return {can_save:true,items:[],next_offset:null,status:{operation:'unsaved',integrity:'unsaved',total:0}};
  };
  const panel = createCommentsPanel({document:{body:new Node('body')},element:(...args)=>new Node(...args),api,binary:async()=>{},objectURL:{},formatDate:()=>'',openWindow:()=>null,openSettings:()=>settings++,commentEntry:{readChannel:async()=>({channel}),selection:async()=>channel==='core'?'primary-core':'secondary'}});
  const card = new Node('article'); panel.open({id},card,new Node('button')); await tick();
  assert.equal(flat(card).some(n=>n.tagName==='SELECT'),false);
  flat(card).find(n=>n.textContent==='通道设置').click(); assert.equal(settings,1);
  await flat(card).find(n=>n.textContent==='加载更新').click(); await tick();
  const post = calls.find(c=>c.method==='POST');
  assert.equal(post.path,`/api/works/${id}/comments${channel==='browser'?'/browser':''}`);
  assert.deepEqual(post.body,{action:'update',account_role:channel==='core'?'primary':'secondary',allow_primary:channel==='core'});
  assert.ok(flat(card).some(n=>n.textContent==='本次用大号浏览器'));
  panel.close();
}
console.log('Comment entry panel channel routing passed');
