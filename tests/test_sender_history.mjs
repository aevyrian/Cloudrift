import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';

const script=readFileSync(new URL('../userscripts/douyin-send.user.js',import.meta.url),'utf8');
const A='7421234567890123456',B='7421234567890123457';
let sequence=0;
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function fixture(storage={library_server:'http://nas.example:8090',library_token:'secret-one'}){
  let now=Date.parse('2026-10-30T12:00:00Z'), active=A, shown=true, preview=false, failStorage=false, failScan=false, scans=0, author='MS4wLjABAAAA_first', caption='第一条';
  const requests=[],timers=[],frames=[],callbacks={},elements=[], page=new URL('https://www.douyin.com/?recommend=1');
  class Clock extends Date {constructor(...args){super(...(args.length?args:[now]));}static now(){return now;}}
  function element(tag){
    const listeners={},attrs={};
    const node={tag,style:{},dataset:{},classList:{toggle(){},add(){},remove(){}},children:[],hidden:false,open:false,textContent:'',value:'',disabled:false,offsetHeight:300,
      append(...items){this.children.push(...items)},setAttribute(k,v){attrs[k]=String(v)},getAttribute(k){return attrs[k]??null},
      addEventListener(k,fn){(listeners[k]??=[]).push(fn)},contains(){return false},matches(){return false},closest(){return null},
      querySelector(){return null},querySelectorAll(){return []},getBoundingClientRect(){return {width:0,height:0,top:0,bottom:0,left:0}},
      attachShadow(){return element('shadow')},focus(){}};
    elements.push(node);return node;
  }
  const feed={matches:s=>s==='[data-e2e-vid], [data-e2e-aweme-id]',getAttribute:name=>name==='data-e2e-vid'?active:null,
    querySelector:s=>s==='a[data-e2e="video-author-name"], a[data-e2e="video-avatar"]'?{href:`https://www.douyin.com/user/${author}`,textContent:author}:
      s==='[data-e2e="feed-video-nickname"]'?{textContent:author}:s==='[data-e2e="video-desc"], [data-e2e="feed-video-desc"]'?{textContent:caption}:null,
    querySelectorAll:()=>[],contains:()=>false,getBoundingClientRect:()=>shown?{width:1000,height:700,top:0,bottom:700,left:0}:{width:0,height:0,top:0,bottom:0,left:0}};
  const doc={body:element('body'),createElement:element,visibilityState:'visible',querySelector:()=>null,
    querySelectorAll:s=>s==='[data-e2e="feed-active-video"]'?[feed]:s==='[data-e2e="video-detail"], [data-e2e="note-detail"]'&&preview?[feed]:[],addEventListener(k,fn){callbacks[k]=fn}};
  const mockCrypto={randomUUID(){return `00000000-0000-4000-8000-${String(++sequence).padStart(12,'0')}`;},subtle:{async digest(_,data){
    const result=new Uint8Array(32);for(let i=0;i<data.length;i++)result[i%32]=(result[i%32]*31+data[i])%256;return result.buffer;
  }}};
  runInNewContext(script,{URL,document:doc,location:page,innerWidth:1200,innerHeight:800,Date:Clock,crypto:mockCrypto,TextEncoder,
    GM_getValue:(key,fallback)=>structuredClone(storage[key]??fallback),GM_setValue:(key,value)=>{if(failStorage&&key.startsWith('library_browse_event_'))throw Error('storage');storage[key]=structuredClone(value)},
    GM_listValues:()=>{scans++;if(failScan)throw Error('storage');return Object.keys(storage)},GM_deleteValue:key=>{delete storage[key]},GM_registerMenuCommand(){},
    GM_xmlhttpRequest:request=>requests.push(request),ResizeObserver:class{observe(){}disconnect(){}},MutationObserver:class{observe(){}},
    addEventListener(){},requestAnimationFrame:fn=>{frames.push(fn);return frames.length},setInterval:fn=>{callbacks.detect=fn},
    setTimeout(fn,delay){const timer={fn,at:now+delay};timers.push(timer);return timer},clearTimeout(timer){const index=timers.indexOf(timer);if(index>=0)timers.splice(index,1)}});
  function detect(id,name=author,title=caption){active=id;author=name;caption=title;callbacks.detect();}
  async function advance(ms){now+=ms;while(true){const index=timers.findIndex(timer=>timer.at<=now);if(index<0)break;timers.splice(index,1)[0].fn();await tick();}callbacks.detect();await tick();}
  const pending=()=>Object.entries(storage).filter(([key])=>key.startsWith('library_browse_event_'));
  const posts=()=>requests.filter(request=>request.url.endsWith('/api/browse-history'));
  const reply=(request,value,status=200)=>request.onload({status,responseText:JSON.stringify(value)});
  function visibility(value){doc.visibilityState=value;callbacks.visibilitychange();if(value==='visible')while(frames.length)frames.shift()();}
  return {storage,requests,posts,pending,detect,advance,reply,doc,feed,elements,visibility,scans:()=>scans,setShown(value){shown=value},setPreview(value){preview=value},setFailStorage(value){failStorage=value},setFailScan(value){failScan=value},page};
}

const b=fixture();await tick();
assert.equal(b.pending().length,1,'面板关闭时仍保存当前浏览作品');
const first=b.pending()[0][1].item;
assert.equal(first.work_id,A);assert.equal(first.title,'第一条');assert.equal(first.author_name,'MS4wLjABAAAA_first');
assert.equal(first.author_url,'https://www.douyin.com/user/MS4wLjABAAAA_first');
assert.match(first.event_id,/^[0-9a-f-]{36}$/);assert.equal(first.viewed_at,'2026-10-30T12:00:00.000Z');
assert.match(b.elements.find(el=>el.className==='history-status').textContent,/待同步/);
const scans=b.scans();for(let i=0;i<30;i++)b.detect(A);await tick();assert.equal(b.scans(),scans,'页面频繁变化时不能每帧扫描存储');
await b.advance(2100);assert.equal(b.posts().length,1);
const failed=b.posts()[0];assert.equal(failed.headers.Authorization,'Bearer secret-one');failed.onerror();await tick();
assert.equal(b.pending().length,1,'网络错误不删除本地记录');
assert.match(b.elements.find(el=>el.className==='history-status').textContent,/同步失败/);
await b.advance(30000);assert.equal(b.posts().length,2);
const retry=b.posts()[1];assert.equal(JSON.parse(retry.data).items[0].event_id,first.event_id,'重试沿用相同事件 ID');
b.reply(retry,{accepted:0});await tick();assert.equal(b.pending().length,1,'未确认整个批次不能删除');
await b.advance(30000);const success=b.posts().at(-1);b.reply(success,{accepted:1});await tick();assert.equal(b.pending().length,0);
assert.match(b.elements.find(el=>el.className==='history-status').textContent,/已同步/);

b.detect(B,'MS4wLjABAAAA_second','第二条');await tick();assert.equal(b.pending().length,1);
let second=b.pending()[0][1].item;assert.equal(second.work_id,B);assert.equal(second.title,'第二条');assert.equal(second.author_name,'MS4wLjABAAAA_second');
b.setShown(false);b.detect(B);b.setShown(true);b.detect(B);await tick();assert.equal(b.pending().length,1,'切换中空帧不会重复记录');
b.detect(A,'MS4wLjABAAAA_first','返回第一条');await tick();assert.equal(b.pending().length,2,'离开后再次浏览应记录新事件');
assert.notEqual(b.pending().find(([,value])=>value.item.work_id===A)[1].item.event_id,first.event_id);
b.doc.visibilityState='hidden';b.detect(B);await tick();assert.equal(b.pending().length,2,'后台标签页预加载不能当作浏览');
b.doc.visibilityState='visible';b.detect(B);await tick();assert.equal(b.pending().length,3,'切回标签后识别当前作品');
const beforeReturn=b.pending().length;b.doc.visibilityState='hidden';b.detect(B);b.doc.visibilityState='visible';b.detect(B);await tick();
assert.equal(b.pending().length,beforeReturn+1,'切走标签后返回同一作品应记录新的浏览');
const actualVisibility=fixture();await tick();actualVisibility.visibility('hidden');actualVisibility.visibility('visible');await tick();
assert.equal(actualVisibility.pending().length,2,'隐藏时动画帧暂停，恢复可见仍须记录新浏览');

const isolated=fixture();await tick();const oldEvent=isolated.pending()[0][1].item.event_id;
isolated.storage.library_server='http://other.example:8090';isolated.storage.library_token='secret-two';
isolated.detect(B,'MS4wLjABAAAA_second','新网站作品');await tick();await isolated.advance(2100);
const other=isolated.posts().at(-1);assert.equal(new URL(other.url).host,'other.example:8090');
assert.deepEqual(JSON.parse(other.data).items.map(item=>item.work_id),[B],'旧网站的待同步记录不能发送到新网站');
isolated.reply(other,{accepted:1});await tick();assert.equal(isolated.pending().length,1);assert.equal(isolated.pending()[0][1].item.event_id,oldEvent);
isolated.storage.library_server='http://nas.example:8090';isolated.storage.library_token='secret-one';
await isolated.advance(31000);const original=isolated.posts().at(-1);assert.equal(new URL(original.url).host,'nas.example:8090');
assert.ok(JSON.parse(original.data).items.some(item=>item.event_id===oldEvent),'切回原连接后继续重试原记录');

const identity=fixture();await tick();identity.storage.library_token='different-token';identity.detect(B);await tick();await identity.advance(2100);
assert.deepEqual(JSON.parse(identity.posts().at(-1).data).items.map(item=>item.work_id),[B],'同网站更换密钥也不能发送旧连接的记录');
const standalone=fixture();await tick();standalone.page.href=`https://www.douyin.com/video/${B}`;standalone.detect(A);await tick();
const direct=standalone.pending().find(([,value])=>value.item.work_id===B)?.[1].item;
assert.ok(direct,'独立作品页面节点尚未加载时仍记录地址中的作品');
assert.equal(direct.author_name,'','不能把背景推荐视频的作者写到独立作品');assert.equal(direct.title,'');
const stale=fixture();await tick();stale.setPreview(true);stale.page.href=`https://www.douyin.com/video/${B}`;stale.detect(A);await tick();
const newer=stale.pending().find(([,value])=>value.item.work_id===B)?.[1].item;
assert.ok(newer,'旧作品详情仍可见时记录明确的新地址');assert.equal(newer.title,'');assert.equal(newer.author_url,'');
const modal=fixture();await tick();modal.setPreview(true);modal.page.href=`https://www.douyin.com/user/MS4wLjABAAAA_first?modal_id=${B}`;modal.detect(A);await tick();
assert.ok(modal.pending().find(([,value])=>value.item.work_id===B),'旧详情仍可见时 modal_id 应是当前作品');
const changing=fixture();await tick();changing.detect(B,'MS4wLjABAAAA_first','旧内容');await tick();
changing.detect(B,'MS4wLjABAAAA_second','新内容');await tick();
const corrected=changing.pending().find(([,value])=>value.item.work_id===B)[1].item;
assert.equal(corrected.author_name,'MS4wLjABAAAA_second','作品切换时迟到的作者应更新当前事件');assert.equal(corrected.title,'新内容');
changing.detect(B,'','');await tick();
assert.equal(changing.pending().find(([,value])=>value.item.work_id===B)[1].item.title,'新内容','节点暂时为空不能擦除已识别标题');
const late=fixture();await tick();await late.advance(2100);const initial=late.posts()[0],initialItem=JSON.parse(initial.data).items[0];
late.detect(A,'MS4wLjABAAAA_first','请求中才加载的标题');await tick();late.reply(initial,{accepted:1});await tick();
assert.equal(late.pending().length,1,'已发送后资料才到，成功确认不得删除更新');
assert.ok(late.pending()[0][1].item.metadata_revision>initialItem.metadata_revision);
await late.advance(1500);const update=late.posts().at(-1);assert.equal(JSON.parse(update.data).items[0].event_id,initialItem.event_id);late.reply(update,{accepted:1});await tick();
assert.equal(late.pending().length,0);late.detect(A,'MS4wLjABAAAA_first','确认后才到的新标题');await tick();
assert.equal(late.pending().length,1,'发送成功后资料才到，须以同一事件重新补充');assert.equal(late.pending()[0][1].item.event_id,initialItem.event_id);
const shared={library_server:'http://nas.example:8090',library_token:'secret-one'},tabA=fixture(shared),tabB=fixture(shared);await tick();
const tabAEvent=tabA.pending()[0][1].item.event_id;await tabB.advance(2100);const batch=tabB.posts().at(-1);
tabB.reply(batch,{accepted:2});await tick();assert.equal(tabA.pending().length,0);
tabA.detect(A,'MS4wLjABAAAA_first','另一个标签发出后才出现的标题');await tick();
assert.equal(tabA.pending().length,1,'另一个标签发送后，本标签迟到的资料仍应重排');
assert.equal(tabA.pending()[0][1].item.event_id,tabAEvent);
const broken=fixture();await tick();broken.setFailStorage(true);broken.detect(B);await tick();
assert.match(broken.elements.find(el=>el.className==='history-status').textContent,/助手存储失败/,'存储失败应明确提示');
broken.setFailStorage(false);broken.detect(B);await tick();assert.match(broken.elements.find(el=>el.className==='history-status').textContent,/待同步/,'存储恢复后自动重试');
const scanFailure=fixture();await tick();scanFailure.setFailScan(true);await scanFailure.advance(1100);
assert.match(scanFailure.elements.find(el=>el.className==='history-status').textContent,/助手存储失败/);
scanFailure.setFailScan(false);await scanFailure.advance(30000);
assert.doesNotMatch(scanFailure.elements.find(el=>el.className==='history-status').textContent,/助手存储失败/,'读取恢复后可继续同步并消除错误');
console.log('PASS: 面板关闭识别、真实切换、背景页隔离、断网持久重试、确认后清除和网站/密钥隔离');
