import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
const { createCommentAccountSettings } = createRequire(import.meta.url)('../app/static/app.js');
class Node {
  constructor() { this.events = {}; this.textContent = ''; this.value = ''; this.hidden = false; }
  addEventListener(name, fn) { this.events[name] = fn; }
  click() { return this.events.click(); }
}
const nodes = Object.fromEntries(['primary','secondary','state','bindPrimary','bindSecondary','pause','resume','message','cookie'].map(key=>[key,new Node()]));
const calls = [], secret = 'synthetic-private-cookie';
let accounts = {paused:true,reason:'等待处理',primary:{bound:false,source:'download',reason:'请更新下载 Cookie'},secondary:{bound:false},can_bind_secondary:false};
let failure = '', delay = null;
const api = async (path, method='GET', body) => {
  calls.push({path,method,body});
  if (path === '/api/comment-accounts') return accounts;
  if (path.endsWith('/secondary-cookie')) {
    assert.equal(nodes.cookie.value, '', '提交前清空敏感输入');
    if (delay) return new Promise(resolve=>{delay.resolve=resolve;});
    if (failure) throw Error(failure);
    accounts={...accounts,paused:true,secondary:{bound:true}}; return accounts;
  }
  if (path.endsWith('/resume')) {accounts={...accounts,paused:false};return accounts;}
  if (path.endsWith('/pause')) {accounts={...accounts,paused:true};return accounts;}
  throw Error('unexpected request');
};
const ui=createCommentAccountSettings({nodes,api});await ui.refresh();
assert.equal(nodes.bindSecondary.disabled,true);assert.equal(nodes.resume.disabled,true);
await nodes.bindPrimary.click();assert.match(nodes.message.textContent,/更新下载/);
assert.equal(calls.some(c=>c.method==='POST'),false,'刷新大号不发起绑定或跳转');
accounts={...accounts,primary:{bound:true,source:'download'},can_bind_secondary:true};await ui.refresh();
assert.match(nodes.primary.textContent,/视频下载账号/);
nodes.cookie.value=secret;nodes.cookie.events.input();assert.equal(nodes.bindSecondary.disabled,false);
await nodes.bindSecondary.click();assert.match(nodes.message.textContent,/小号绑定成功/);
assert.deepEqual(calls.find(c=>c.path.endsWith('/secondary-cookie')).body,{cookie:secret});
assert.equal(nodes.resume.disabled,false);await nodes.resume.click();
assert.deepEqual(calls.find(c=>c.path.endsWith('/resume')).body,{acknowledge:true});
assert.match(nodes.message.textContent,/不会自动开始/);
failure='大号和小号必须是不同账号';nodes.cookie.value=secret;await nodes.bindSecondary.click();
assert.equal(nodes.message.textContent,failure);assert.equal(nodes.cookie.value,'');assert.equal(nodes.bindPrimary.disabled,false);
assert.ok(!Object.values(nodes).some(node=>node.textContent.includes(secret)), 'Cookie 不得回显到消息');
const posts=calls.filter(c=>c.method==='POST').length;
nodes.cookie.value='';await nodes.bindSecondary.click();
nodes.cookie.value='x'.repeat(30001);await nodes.bindSecondary.click();
assert.equal(calls.filter(c=>c.method==='POST').length,posts,'空或过长输入在本地拒绝');
delay={};nodes.cookie.value=secret;const pending=nodes.bindSecondary.click();
assert.equal(nodes.bindSecondary.disabled,true);ui.reset();delay.resolve(accounts);await pending;
assert.equal(nodes.message.textContent,'');assert.equal(nodes.cookie.value,'');assert.equal(nodes.bindSecondary.disabled,true,'退出登录后迟到结果不能恢复界面');
assert.ok(!calls.some(c=>c.path.includes('/works/')||c.path.includes('/bind')), '绑定不应启动采集或跳转流程');
const html=readFileSync(new URL('../app/static/index.html',import.meta.url),'utf8');
assert.match(html, /id="comment-secondary-cookie" type="password"[^>]*autocomplete="off"/);
console.log('账号设置：下载大号复用、小号粘贴、反馈与敏感输入清理检查通过');
