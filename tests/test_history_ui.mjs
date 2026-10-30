import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../app/static/app.js', import.meta.url), 'utf8');
const html = readFileSync(new URL('../app/static/index.html', import.meta.url), 'utf8');
const style = readFileSync(new URL('../app/static/style.css', import.meta.url), 'utf8');
assert.ok(html.indexOf('data-view="authors"') < html.indexOf('data-view="history"') && html.indexOf('data-view="history"') < html.indexOf('data-view="favorites"'));
assert.match(style, /grid-template-columns:repeat\(6,minmax\(0,1fr\)\)/);

class Node {
  constructor(tag, className = '', text = '') { this.tagName = tag; this.className = className; this.textContent = text; this.children = []; this.hidden = false; this.disabled = false; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = [...nodes]; }
}
const nodes = Object.fromEntries(['history-list', 'history-status', 'history-more', 'history-retry', 'history-count'].map(id => [id, new Node('div')]));
const state = { view: 'history', navigation: 1, historySerial: 0 };
const requests = [];
let api = (path) => new Promise((resolve, reject) => requests.push({ path, resolve, reject }));
const context = vm.createContext({ state, $: id => nodes[id], element: (tag, cls, text) => new Node(tag, cls, text), empty: (container, title, description) => container.append(new Node('empty', '', `${title} ${description}`)), api: path => api(path), Date, URL, encodeURIComponent });
const start = source.indexOf('  async function loadBrowseHistory('), end = source.indexOf('  async function loadTasks(', start);
assert.ok(start >= 0 && end > start, '历史加载函数应存在');
vm.runInContext(source.slice(start, end) + '\nglobalThis.loadBrowseHistory = loadBrowseHistory;', context);

const item = (id, time, title = '作品') => ({ event_id: id, work_id: '1234567890123456789', url: 'https://www.douyin.com/video/1234567890123456789', title, author_name: '博主', author_url: 'https://www.douyin.com/user/abcdef', viewed_at: time });
let loading = context.loadBrowseHistory(true);
assert.equal(requests[0].path, '/api/browse-history?limit=50');
requests.shift().resolve({ items: [item('1', '2026-10-30T10:00:00Z', '<img src=x onerror=alert(1)>')], total: 2, next_cursor: 'cursor +/' });
await loading;
assert.equal(nodes['history-list'].children.length, 2, '当天日期与一条记录');
assert.equal(nodes['history-list'].children[1].children[0].children[0].textContent, '<img src=x onerror=alert(1)>', '标题作为纯文本展示');
assert.equal(nodes['history-more'].hidden, false);
loading = context.loadBrowseHistory();
assert.equal(requests[0].path, '/api/browse-history?limit=50&before=cursor%20%2B%2F');
requests.shift().resolve({ items: [item('2', '2026-10-30T09:00:00Z')], total: 2, next_cursor: null }); await loading;
assert.equal(nodes['history-list'].children.length, 3, '同一天跨页不重复日期标题');
assert.equal(nodes['history-more'].hidden, true);

loading = context.loadBrowseHistory(true);
requests.shift().reject(new Error('网络失败')); await loading;
assert.equal(nodes['history-status'].textContent, '网络失败');
assert.equal(nodes['history-retry'].hidden, false, '加载失败可重试');
loading = context.loadBrowseHistory(true);
requests.shift().resolve({ items: [], total: 0, next_cursor: null }); await loading;
assert.match(nodes['history-list'].children[0].textContent, /旧.*历史.*不会.*补/);

loading = context.loadBrowseHistory(true);
state.navigation++; state.view = 'settings';
requests.shift().resolve({ items: [item('3', '2026-10-30T10:00:00Z')], total: 1, next_cursor: null }); await loading;
assert.equal(nodes['history-list'].children.length, 0, '离开历史页后旧响应不可渲染');

state.navigation++; state.view = 'history';
loading = context.loadBrowseHistory(true);
const malicious = { ...item('4', '2026-10-30T10:00:00Z'), url: 'javascript:alert(1)' };
requests.shift().resolve({ items: [malicious], total: 1, next_cursor: null }); await loading;
assert.equal(nodes['history-list'].children[1].children[1].children.length, 0, '无效链接不能渲染为可执行链接');
console.log('History UI checks passed: navigation, pagination, retry, stale responses and safe links.');
