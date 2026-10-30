import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const require = createRequire(import.meta.url);
const view = require('../app/static/task-view.js');
const webSource = readFileSync(new URL('../app/static/app.js', import.meta.url), 'utf8');
const html = readFileSync(new URL('../app/static/index.html', import.meta.url), 'utf8');
const { taskTitle, switchView } = require('../app/static/app.js');
const tasks = [
  { id: 'running', kind: 'author', status: 'downloading', total: 20, completed: 6, preview: [{ nickname: '山野慢镜头' }] },
  { id: 'failed', kind: 'author', status: 'failed', total: 10, completed: 2, preview: [{ nickname: '城市漫游' }] },
  { id: 'confirm', kind: 'detail', status: 'awaiting_confirmation', total: 1, completed: 0, preview: [{ desc: '海边' }] },
  { id: 'done', kind: 'author', status: 'completed', total: 2, completed: 2, preview: [{ nickname: '摄影笔记' }] },
  { id: 'paused', kind: 'detail', status: 'paused', total: 2, completed: 1, preview: [{ desc: '稍后继续' }] }
];
assert.deepEqual(view.filterTasks(tasks, 'attention', '', taskTitle).map(t => t.id), ['failed', 'confirm']);
assert.deepEqual(view.filterTasks(tasks, 'active', '稍后', taskTitle).map(t => t.id), ['paused']);
assert.equal(view.selectedTask(view.filterTasks(tasks, 'all', '山野', taskTitle), 'failed').id, 'running');
assert.equal(view.selectedTask([], 'failed'), undefined);

class Element {
  constructor(tag = 'div', text = '') { this.tagName = tag; this.textContent = text; this.className = ''; this.children = []; this.dataset = {}; this.style = {}; this.attributes = {}; this.events = {}; this.value = ''; this.hidden = false; this.open = false; this.classList = { add: name => { this.className += ` ${name}`; } }; }
  append(...items) { this.children.push(...items); }
  prepend(...items) { this.children.unshift(...items); }
  replaceChildren(...items) { this.children = [...items]; }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(name, callback) { this.events[name] = callback; }
  get lastElementChild() { return this.children.at(-1); }
  querySelector(selector) { return this.children.find(n => selector.startsWith('.') ? n.className.split(' ').includes(selector.slice(1)) : n.tagName === selector) || this.children.map(n => n.querySelector?.(selector)).find(Boolean); }
  click() { return this.events.click?.({ target: this }); }
  showModal() { this.open = true; }
  close() { this.open = false; }
}

function fixture({ saved = null, readError = false, writeError = false, onBatch } = {}) {
  const ids = Object.fromEntries(['task-list', 'task-detail', 'task-detail-dialog', 'task-dialog-body', 'task-query', 'task-filters', 'task-summary', 'task-layout-note', 'task-dialog-close', 'task-result-count', 'task-table-heading', 'open-submit'].map(id => [id, new Element()]));
  const choices = ['a', 'b'].map(value => { const button = new Element('button'); button.dataset.taskLayoutChoice = value; return button; });
  const writes = [];
  ids['task-bulk-actions']=new Element();
  globalThis.document = { getElementById: id => ids[id], createElement: tag => new Element(tag) };
  globalThis.localStorage = { getItem() { if (readError) throw Error('Storage unavailable'); return saved; }, setItem(key, value) { if (writeError) throw Error('Storage unavailable'); saved = value; writes.push([key, value]); } };
  globalThis.matchMedia = () => ({ matches: false });
  let requests = 0;
  globalThis.fetch = () => { requests++; throw Error('A layout change must not call the network'); };
  const application = new Element();
  const makeTaskCard = task => { const card = new Element('article'); card.task = task; const actions = new Element('div'); actions.className = 'record-actions'; card.append(actions); return card; };
  const controller = view.create({ root: { querySelectorAll: () => choices }, application, makeTaskCard, title: taskTitle, statusNames: {}, formatDate: () => '刚刚', onBatch });
  return { ids, choices, application, controller, writes, requests: () => requests };
}

try {
  const operations=[];const bulk=fixture({onBatch:async(action,ids)=>operations.push({action,ids})});bulk.controller.update(tasks);
  bulk.ids['task-filters'].children.find(b=>b.dataset.filter==='attention').click();
  await bulk.ids['task-bulk-actions'].children[0].click();assert.deepEqual(operations.at(-1),{action:'retry',ids:['failed']});
  await bulk.ids['task-bulk-actions'].children[2].click();assert.deepEqual(operations.at(-1),{action:'delete',ids:['failed','confirm']});
  let finishBatch;
  const pending = fixture({ onBatch: () => new Promise(resolve => { finishBatch = resolve; }) });
  pending.controller.update(tasks);
  const retry = pending.ids['task-bulk-actions'].children[0];
  const request = retry.click();
  assert.ok(pending.ids['task-bulk-actions'].children.every(button => button.disabled), '批量操作期间应阻止其他批量操作');
  pending.controller.update(tasks.map(task => task.id === 'running' ? { ...task, completed: 7 } : task));
  assert.equal(retry.disabled, true, '任务进度刷新期间，正在进行的批量操作必须保持禁用');
  await pending.ids['task-bulk-actions'].children[2].click();
  pending.ids['task-filters'].children.find(button => button.dataset.filter === 'attention').click();
  assert.equal(retry.disabled, true, '切换筛选期间，正在进行的批量操作必须保持禁用');
  finishBatch(); await request;
  assert.equal(retry.disabled, false, '批量操作结束后可再次操作');
  const normal = fixture();
  assert.equal(normal.application.dataset.taskLayout, 'a');
  normal.controller.update(tasks);
  assert.deepEqual(normal.writes, []);
  normal.ids['task-list'].children[0].querySelector('.record-actions').children.at(-1).click();
  assert.equal(normal.requests(), 0);
  normal.ids['task-query'].value = '山野';
  normal.ids['task-query'].events.input();
  assert.equal(normal.ids['task-detail'].children[1].task.id, 'running', '详情必须跟随可见搜索结果');
  normal.ids['task-query'].value = '';
  normal.ids['task-filters'].children.find(button => button.dataset.filter === 'completed').click();
  assert.equal(normal.ids['task-detail'].children[1].task.id, 'done', '筛选后不能继续显示已隐藏任务');
  const row = normal.ids['task-list'].children[0];
  normal.controller.update(structuredClone(tasks));
  assert.equal(normal.ids['task-list'].children[0], row, '重复轮询保留当前渲染与选择');
  normal.ids['task-query'].value = '不存在'; normal.ids['task-query'].events.input();
  assert.equal(normal.ids['task-detail'].hidden, true);
  assert.equal(fixture({ saved: 'b' }).application.dataset.taskLayout, 'a', '旧B偏好必须回到A');
  assert.equal(fixture({ saved: 'invalid' }).application.dataset.taskLayout, 'a');
  const unavailable = fixture({ readError: true, writeError: true });
  assert.equal(unavailable.application.dataset.taskLayout, 'a');
  assert.equal(unavailable.requests(), 0);
} finally {
  delete globalThis.document; delete globalThis.localStorage; delete globalThis.matchMedia; delete globalThis.fetch;
}

// A rejected request must dismiss private dialogs before the login screen returns.
try {
  for (const status of [401, 403]) {
    const session = fixture({ saved: 'b' });
    session.controller.update(tasks);
    session.ids['task-list'].children[0].querySelector('.record-actions').children.at(-1).click();
    assert.equal(session.ids['task-detail-dialog'].open, true);
    const inputs = ['access-key', 'script-token', 'cookie', 'confirm-key', 'legacy-key', 'new-admin-key', 'repeat-admin-key'];
    for (const id of [...inputs, 'connection', 'feed', 'admin-key-form', 'submit-dialog', 'access-eyebrow', 'access-title', 'access-description', 'access-key-label', 'confirm-key-field', 'legacy-key-field', 'access-hint', 'connect-button']) session.ids[id] = new Element();
    session.ids.application = session.application;
    session.ids.connection.hidden = true;
    session.ids['submit-dialog'].showModal();
    for (const id of inputs) session.ids[id].value = 'synthetic-private-input';
    session.ids['admin-key-form'].reset = () => { for (const id of ['new-admin-key', 'repeat-admin-key']) session.ids[id].value = ''; };
    const state = { token: 'synthetic-session', generation: 0, navigation: 0, auth: { setup_required: false } };
    const stored = new Map([['library_access_key', state.token]]);
    const context = vm.createContext({
      $: id => session.ids[id], state, switchView, taskView: session.controller, AbortSignal,
      document: { querySelectorAll: selector => selector === 'dialog[open]' ? [session.ids['task-detail-dialog'], session.ids['submit-dialog']].filter(dialog => dialog.open) : [] },
      localStorage: { removeItem: key => stored.delete(key) },
      fetch: async () => ({ ok: false, status, json: async () => ({ detail: '请重新登录' }) })
    });
    vm.runInContext(webSource.slice(webSource.indexOf('  async function api('), webSource.indexOf('  async function initializeConnection(')), context);
    await assert.rejects(vm.runInContext("api('/api/tasks')", context), /请重新登录/);
    for (const id of ['task-detail-dialog', 'submit-dialog']) assert.equal(session.ids[id].open, false, `${status} 后必须关闭 ${id}`);
    for (const id of inputs) assert.equal(session.ids[id].value, '', `${status} 后必须清空 ${id}`);
    assert.equal(state.token, '');
    assert.equal(stored.has('library_access_key'), false);
    assert.equal(session.ids.application.hidden, true);
    assert.equal(session.ids.connection.hidden, false);
    assert.notEqual(session.ids['connect-button'].disabled, true);
    assert.equal(session.ids['task-detail'].children.length, 0);
    assert.equal(session.ids['task-dialog-body'].children.length, 0);
    session.controller.update(tasks);
    assert.equal(session.ids['task-list'].children.length, tasks.length, '重新登录后相同任务仍须正常渲染');
  }
} finally {
  delete globalThis.document; delete globalThis.localStorage; delete globalThis.matchMedia; delete globalThis.fetch;
}

// Exercise the existing action callbacks rather than duplicating their routing rules.
const calls = [];
const cardContext = vm.createContext({
  element: (tag, className, text) => { const node = new Element(tag, text); node.className = className; return node; },
  taskTitle, statusNames: {}, formatDate: () => '刚刚',
  action: (label, callback) => { const node = new Element('button', label); node.invoke = callback; return node; },
  api: async (url, method) => { calls.push({ url, method }); }, loadTasks: async () => {}, notice:()=>{}
});
const cardFunction = webSource.slice(webSource.indexOf('  function taskCard(task)'), webSource.indexOf('  const taskView ='));
assert.ok(cardFunction.startsWith('  function taskCard(task)'));
vm.runInContext(`${cardFunction}\nglobalThis.makeCard = taskCard;`, cardContext);
for (const [status, label, suffix] of [['failed', '重试', 'retry'], ['queued', '取消', 'cancel'], ['awaiting_confirmation', '接受此任务原项目优选', 'confirm'], ['awaiting_confirmation', '取消', 'cancel']]) {
  const card = cardContext.makeCard({ id: 'task/a', status, kind: 'detail', link: 'https://www.douyin.com/video/123', preview: [] });
  await card.querySelector('.record-actions').children.find(node => node.textContent === label).invoke();
  assert.deepEqual(calls.at(-1), { url: `/api/tasks/task%2Fa/${suffix}`, method: 'POST' });
}
const deleting=cardContext.makeCard({id:'remove/a',status:'downloading',kind:'detail',link:'https://www.douyin.com/video/123',preview:[]});
await deleting.querySelector('.record-actions').children.find(n=>n.textContent==='删除').invoke();
assert.deepEqual(calls.at(-1),{url:'/api/tasks/remove%2Fa',method:'DELETE'});
assert.equal(view.bulkEligible({status:'failed'},'retry'),true);
assert.equal(view.bulkEligible({status:'completed'},'retry'),false);
assert.equal(view.bulkEligible({status:'downloading'},'cancel'),true);
const taskScript = html.indexOf('src="/static/task-view.js?'), appScript = html.indexOf('src="app.js?');
assert.ok(taskScript >= 0 && appScript > taskScript, '任务模块必须走静态目录并先于app初始化');
console.log('Task view checks passed: fixed A, legacy B preference, dialog selection, polling, and shared API actions.');
