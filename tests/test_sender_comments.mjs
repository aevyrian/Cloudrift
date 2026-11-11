import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import assert from 'node:assert/strict';
const require = createRequire(import.meta.url);
const metadata = readFileSync(new URL('../userscripts/douyin-send.user.js', import.meta.url), 'utf8').split('// ==/UserScript==')[0];
assert.match(metadata, /^\/\/ @early-start\s*$/m);
assert.match(metadata, /^\/\/ @inject-into\s+page\s*$/m);
const { commentTemplate, commentURL, commentCaptcha, commentHeaders, commentPayload, installCommentHooks } = require('../userscripts/douyin-send.user.js');
const id = '1234567890123456789';
const source = `https://www.douyin.com/aweme/v1/web/comment/list/?aweme_id=${id}&cursor=0&count=10&msToken=old&a_bogus=secret&X-Bogus=secret&_signature=secret&device_platform=webapp`;
assert.equal(commentTemplate(source).id, id);
assert.equal(commentTemplate(source.replace('www.douyin.com', 'evil.example')), null);
assert.equal(commentTemplate(source.replace(id, '123')), null);
const reply = new URL(commentURL(source, id, '456', 20, 'current'));
assert.equal(reply.pathname, '/aweme/v1/web/comment/list/reply/');
assert.equal(reply.searchParams.get('item_id'), id);
assert.equal(reply.searchParams.get('comment_id'), '456');
assert.equal(reply.searchParams.get('cursor'), '20');
assert.equal(reply.searchParams.get('count'), '20');
assert.equal(reply.searchParams.get('msToken'), 'current');
assert.equal(reply.searchParams.get('device_platform'), 'webapp');
for (const key of ['aweme_id', 'a_bogus', 'X-Bogus', '_signature']) assert.equal(reply.searchParams.has(key), false);
const filtered = commentPayload({status_code:0, has_more:0, cursor:20, token:'secret', comments:[{cid:'1', text:'hi', user:{nickname:'n', sec_uid:'secret'}, image_list:[{origin_url:{url_list:['https://example.com/a'], uri:'secret'}}], voice_url:'secret'}]});
assert.equal(filtered.token, undefined);
assert.equal(filtered.comments[0].user.sec_uid, undefined);
assert.equal(filtered.comments[0].image_list[0].origin_url.uri, undefined);
assert.equal(filtered.comments[0].voice_url, true);

assert.deepEqual(commentHeaders({uifid:'ok', Cookie:'secret', Authorization:'secret', 'sec-fetch-site':'same-origin', 'x-signature':'stale'}), [['uifid','ok']]);

const mockPage = runInNewContext(`(() => {
  class XHR {
    open(method, url) { this.method = method; this.url = url; }
    setRequestHeader(name, value) { (this.sentHeaders ||= {})[name] = value; }
    addEventListener(name, fn) { this.listener = fn; }
    send() { this.responseURL = this.url; this.listener?.(); }
  }
  return { XMLHttpRequest: XHR, Headers, fetch: async input => ({url: input}) };
})()`, {Headers});
const captured = [], hookState = {};
installCommentHooks(mockPage, (url, headers) => captured.push({url, headers}), hookState);
const sdkOpen = mockPage.XMLHttpRequest.prototype.open, sdkSend = mockPage.XMLHttpRequest.prototype.send;
mockPage.XMLHttpRequest.prototype.open = function(...args) { return sdkOpen.apply(this, args); };
mockPage.XMLHttpRequest.prototype.send = function(...args) { this.setRequestHeader('uifid', 'example'); return sdkSend.apply(this, args); };
installCommentHooks(mockPage, (url, headers) => captured.push({url, headers}), hookState);
const installed = [mockPage.XMLHttpRequest.prototype.open, mockPage.XMLHttpRequest.prototype.send, mockPage.fetch];
installCommentHooks(mockPage, (url, headers) => captured.push({url, headers}), hookState);
assert.deepEqual([mockPage.XMLHttpRequest.prototype.open, mockPage.XMLHttpRequest.prototype.send, mockPage.fetch], installed, '重复检测不叠加包装');
const xhr = new mockPage.XMLHttpRequest();xhr.open('GET', source);xhr.send();
assert.ok(captured.some(value => value.url === source && value.headers.uifid === 'example'), 'SDK 覆盖后重新捕获真实响应与请求头');

const captchaElement={getBoundingClientRect:()=>({width:10,height:10})},captchaDocument={querySelectorAll:()=>[captchaElement]};
assert.equal(commentCaptcha(captchaDocument,{getComputedStyle:()=>({display:'block',visibility:'visible'})}),true);
assert.equal(commentCaptcha(captchaDocument,{getComputedStyle:()=>({display:'none',visibility:'visible'})}),false);
assert.equal(commentCaptcha(captchaDocument,{getComputedStyle:()=>({display:'block',visibility:'hidden'})}),false);
assert.equal(commentCaptcha({querySelectorAll:()=>[{getBoundingClientRect:()=>({width:0,height:10})}]},{getComputedStyle:()=>({display:'block',visibility:'visible'})}),false);

const delayedPage = runInNewContext(`(() => {
  class XHR { open(method,url){this.url=url;} setRequestHeader(){} addEventListener(name,fn){this.listener=fn;} send(){} }
  let finishFetch;return {XMLHttpRequest:XHR,Headers,fetch:()=>new Promise(resolve=>{finishFetch=resolve;}),finishFetch:url=>finishFetch({url})};
})()`, {Headers});
let epoch=null;const epochA={},epochB={},delayedCaptures=[];
installCommentHooks(delayedPage,(url,headers,sourceEpoch)=>delayedCaptures.push({url,sourceEpoch}),{},()=>epoch);
const beforeSession=new delayedPage.XMLHttpRequest();beforeSession.open('GET',source);beforeSession.send();
epoch=epochA;const delayedXHR=new delayedPage.XMLHttpRequest();delayedXHR.open('GET',source);delayedXHR.send();
const delayedFetch=delayedPage.fetch(source);const recorded=delayedCaptures.length;
epoch=epochB;beforeSession.responseURL=source;beforeSession.listener();delayedXHR.responseURL=source;delayedXHR.listener();delayedPage.finishFetch(source);await delayedFetch;
assert.equal(delayedCaptures.length,recorded,'旧请求或无会话请求的迟到响应不得写入新会话模板');
assert.ok(delayedCaptures.every(item=>item.sourceEpoch===epochA),'发出时只归属原会话');
