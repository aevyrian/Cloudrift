import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
const { createCommentsPanel } = createRequire(import.meta.url)('../app/static/app.js');

class Node {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.events = {}; this.dataset = {}; this.hidden = false; this.parent = null; this.focused = false; this.textContent = ''; }
  append(...nodes) { for (const n of nodes) { n.parent = this; this.children.push(n); } }
  replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(n => n !== this); this.parent = null; }
  setAttribute(name, value) { this[name] = value; }
  addEventListener(type, fn) { this.events[type] = fn; }
  focus() { this.focused = true; }
  click() { return this.events.click?.({ target: this }); }
}
const doc = { createElement: tag => new Node(tag), body: new Node('body') };
const element = (tag, cls, text) => Object.assign(new Node(tag), { className: cls || '', textContent: text === undefined ? '' : String(text) });
const deferred = [];
const calls = [];
const api = (path, method = 'GET', body) => { calls.push({ path, method, body }); return new Promise((resolve, reject) => deferred.push({ resolve, reject })); };
const revoked = [];
const blobs = [];
const panel = createCommentsPanel({ document: doc, element, api, binary: async path => { blobs.push(path); return {}; }, objectURL: { createObjectURL: () => 'blob:local', revokeObjectURL: url => revoked.push(url) }, formatDate: () => '日期' });
const card = new Node('article'), button = new Node('button');
const status = { operation: 'unsaved', integrity: 'unsaved', total: 0, replies: 0, reason: '', saved_at: null, platform_total: null };
const respond = (data) => deferred.shift().resolve(data);
const wait = () => new Promise(resolve => setImmediate(resolve));
const flatten = node => [node, ...node.children.flatMap(flatten)];
panel.open({ id: 'legacy-a' }, card, button);
assert.equal(calls[0].method, 'GET', '打开评论只能读取本地');
respond({ work_id: null, can_save: false, status, items: [], next_offset: null }); await wait();
assert.match(flatten(card).map(n => n.textContent).join(' '), /暂时无法识别原作品/);
assert.match(flatten(card).map(n => n.textContent).join(' '), /助手采集通道.*服务器通道/, '评论面板须说明两个采集通道');
assert.ok(!flatten(card).some(n => n.textContent === '保存评论'), '身份不明时不能采集');
panel.close(true); assert.equal(button.focused, true);

panel.open({ id: '1234567890123456789' }, card, button);
respond({ work_id: '1234567890123456789', can_save: true, status: { ...status, channel: 'assistant' }, items: [], next_offset: null }); await wait();
assert.match(flatten(card).map(n => n.textContent).join(' '), /助手采集通道/, '状态须标明助手采集通道');
const save = flatten(card).find(n => n.textContent === '保存评论'); assert.ok(save);
save.click(); assert.equal(calls.at(-1).method, 'POST'); assert.equal(calls.at(-1).body.action, 'save');
respond({}); await wait();
respond({ work_id: '1234567890123456789', can_save: true, status: { ...status, operation: 'stopped', integrity: 'partial', total: 1 }, items: [{ id: '9', text: '<img onerror=alert(1)>', nickname: '作者', root_id: '', created_at: 0, likes: 0, reply_count: 1, attachments: [{ id: '10', kind: 'image', status: 'saved' }] }], next_offset: null }); await wait();
assert.ok(flatten(card).some(n => n.textContent === '<img onerror=alert(1)>'), '评论正文应为纯文本');
assert.ok(flatten(card).some(n => n.textContent === '继续保存'));
assert.deepEqual(blobs, ['/api/comment-assets/10']);
assert.ok(flatten(card).some(n => n.textContent.includes('回复')));
flatten(card).find(n => n.textContent === '查看 1 条回复').click();
assert.match(calls.at(-1).path, /root_id=9/);
respond({items:[{id:'11',nickname:'回复者',text:'回复内容',reply_target_missing:true,attachments:[]}],next_offset:null}); await wait();
assert.ok(flatten(card).some(n => n.textContent === '回复内容'));
assert.ok(flatten(card).some(n => n.textContent === '上级评论未保存'));
flatten(card).find(n => n.textContent === '导出含图片 ZIP').click(); await wait();
assert.equal(blobs.at(-1), '/api/works/1234567890123456789/comments/export?format=zip');
const sheet = flatten(card).find(n => n.className === 'comments-panel');
let prevented = false, stopped = false; sheet.events.keydown({ key:'Escape', preventDefault(){prevented=true;}, stopPropagation(){stopped=true;} });
assert.ok(prevented && stopped && button.focused, 'Esc 关闭评论并将焦点返回入口');
assert.ok(revoked.includes('blob:local'));

panel.open({ id: 'one' }, card, button);
panel.open({ id: 'two' }, card, button);
respond({ work_id: 'one', can_save: true, status, items: [{ id: 'old', text: '旧作品', attachments: [] }], next_offset: null }); await wait();
assert.ok(!flatten(card).some(n => n.textContent === '旧作品'), '旧作品响应不可覆盖新作品');
respond({ work_id: 'two', can_save: true, status, items: [], next_offset: null }); await wait();
assert.equal(panel.isOpenFor(card), true);
panel.close(false); assert.equal(panel.isOpenFor(card), false);
panel.open({ id: '1234567890123456789' }, card, button);
respond({ work_id: '1234567890123456789', can_save: true, status: { ...status, operation:'failed', reason:'读取失败', channel:'server' }, items: [], next_offset:null }); await wait();
assert.match(flatten(card).map(n => n.textContent).join(' '), /服务器通道/, '回退后状态须标明服务器通道');
const retry = flatten(card).find(n => n.textContent === '重试'); assert.ok(retry, '首次失败应提供重试');
const rescan = flatten(card).find(n => n.textContent === '从头重新扫描'); assert.ok(rescan, '失败后应可选择从头扫描');
rescan.click(); assert.equal(calls.at(-1).body.action, 'update', '从头扫描应发 update 保留已有评论');
respond({}); await wait(); respond({ work_id:'1234567890123456789', can_save:true, status:{ ...status, operation:'failed' }, items:[], next_offset:null }); await wait();
flatten(card).find(n => n.textContent === '重试').click(); assert.equal(calls.at(-1).body.action, 'continue', '重试应保留未完成进度');
respond({}); await wait(); respond({ work_id:'1234567890123456789', can_save:true, status, items:[], next_offset:null }); await wait(); panel.close();
panel.open({ id:'1234567890123456789' }, card, button);
respond({ can_save:true, status:{...status, operation:'stopped', integrity:'partial', total:1}, items:[{id:'5',text:'原评论',reply_count:1,attachments:[]}], next_offset:null });await wait();
flatten(card).find(n=>n.textContent==='继续保存').click();deferred.shift().reject(new Error('提交失败'));await wait();
flatten(card).find(n=>n.textContent==='查看 1 条回复').click();assert.match(calls.at(-1).path,/root_id=5/, '提交失败后旧评论仍能展开回复');
respond({items:[{id:'6',text:'回复',attachments:[]}],next_offset:50});await wait();
flatten(card).find(n=>n.textContent==='继续保存').click();respond({});await wait();deferred.shift().reject(new Error('读取失败'));await wait();
flatten(card).find(n=>n.textContent==='加载更多回复').click();assert.match(calls.at(-1).path,/offset=50&limit=50&root_id=5/, '刷新失败后旧评论仍能继续读回复');
respond({items:[],next_offset:null});await wait();panel.close();
const realSetTimeout = globalThis.setTimeout, realClearTimeout = globalThis.clearTimeout;
const timers = []; globalThis.setTimeout = fn => { timers.push(fn); return timers.length; }; globalThis.clearTimeout = () => {};
const item = i => ({ id: String(i), text: `comment-${i}`, attachments: [] });
const page = (first, last, operation, next) => ({ can_save: true, status: { ...status, integrity: 'partial', operation, total: 150 }, items: Array.from({ length: last-first+1 }, (_, i) => item(first+i)), next_offset: next });
panel.open({ id:'1234567890123456789' }, card, button);
respond(page(1,50,'running',50)); await wait();
const more = flatten(card).find(n => n.textContent === '加载更多评论'); more.click(); const oldMore = deferred.shift();
timers.shift()(); const polling = deferred.shift(); polling.resolve(page(1,50,'finished',50)); await wait();
const reset = deferred.shift(); assert.ok(reset);
oldMore.resolve(page(51,100,'running',100)); await wait();
const countBefore = calls.length; more.click(); assert.equal(calls.length, countBefore, '重置加载中不能发出旧游标请求');
reset.resolve(page(1,50,'finished',50)); await wait(); more.click();
assert.match(calls.at(-1).path, /offset=50/); respond(page(51,100,'finished',100)); await wait();
const texts = flatten(card).filter(n => n.className === 'comment-text').map(n => n.textContent);
assert.equal(texts.length,100); assert.ok(texts.includes('comment-51'), '刷新和旧翻页交错不能漏掉中间一页');
panel.close(); globalThis.setTimeout = realSetTimeout; globalThis.clearTimeout = realClearTimeout;
console.log('Comments UI checks passed: explicit collection, missing identity, safe content, local assets and stale responses.');
