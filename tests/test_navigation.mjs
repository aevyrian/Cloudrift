import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { createRequire } from 'node:module';
const source = readFileSync(new URL('../app/static/app.js', import.meta.url), 'utf8');
const { switchView, applySound } = createRequire(import.meta.url)('../app/static/app.js');
const tick = () => new Promise(resolve => setImmediate(resolve));
const stateContext = vm.createContext({ localStorage: { getItem: key => key === 'library_muted' ? 'false' : '' } });
vm.runInContext(source.slice(source.indexOf('  const state ='), source.indexOf('  const statusNames =')) + '\nglobalThis.state = state;', stateContext);
assert.equal(stateContext.state.muted, false, '刷新作品库默认打开声音');
const html = readFileSync(new URL('../app/static/index.html', import.meta.url), 'utf8');
assert.match(html, /id="sound-toggle"[^>]*aria-pressed="true">声音：开<\/button>/, '初始声音按钮应显示声音开启');
const button = { events: {}, setAttribute() {}, addEventListener(type, callback) { this.events[type] = callback; } };
const videos = [{ muted: true }];
const soundContext = vm.createContext({ state: stateContext.state, $: id => id === 'sound-toggle' ? button : { querySelectorAll: () => videos }, localStorage: { setItem() {} }, applySound, activeCard: null });
vm.runInContext(source.slice(source.indexOf('  function paintSound()'), source.indexOf("  $('refresh-tasks')")), soundContext);
button.events.click(); assert.equal(videos[0].muted, true, '用户可以手动关闭声音');
button.events.click(); assert.equal(videos[0].muted, false, '用户可以再次打开声音');

const ids = Object.fromEntries(['cookie-state', 'cookie', 'clear-cookie', 'quality-policy', 'download-dir', 'download-dir-hint', 'script-server', 'script-token', 'reveal-token'].map(id => [id, {}]));
let resolveStatus; let statusPainted = false; let legacyLoaded = false;
const status = new Promise(resolve => { resolveStatus = resolve; });
const state = { navigation: 1, view: 'settings', token: 'synthetic-test' };
const settingsContext = vm.createContext({ state, $: id => ids[id], location: { origin: 'https://test.example' }, showStatus() { statusPainted = true; }, loadLegacy: async () => { legacyLoaded = true; }, api: path => path === '/api/status' ? status : Promise.resolve({ cookie_configured: false, quality_policy: 'upstream', download_dir: '/media', media_root: '/media' }) });
vm.runInContext(source.slice(source.indexOf('  async function loadSettings('), source.indexOf('  async function loadLegacy(')) + '\nglobalThis.loadSettings = loadSettings;', settingsContext);
const loading = settingsContext.loadSettings();
await tick();
assert.equal(ids['download-dir'].value, '/media', '设置表单无需等待慢状态统计');
assert.equal(legacyLoaded, true, '旧库状态应并行加载');
state.navigation++; state.view = 'tasks';
resolveStatus({}); await loading;
assert.equal(statusPainted, false, '离开设置后，旧响应不能覆盖当前页');

let resolveTasks;
const taskWait = new Promise(resolve => { resolveTasks = resolve; });
let rendered = 0;
const tasksContext = vm.createContext({ state: { navigation: 1, view: 'tasks' }, api: () => taskWait, taskView: { update() { rendered++; } } });
vm.runInContext(source.slice(source.indexOf('  async function loadTasks('), source.indexOf('  async function loadAuthors(')) + '\nglobalThis.loadTasks = loadTasks;', tasksContext);
const first = tasksContext.loadTasks();
tasksContext.state.navigation++; tasksContext.state.view = 'settings';
resolveTasks([]); await first;
assert.equal(rendered, 0, '离开下载后不得渲染迟到的任务响应');

class AuthorNode {
  constructor(tag, className = '', text = '') { this.tagName = tag; this.className = className; this.textContent = text; this.children = []; this.classList = { toggle() {} }; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = [...nodes]; }
  setAttribute() {}
}
const authorList = new AuthorNode('div');
const authorState = { navigation: 1, authorsGeneration: 0, view: 'favorites' };
let interval = 4320;
const authorContext = vm.createContext({
  state: authorState, $: id => ['favorite-author-list', 'author-list'].includes(id) ? authorList : { value: '' },
  element: (tag, className, text) => new AuthorNode(tag, className, text),
  action: (label, invoke) => Object.assign(new AuthorNode('button', '', label), { invoke }),
  Option: function (label, value) { return { label, value }; },
  frequencies: { once: '不订阅', three_days: '每3天更新', weekly: '每7天更新' },
  frequencyValues: { 4320: 'three_days', 10080: 'weekly' }, formatDate: () => '', notice() {}, empty() {},
  api: async (path, method, body) => {
    if (method === 'PATCH') interval = body.frequency === 'weekly' ? 10080 : 4320;
    return path === '/api/authors/history' ? [{ id: 'author', name: '博主', url: 'https://example.test', enabled: true, registered: true, favorite: true, interval_minutes: interval }] : {};
  }
});
vm.runInContext(source.slice(source.indexOf('  async function loadAuthors('), source.indexOf('  async function loadSettings(')) + '\nglobalThis.loadAuthors = loadAuthors;', authorContext);
await authorContext.loadAuthors(true);
const authorCard = authorList.children[1], authorActions = authorCard.children[1];
authorActions.children.find(node => node.tagName === 'select').value = 'weekly';
await authorActions.children.find(node => node.textContent === '保存方式').invoke();
assert.equal(authorList.children[1].children[0].children[0].children[1].textContent, '每7天更新', '收藏博主保存频率后应立即刷新卡片');
authorState.view = 'authors'; interval = 4320;
await authorContext.loadAuthors();
authorList.children[1].children[1].children.find(node => node.tagName === 'select').value = 'weekly';
await authorList.children[1].children[1].children.find(node => node.textContent === '保存方式').invoke();
assert.equal(authorList.children[1].children[0].children[0].children[1].textContent, '每7天更新', '订阅页保存频率后应立即刷新卡片');

for (const favoritesOnly of [false, true]) {
  authorState.view = favoritesOnly ? 'favorites' : 'authors';
  const pending = [];
  authorContext.api = () => new Promise(resolve => pending.push(resolve));
  const older = authorContext.loadAuthors(favoritesOnly);
  const newer = authorContext.loadAuthors(favoritesOnly);
  pending[1]([]); await newer;
  pending[0]([{ id: 'removed', name: '已取消收藏', favorite: true }]); await older;
  assert.equal(authorList.children.length, 1, '迟到的旧列表不得覆盖最新博主状态');
}

const panels = ['library', 'tasks', 'authors', 'settings'].map(view => ({ id: `view-${view}`, hidden: true }));
const nodes = Object.fromEntries(['application', 'page-title', 'page-eyebrow'].map(id => [id, { dataset: {} }]));
const navState = { view: 'library', generation: 0, navigation: 0 };
let rejectOld; const slowTasks = new Promise((resolve, reject) => { rejectOld = reject; });
const notices = [];
const navigationContext = vm.createContext({ state: navState, $: id => nodes[id], switchView, titles: { tasks: ['下载', 'TASKS'], settings: ['设置', 'SETTINGS'] }, document: { querySelectorAll: selector => selector === '.view' ? panels : [] }, history: { replaceState() {} }, notice: message => notices.push(message), loadTasks: () => slowTasks, loadSettings: async () => {}, loadAuthors: async () => {} });
vm.runInContext(source.slice(source.indexOf('  async function navigate('), source.indexOf('  let playbackFrame=')) + '\nglobalThis.navigate = navigate;', navigationContext);
const oldNavigation = navigationContext.navigate('tasks');
await navigationContext.navigate('settings');
assert.equal(panels.find(panel => panel.id === 'view-settings').hidden, false, '慢任务请求不应阻止立即切到设置');
rejectOld(new Error('旧任务请求失败')); await oldNavigation;
assert.equal(notices.includes('旧任务请求失败'), false, '旧页面错误不应弹到新页面');
console.log('Navigation checks passed: immediate switching, stale responses, independent settings, and sound on by default.');
