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
  click() { return this.events.click?.({ target: this }); }
}
const flatten = node => [node, ...node.children.flatMap(flatten)];
const wait = () => new Promise(resolve => setImmediate(resolve));
const calls = [], workId = '1234567890123456789', token = 'a'.repeat(32);
let status = { operation: 'failed', integrity: 'partial', total: 0, replies: 0 }, failNext = false;
let accounts = {paused:false,primary:{bound:true},secondary:{bound:true},identity_salt:'private-salt'};
const api = async (path, method = 'GET', body) => {
  calls.push({ path, method, body });
  if (path === '/api/comment-accounts') return accounts;
  if (path.endsWith('/browser')) { if (failNext) { failNext = false; throw new Error('模拟失败'); } status = { ...status, operation: 'browser', channel: 'browser' }; return { work_id: workId, token, status: 'waiting' }; }
  if (path.endsWith('/cancel')) { status = { ...status, operation: 'stopped' }; return {}; }
  return { work_id: workId, can_save: true, status, items: [], next_offset: null };
};
const panel = createCommentsPanel({ document: { body: new Node('body') }, element: (...args) => new Node(...args), api, binary: async () => ({}), objectURL: {}, formatDate: () => '', openWindow: () => null });
const card = new Node('article'), trigger = new Node('button');
panel.open({ id: 'legacy-local-id' }, card, trigger); await wait();
flatten(card).find(node => node.textContent === '小号浏览器：重试').click(); await wait();
assert.deepEqual(calls.find(call => call.path.endsWith('/browser')).body, { action: 'continue', account_role: 'secondary', allow_primary: false });
const link = flatten(card).find(node => node.textContent === '打开抖音继续补抓 ↗');
assert.equal(link.href, `https://www.douyin.com/video/${workId}#library-comments=${token}`);
assert.ok(flatten(card).some(node => node.textContent.includes('浏览器阻止')));
assert.ok(flatten(card).some(node => node.textContent === '停止保存'));
panel.close();
panel.open({ id: 'legacy-local-id' }, card, trigger); await wait();
assert.ok(flatten(card).some(node => node.textContent === '打开抖音继续补抓 ↗'), '重新打开面板时保留链接');
assert.ok(!flatten(card).some(node => node.tagName === 'SELECT'), '进行中不能重复启动');
flatten(card).find(node => node.textContent === '停止保存').click(); await wait();
assert.ok(calls.some(call => call.path.endsWith('/cancel')));
panel.close();
status = {operation:'unsaved',integrity:'unsaved',total:0,replies:0};
panel.open({id:'legacy-local-id'},card,trigger);await wait();
let source = flatten(card).find(node => node.tagName === 'SELECT');
assert.equal(source.value,'secondary', '重开面板默认小号');
source.value='primary-browser';source.events.change();
flatten(card).find(node=>node.textContent==='大号浏览器：保存评论').click();await wait();
assert.deepEqual(calls.filter(call=>call.path.endsWith('/browser')).at(-1).body,{action:'save',account_role:'primary',allow_primary:true});
flatten(card).find(node=>node.textContent==='停止保存').click();await wait();
source=flatten(card).find(node=>node.tagName==='SELECT');assert.equal(source.value,'secondary','大号成功后同一面板应复位');
flatten(card).find(node=>node.textContent==='小号浏览器：继续保存').click();await wait();
assert.deepEqual(calls.filter(call=>call.path.endsWith('/browser')).at(-1).body,{action:'continue',account_role:'secondary',allow_primary:false});
flatten(card).find(node=>node.textContent==='停止保存').click();await wait();
source=flatten(card).find(node=>node.tagName==='SELECT');source.value='primary-browser';source.events.change();failNext=true;
flatten(card).find(node=>node.textContent==='大号浏览器：继续保存').click();await wait();
source=flatten(card).find(node=>node.tagName==='SELECT');assert.equal(source.value,'secondary','大号失败后同一面板应复位');
flatten(card).find(node=>node.textContent==='小号浏览器：继续保存').click();await wait();
assert.deepEqual(calls.filter(call=>call.path.endsWith('/browser')).at(-1).body,{action:'continue',account_role:'secondary',allow_primary:false});
panel.close();
for (accounts of [{paused:true,reason:'需要处理验证',primary:{bound:true},secondary:{bound:true}}, {paused:false,primary:{bound:true},secondary:{bound:false}}]) {
  status = {operation:'unsaved',integrity:'unsaved',total:0,replies:0};panel.open({id:'legacy-local-id'},card,trigger);await wait();
  source=flatten(card).find(node=>node.tagName==='SELECT');assert.equal(source.value,'secondary');
  const start=flatten(card).find(node=>node.textContent==='小号浏览器：保存评论');assert.equal(start.disabled,true);
  const before=calls.length;start.click();await wait();assert.equal(calls.length,before,'暂停或未绑定不可发起');
  source.value='primary-core';source.events.change();flatten(card).find(node=>node.textContent==='后台大号：保存评论').click();await wait();assert.equal(calls.length,before,'不能切换大号绕过暂停或绑定');
  assert.ok(!flatten(card).some(node=>node.textContent.includes('private-salt')));panel.close();
}
