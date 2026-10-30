import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import { createRequire } from 'node:module';
import { existsSync, readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { fileURLToPath } from 'node:url';
const require = createRequire(import.meta.url);
const script = new URL('../userscripts/douyin-send.user.js', import.meta.url);
const web = new URL('../app/static/app.js', import.meta.url);
assert.ok(existsSync(script), '发送脚本尚未实现');
const { resolveTargets, normalizeServer, togglePanel, defaultKind, senderPosition, pageTargets, subscriptionState } = require(fileURLToPath(script));
const subscribedURL='https://www.douyin.com/user/MS4wLjABAAAA_subscriber';
for(const [minutes,frequency] of [[4320,'three_days'],[10080,'weekly'],[43200,'monthly'],[129600,'quarterly'],[525600,'yearly']]){const found=subscriptionState([{id:'creator',url:subscribedURL,enabled:true,interval_minutes:minutes}],subscribedURL);assert.equal(found.enabled,true);assert.equal(found.frequency,frequency);}
assert.equal(subscriptionState([{url:subscribedURL,enabled:false}],subscribedURL).enabled,false);
assert.equal(subscriptionState([{url:subscribedURL,enabled:true}],'https://www.douyin.com/user/MS4wLjABAAAA_someone_else').author,null,'切换博主后不能沿用上一个红心');
assert.equal(defaultKind(undefined),'author','首次默认下载博主全部作品');
assert.equal(defaultKind('weekly'),'author','不从旧值启用订阅或不明模式');
assert.equal(defaultKind('detail'),'detail','默认按钮可在设置中改为当前作品');
let placement=senderPosition({left:1200,top:300,width:50},{width:1600,height:900},380);
assert.equal(placement.left+22,1225,'方形下载入口与操作栏中心对齐');
assert.equal(placement.top+44+12,300,'入口与下方操作栏保持12px间距');
assert.ok(placement.panelLeft+placement.panelWidth<placement.left,'面板向左展开');
assert.ok(placement.panelTop>=12 && placement.panelTop+380<=888);
placement=senderPosition({left:18,top:50,width:42},{width:390,height:640},600);
assert.ok(placement.panelLeft>=12 && placement.panelLeft+placement.panelWidth<=378,'窄窗口面板不溢出');
assert.ok(placement.top>=12 && placement.panelTop>=12 && placement.panelTop+600<=628);
placement=senderPosition(null,{width:390,height:640},350);
assert.ok(placement.left>=12 && placement.left+44<=378,'没有操作栏的博主页保留方形入口');
for(const viewport of [{width:320,height:568},{width:390,height:844},{width:1440,height:900}]){
  const position=senderPosition({left:viewport.width-70,top:80,width:44},viewport,430);
  assert.ok(position.left>=12&&position.left+44<=viewport.width-12,'下载按钮不能超出屏幕');
  assert.ok(position.panelLeft>=12&&position.panelLeft+position.panelWidth<=viewport.width-12,'加宽后的面板不能超出屏幕');
}
const panel = { hidden: true }, attributes = {};
const toggle = { setAttribute(name, value) { attributes[name] = value; } };
togglePanel(panel, toggle);
assert.equal(panel.hidden, false, '三点按钮打开发送菜单');
assert.equal(attributes['aria-expanded'], 'true');
togglePanel(panel, toggle);
assert.equal(panel.hidden, true, '再次点击收起菜单');
assert.equal(attributes['aria-expanded'], 'false');
const sample = '7421234567890123456';
const creator = {href:'https://www.douyin.com/user/MS4wLjABAAAA_actual'};
const detailScope = {
  matches: selector => selector === '[data-e2e="video-detail"], [data-e2e="note-detail"]',
  querySelector: selector => selector === '[data-e2e="user-info"] a[href*="/user/"]' ? creator
    : selector === 'a[href*="/user/"]' ? {href:'https://www.douyin.com/user/MS4wLjABAAAA_recommendation'}
    : selector === 'a[href*="/video/"], a[href*="/note/"]' ? {href:'https://www.douyin.com/video/7421234567890123457'}
    : selector === '[data-e2e-vid], [data-e2e-aweme-id]' ? {getAttribute:()=> '7421234567890123457'} : null,
};
assert.deepEqual(pageTargets(detailScope, `https://www.douyin.com/video/${sample}`).targets,
  {detail:`https://www.douyin.com/video/${sample}`,author:creator.href}, '独立作品页识别本人，不能抓到推荐作品或作者');
assert.deepEqual(pageTargets(detailScope, `https://www.douyin.com/note/${sample}`).targets,
  {detail:`https://www.douyin.com/note/${sample}`,author:creator.href}, '独立图集页保留当前图集链接与博主');
assert.deepEqual(pageTargets(null,creator.href).targets,{detail:null,author:creator.href},'博主页没有播放器时也能发送');
const awemeScope={matches:selector=>selector==='[data-e2e-vid], [data-e2e-aweme-id]',querySelector:()=>null,getAttribute:name=>name==='data-e2e-aweme-id'?sample:null};
assert.equal(pageTargets(awemeScope,'https://www.douyin.com/?recommend=1').targets.detail,`https://www.douyin.com/video/${sample}`,'识别当前容器自身的作品标识');
assert.deepEqual(resolveTargets(`https://www.douyin.com/video/${sample}`), {
  detail: `https://www.douyin.com/video/${sample}`, author: null,
});
assert.equal(resolveTargets(`https://www.douyin.com/?modal_id=${sample}`).detail, `https://www.douyin.com/video/${sample}`);
assert.equal(resolveTargets('https://www.douyin.com/', `https://www.douyin.com/note/${sample}`).detail, `https://www.douyin.com/note/${sample}`);
assert.equal(resolveTargets('https://www.douyin.com/user/MS4wLjABAAAA_abc123').author, 'https://www.douyin.com/user/MS4wLjABAAAA_abc123');
for (const invalid of ['https://www.douyin.com.evil.test/video/' + sample, 'https://evil.test/video/' + sample, 'https://www.douyin.com/video/123', 'https://www.douyin.com/video/' + sample + '0', 'javascript:alert(1)']) {
  assert.equal(resolveTargets(invalid).detail, null, invalid);
}
assert.equal(resolveTargets('https://www.douyin.com/', null, 'https://evil.test/user/abc123').author, null);
assert.equal(resolveTargets(`https://www.douyin.com/video/${sample}`, 'https://www.douyin.com/video/7421234567890123457').detail, 'https://www.douyin.com/video/7421234567890123457', '当前弹层作品优先于旧地址');
assert.equal(resolveTargets('https://www.douyin.com/jingxuan/search/风景', null, null, '7644073196636392666').detail, 'https://www.douyin.com/video/7644073196636392666', '没有作品链接的图集弹层仍能从当前作品标识发送');
assert.equal(resolveTargets(`https://www.douyin.com/video/${sample}`, null, null, '7644073196636392666').detail, 'https://www.douyin.com/video/7644073196636392666', '当前卡片标识优先于旧页面地址');
assert.equal(resolveTargets('https://www.douyin.com/', null, null, '76440731966363926660').detail, null);
assert.equal(normalizeServer('http://nas.example:8765/'), 'http://nas.example:8765');
assert.throws(() => normalizeServer('https://user:password@example.com/'));
assert.throws(() => normalizeServer('javascript:alert(1)'));
assert.ok(existsSync(web), '网页尚未实现');
const { mediaURL, switchView, moveGallery, isVideoFile, authorInitial, keyError, taskTitle, playVisible, applySound, releasePlayback, preloadVideos } = require(fileURLToPath(web));
const neighbors=[null,{id:'one'},{id:'two'},{id:'far'}].map(video=>({querySelector:()=>video}));
assert.deepEqual(preloadVideos(neighbors,0),[{id:'one'},{id:'two'}],'只预备后两条，远处视频不能持续占用缓冲');
assert.deepEqual(preloadVideos(neighbors,3),[],'末页不越界');
assert.deepEqual(preloadVideos([neighbors[1],neighbors[0],neighbors[2]],0),[{id:'two'}],'图片页不建立视频准备任务');
assert.equal(taskTitle({kind:'author',id:'random-task-id',preview:[{nickname:'真实博主'}]}),'真实博主');
assert.equal(taskTitle({kind:'author',preview:[]}),'正在识别博主');
assert.equal(taskTitle({kind:'detail',preview:[{desc:'作品标题'}]}),'作品标题');
const playing={plays:0,pauses:0,play(){this.plays++;return Promise.resolve()},pause(){this.pauses++}};
await playVisible(playing,true);playVisible(playing,false);
assert.equal(playing.plays,1);assert.equal(playing.pauses,1);assert.equal(playing.preload,'auto');
await playVisible({play:()=>Promise.reject(new Error('浏览器阻止自动播放'))},true);
const preparing={calls:0,pauses:0,preparePlayback(){this.calls++;return Promise.resolve()},pause(){this.pauses++}};
playVisible(preparing,false);assert.equal(preparing.calls,0);
await playVisible(preparing,true);assert.equal(preparing.calls,1);assert.equal(preparing.playbackActive,true);
const live={playbackStreaming:true,playbackReady:true,paused:0,cleared:0,reloaded:0,pause(){this.paused++},getAttribute(){return 'stream'},removeAttribute(name){assert.equal(name,'src');this.cleared++},load(){this.reloaded++}};
playVisible(live,false);assert.equal(live.paused,1);assert.equal(live.cleared,0);assert.equal(live.playbackReady,true,'邻近视频切走保留缓冲，不反复准备');
releasePlayback(live);assert.equal(live.cleared,1);assert.equal(live.reloaded,1);assert.equal(live.playbackReady,false,'远离画面才释放连接');
const videos=[{muted:true},{muted:true}];applySound(videos,false);assert.ok(videos.every(v=>v.muted===false));applySound(videos,true);assert.ok(videos.every(v=>v.muted===true));
const manuallyPaused={userPaused:true,paused:true,plays:0,play(){this.plays++;return Promise.resolve()},pause(){}};playVisible(manuallyPaused,true);assert.equal(manuallyPaused.plays,0,'手动暂停后，切标签返回保持暂停');manuallyPaused.userPaused=false;await playVisible(manuallyPaused,true);assert.equal(manuallyPaused.plays,1,'本人点播放后才继续');
let blocked=false;await playVisible({play:()=>Promise.reject(new Error()),onAutoplayBlocked(){blocked=true}},true);assert.ok(blocked,'浏览器禁止有声自动播放时提示点击，不擅自改回静音');
assert.equal(mediaURL('/api/media/a/0?compatible=true','secret','http://nas:8765'),'http://nas:8765/api/media/a/0?compatible=true&token=secret');
assert.equal(switchView('favorites',[]),'favorites');
assert.equal(keyError('My-admin-key-123', 'My-admin-key-123'), '');
assert.match(keyError('My-admin-key-123', 'different-key'), /不一致/);
for (const invalid of ['short', 'contains space', '中文管理员密钥', 'newline\nkey', 'trailing-newline\n']) assert.ok(keyError(invalid, invalid));
assert.equal(authorInitial('𝓢𝓲𝓵𝓮𝓷𝓽'), '𝓢', '头像不拆开 Unicode 字符');
assert.equal(authorInitial('@山野'), '山');
assert.equal(isVideoFile({ name: 'live_1.mp4', url: '/api/media/work/0' }), true, '实况动态页使用播放器');
assert.equal(isVideoFile({ name: 'live_2.jpeg', url: '/api/media/work/1' }), false, '实况静态页使用图片');
assert.equal(isVideoFile({ name: 'live_3.MOV' }), true);
assert.equal(mediaURL('/api/media/a/0', 'a&b', 'http://nas:8765'), 'http://nas:8765/api/media/a/0?token=a%26b');
assert.throws(() => mediaURL('https://evil.test/api/media/a/0', 'secret', 'http://nas:8765'));
assert.throws(() => mediaURL('/api/settings', 'secret', 'http://nas:8765'));
const a = { pauses: 0, pause() { this.pauses++; } };
const b = { pauses: 0, pause() { this.pauses++; } };
assert.equal(switchView('tasks', [a, b]), 'tasks');
assert.equal(a.pauses, 1);
assert.equal(a.playbackActive,false);
assert.equal(b.pauses, 1);
assert.equal(switchView('unknown', []), 'library');
assert.equal(moveGallery(0, -1, 3), 0);
assert.equal(moveGallery(1, 1, 3), 2);
assert.equal(moveGallery(2, 1, 3), 2);
assert.equal(moveGallery(0, 1, 0), 0);
// Execute the Sender in a small browser stub, exercising the actual request and UI flow.
function browser(url = subscribedURL, {feed = null, preview = null, pageDocument = null, profileFetch = () => Promise.reject(new Error('profile unavailable'))} = {}) {
  const elements = [], requests = [], profileRequests = [], callbacks = {}, frame = [];
  const fetchProfile=(url,options)=>{profileRequests.push({url,options});return profileFetch(url,options)};
  function element(tag) {
    const listeners = {}, attrs = {};
    const el = {tag, style:{}, dataset:{}, classList:{toggle(){},add(){},remove(){}}, children:[], hidden:false, open:false,
      textContent:'', value:'', disabled:false, parentElement:null, offsetHeight:300,
      append(...items){for(const item of items){item.parentElement=this;this.children.push(item)}},
      replaceChildren(...items){this.children=[];this.append(...items)},
      setAttribute(name,value){attrs[name]=String(value)},getAttribute(name){return attrs[name]??null},
      addEventListener(name,fn){(listeners[name]??=[]).push(fn)},
      dispatch(name,props={}){for(const fn of listeners[name]||[])fn({target:this,preventDefault(){},...props})},
      click(){if(!this.disabled)this.dispatch('click')},submit(){this.dispatch('submit')},focus(){},
      contains(node){return this===node||this.children.some(child=>child.contains?.(node))},
      matches(){return false},closest(){return null},querySelector(){return null},querySelectorAll(){return []},
      getBoundingClientRect(){return {width:0,height:0,top:0,bottom:0,left:0}},
      attachShadow(){return element('shadow')},
      get selectedOptions(){return this.children.filter(child=>child.value===this.value)}
    };
    elements.push(el);return el;
  }
  const page = new URL(url), storage={library_server:'http://nas.example:8090',library_token:'test-token'};
  const doc={body:element('body'),createElement:element,
    querySelector(selector){return selector==='h1'?{textContent:page.pathname.includes('someone_else')?'乙博主':'甲博主'}:null},
    querySelectorAll(selector){if(selector==='[data-e2e="feed-active-video"]')return feed?[feed]:[];if(selector==='[data-e2e="video-detail"], [data-e2e="note-detail"]')return preview?[preview]:[];return []},addEventListener(name,fn){callbacks['document:'+name]=fn}};
  const observer=class {constructor(fn){callbacks.mutation=fn}observe(){}disconnect(){}};
  runInNewContext(readFileSync(script,'utf8'),{URL,document:doc,unsafeWindow:pageDocument?{document:pageDocument,fetch:fetchProfile}:undefined,fetch:fetchProfile,AbortSignal,location:page,innerWidth:1200,innerHeight:800,crypto:webcrypto,TextEncoder,
    GM_getValue(key,fallback){return storage[key]??fallback}, GM_setValue(key,value){storage[key]=value},
    GM_listValues(){return Object.keys(storage)}, GM_deleteValue(key){delete storage[key]},
    GM_registerMenuCommand(){},GM_xmlhttpRequest(request){requests.push(request)},
    DOMParser:class{parseFromString(text){return {title:text.startsWith('<title>验证码中间页</title>')?'验证码中间页':'',scripts:[{textContent:text}]}}},ResizeObserver:class{observe(){}disconnect(){}},MutationObserver:observer,
    addEventListener(name,fn){callbacks[name]=fn},
    requestAnimationFrame(fn){frame.push(fn);return frame.length},cancelAnimationFrame(){},
    setInterval(fn){callbacks.detect=fn},setTimeout(fn){frame.push(fn);return frame.length},clearTimeout(){}});
  const find=(value,prop='textContent')=>elements.find(el=>el[prop]===value);
  const byClass=value=>elements.find(el=>el.className?.split(/\s+/).includes(value));
  function go(next){page.href=next;callbacks.detect();while(frame.length)frame.shift()()}
  function respond(request,data,status=200){assert.ok(request,'预期请求不存在');request.onload({status,responseText:JSON.stringify(data)})}
  return {elements,requests,profileRequests,callbacks,frame,storage,page,find,byClass,go,respond,
    host:elements.find(el=>el.id==='douyin-library-sender')};
}
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const authorB='https://www.douyin.com/user/MS4wLjABAAAA_someone_else';
const authorA={id:'a',url:subscribedURL,name:'甲博主',enabled:true,interval_minutes:129600,next_check:1793448000};
const knownB={id:'b',url:authorB,name:'乙博主',enabled:true,interval_minutes:10080,next_check:1793534400};
const request=(b,method,path)=>b.requests.findLast(r=>r.method===method&&new URL(r.url).pathname===path);
const body=r=>JSON.parse(r.data);
const activate=b=>b.find('抖音收藏库 · 下载到媒体库','title').click();
const select=(b,value)=>{const el=b.elements.find(e=>e.getAttribute('aria-label')==='博主更新周期');el.value=value;el.dispatch('change');return el};

const feedId='7701550905683391333';
const feedAuthor={...creator,textContent:'',parentElement:null,getBoundingClientRect:()=>({width:40,height:40,top:100,bottom:140,left:100})};
const feed={matches:s=>s==='[data-e2e-vid], [data-e2e-aweme-id]',getAttribute:name=>name==='data-e2e-vid'?feedId:null,
  querySelector:s=>s==='a[data-e2e="video-author-name"], a[data-e2e="video-avatar"]'||s==='[data-e2e="video-avatar"]'?feedAuthor:s==='[data-e2e="feed-video-nickname"]'?{textContent:'推荐博主'}:null,
  querySelectorAll:s=>s==='[data-e2e="video-avatar"]'?[feedAuthor]:[],contains:()=>false,getBoundingClientRect:()=>({width:1000,height:700,top:0,bottom:700,left:0})};
const hiddenRail={...feedAuthor,getBoundingClientRect:()=>({width:0,height:0,top:0,bottom:0,left:0})};
const outsideRail={...feedAuthor,getBoundingClientRect:()=>({width:40,height:40,top:100,bottom:140,left:-100})};
const otherAuthorRail={...feedAuthor,href:authorB};
const laterFeed={...feed,querySelector:s=>s==='[data-e2e="video-avatar"]'?hiddenRail:feed.querySelector(s),
  querySelectorAll:s=>s==='[data-e2e="video-avatar"]'?[hiddenRail]:s==='[data-e2e="user-info"] a[href*="/user/"]'?[outsideRail,otherAuthorRail,feedAuthor]:s==='[data-e2e="video-avatar"], [data-e2e="user-info"] a[href*="/user/"]'?[hiddenRail,outsideRail,otherAuthorRail,feedAuthor]:[]};
const laterPosition=browser('https://www.douyin.com/?recommend=1',{feed:laterFeed});
assert.equal(laterPosition.host.style.left,'98px','略过隐藏、屏幕外和其他博主的头像，跟随当前可见头像');
assert.equal(laterPosition.host.style.top,'44px');
const infoRail={...feedAuthor,getBoundingClientRect:()=>({width:40,height:40,top:80,bottom:120,left:300})};
const videoRail={...feedAuthor,getBoundingClientRect:()=>({width:40,height:40,top:200,bottom:240,left:800})};
const priorityFeed={...feed,querySelectorAll:s=>s==='[data-e2e="video-avatar"]'?[videoRail]:s==='[data-e2e="user-info"] a[href*="/user/"]'?[infoRail]:s==='[data-e2e="video-avatar"], [data-e2e="user-info"] a[href*="/user/"]'?[infoRail,videoRail]:[]};
const priorityPosition=browser('https://www.douyin.com/?recommend=1',{feed:priorityFeed});
assert.equal(priorityPosition.host.style.left,'798px','视频侧栏头像优先于信息栏头像');
let railRect={width:40,height:40,top:200,bottom:240,left:600};
const changingRail={...feedAuthor,getBoundingClientRect:()=>railRect};
const changingFeed={...feed,querySelector:s=>s==='a[data-e2e="video-author-name"], a[data-e2e="video-avatar"]'||s==='[data-e2e="video-avatar"]'?changingRail:feed.querySelector(s),
  querySelectorAll:s=>s==='[data-e2e="video-avatar"]'||s==='[data-e2e="video-avatar"], [data-e2e="user-info"] a[href*="/user/"]'?[changingRail]:[]};
const positioning=browser('https://www.douyin.com/?recommend=1',{feed:changingFeed});
assert.equal(positioning.host.style.left,'598px');assert.equal(positioning.host.style.top,'144px');
railRect={width:0,height:0,top:0,bottom:0,left:0};positioning.callbacks.detect();
assert.equal(positioning.host.style.left,'598px','切作品时操作栏暂未绘制，不能跳到窗口右侧');
assert.equal(positioning.host.style.top,'144px','操作栏暂未绘制时保留原高度');
railRect={width:40,height:40,top:220,bottom:260,left:750};positioning.callbacks.detect();
assert.equal(positioning.host.style.left,'748px','新操作栏绘制后跟随它的位置');
assert.equal(positioning.host.style.top,'164px');
const temporaryInfo={...feedAuthor,getBoundingClientRect:()=>({width:40,height:40,top:80,bottom:120,left:300})};
const fallbackFeed={...changingFeed,querySelectorAll:s=>s==='[data-e2e="video-avatar"]'?[changingRail]:s==='[data-e2e="user-info"] a[href*="/user/"]'?[temporaryInfo]:[]};
const stablePosition=browser('https://www.douyin.com/?recommend=1',{feed:fallbackFeed});
railRect={width:0,height:0,top:0,bottom:0,left:0};stablePosition.callbacks.detect();
assert.equal(stablePosition.host.style.left,'748px','侧栏暂时隐藏时不能跳到可见的作者信息栏');
assert.equal(stablePosition.host.style.top,'164px');
railRect={width:40,height:40,top:240,bottom:280,left:780};stablePosition.callbacks.detect();
assert.equal(stablePosition.host.style.left,'778px','侧栏恢复后跟随新位置');
assert.equal(stablePosition.host.style.top,'184px');
stablePosition.go(subscribedURL);
railRect={width:0,height:0,top:0,bottom:0,left:0};stablePosition.go('https://www.douyin.com/?recommend=1');
assert.equal(stablePosition.host.style.left,'298px','离开作品页后重置侧栏状态，允许首次使用作者信息栏');
let liveLeft=840, railTop=160;
const liveAvatar={href:'https://live.douyin.com/123',getBoundingClientRect:()=>({width:40,height:40,top:railTop+40,bottom:railTop+80,left:liveLeft})};
const liveRail={parentElement:null,getBoundingClientRect:()=>({width:60,height:300,top:railTop,bottom:railTop+300,left:liveLeft-10}),querySelector:s=>s==='[data-e2e="video-player-digg"]'?digg:null,querySelectorAll:s=>s==='a[href*="live.douyin.com"]'?[liveAvatar]:[]};
const digg={parentElement:liveRail};liveAvatar.parentElement=liveRail;
const liveFeed={...feed,contains:n=>n===liveRail,querySelectorAll:s=>s==='[data-e2e="video-player-digg"]'?[digg]:[]};
const livePosition=browser('https://www.douyin.com/?recommend=1',{feed:liveFeed});
assert.equal(livePosition.host.style.left,'838px','直播头像可作为操作栏定位，不要求作品作者链接');
assert.equal(livePosition.host.style.top,'104px');
liveLeft=620;railTop=120;livePosition.callbacks.detect();
assert.equal(livePosition.host.style.left,'618px','评论布局变化时跟随操作栏');
assert.equal(livePosition.host.style.top,'64px');
positioning.go(subscribedURL);
assert.equal(positioning.host.style.left,'1136px','进入博主页时不能沿用原视频的位置');
const recommendation=browser('https://www.douyin.com/?recommend=1',{feed});
assert.equal(recommendation.host.hidden,false,'推荐流播放作品时显示入口');
assert.equal(recommendation.byClass('creator').textContent,'推荐博主','显示当前推荐作品的博主');
activate(recommendation);
recommendation.find('下载当前作品').click();
assert.equal(body(request(recommendation,'POST','/api/tasks')).link,`https://www.douyin.com/video/${feedId}`,'下载当前推荐作品而不是其他作品');
const hover=browser('https://www.douyin.com/jingxuan',{preview:feed});
assert.equal(hover.host.hidden,true,'首页网格悬停预览不能显示入口');
const invalidFeed=browser('https://www.douyin.com/?recommend=1',{feed:{...feed,getAttribute:()=> '123'}});
assert.equal(invalidFeed.host.hidden,true,'没有有效作品标识的推荐流不能显示入口');
const profileWithBackground=browser(subscribedURL,{feed});
assert.equal(profileWithBackground.host.hidden,false);
assert.equal(profileWithBackground.byClass('creator').textContent,'甲博主','博主页不能识别背景推荐流的博主');
assert.equal(profileWithBackground.find('下载当前作品').disabled,true,'博主页不能下载背景推荐流作品');

const home=browser('https://www.douyin.com/jingxuan');
assert.equal(home.host.hidden,true,'首页网格无作品详情时隐藏入口');
home.go(subscribedURL);
assert.equal(home.host.hidden,false,'博主页显示入口');
assert.equal(home.byClass('creator').textContent,'甲博主','博主页显示真实名字');
activate(home);
assert.equal(home.requests.filter(r=>!r.url.includes('/api/author-count?')).length,2,'打开面板并行读取博主和任务');
assert.ok(request(home,'GET','/api/authors'));
assert.ok(request(home,'GET','/api/tasks'));
home.go(authorB);
home.respond(request(home,'GET','/api/authors'),[authorA,knownB]);
home.respond(request(home,'GET','/api/tasks'),[
  {kind:'author',status:'completed',author_id:'a',updated_at:'2025-01-02T03:04:05Z'},
  {kind:'author',status:'completed',link:authorB,updated_at:'2026-05-06T07:08:09Z'}
]);
await tick();
assert.equal(home.requests.filter(r=>!r.url.includes('/api/author-count?')).length,2,'切作者复用在途状态读取');
assert.equal(home.byClass('creator').textContent,'乙博主','旧请求返回后仍显示当前博主');
assert.ok(home.find('更新当前订阅'),'已订阅的博主显示更新当前订阅');
assert.equal(home.byClass('subscription-state').textContent,'已订阅 · 每7天更新','订阅状态应明确显示已订阅和更新频率');
assert.match(home.byClass('last-success').textContent,/2026/,'最后成功时间匹配当前博主');
assert.doesNotMatch(home.byClass('last-success').textContent,/2025/,'旧博主完成时间不得污染当前状态');
assert.ok(home.byClass('next-check').textContent,'显示下次检查信息');
assert.equal(home.find('查看归档 ↗').href,'http://nas.example:8090/#tasks');
assert.deepEqual(home.elements.find(e=>e.getAttribute('aria-label')==='博主更新周期').children.map(e=>e.value),
  ['once','three_days','weekly','monthly','quarterly','yearly']);
home.page.href=subscribedURL;
home.callbacks['document:click']();home.callbacks.popstate();
assert.equal(home.frame.length,1,'连续事件合并为一帧检测');
home.frame.shift()();
assert.equal(home.byClass('creator').textContent,'甲博主','事件驱动更新当前博主');
home.page.href=authorB;
home.callbacks.mutation([{target:{matches:()=>true},addedNodes:[],removedNodes:[]}]);
assert.equal(home.frame.length,1,'相关页面变化安排检测');
home.frame.shift()();
assert.equal(home.byClass('creator').textContent,'乙博主','页面变化后更新当前博主');

const retry=browser();
activate(retry);
request(retry,'GET','/api/authors').onerror();
retry.respond(request(retry,'GET','/api/tasks'),[]);
await tick();
retry.callbacks.detect();retry.callbacks.detect();retry.callbacks.detect();
await tick();
assert.equal(retry.requests.filter(r=>r.method==='GET'&&r.url.endsWith('/api/authors')).length,1,'失败后不自动反复请求');
retry.byClass('refresh-state').click();
assert.equal(retry.requests.filter(r=>r.method==='GET'&&r.url.endsWith('/api/authors')).length,2,'失败后手动刷新');
retry.respond(request(retry,'GET','/api/authors'),[authorA]);
const retryTasks=request(retry,'GET','/api/tasks');
if(retry.requests.filter(r=>r.method==='GET'&&r.url.endsWith('/api/tasks')).length>1)retry.respond(retryTasks,[]);
await tick();

const once=browser();
activate(once);
select(once,'once');
once.respond(request(once,'GET','/api/authors'),[authorA]);
once.respond(request(once,'GET','/api/tasks'),[]);
await tick();
assert.equal(once.elements.find(e=>e.getAttribute('aria-label')==='博主更新周期').value,'once','载入结果不能覆盖用户已选择的周期');
const onceButton=once.byClass('submit-plan');
assert.equal(onceButton.textContent,'下载博主一次');
onceButton.click();
await tick();
const oncePatch=request(once,'PATCH','/api/authors/a');
assert.equal(body(oncePatch).frequency,'once');
assert.equal(request(once,'POST','/api/authors/a/check'),undefined,'保存周期成功前不能提交检查');
once.respond(oncePatch,{...authorA,enabled:false});
await tick();
once.respond(request(once,'POST','/api/authors/a/check'),{id:'task-a'});
await tick();
assert.equal(onceButton.dataset.state,'submitted');
assert.equal(onceButton.textContent,'已提交');
once.go(authorB);
assert.notEqual(onceButton.dataset.state,'submitted','切作者清除提交状态');

const scheduled=browser(authorB);
activate(scheduled);
scheduled.respond(request(scheduled,'GET','/api/authors'),[]);
scheduled.respond(request(scheduled,'GET','/api/tasks'),[]);
await tick();
select(scheduled,'weekly');
const plan=scheduled.byClass('submit-plan');
assert.equal(plan.textContent,'下载博主并订阅');
plan.click();
await tick();
const create=request(scheduled,'POST','/api/authors');
assert.equal(body(create).url,authorB);
assert.equal(body(create).name,'乙博主');
assert.equal(body(create).frequency,'weekly');
assert.equal(request(scheduled,'POST','/api/authors/b/check'),undefined);
scheduled.respond(create,knownB);
await tick();
const check=request(scheduled,'POST','/api/authors/b/check');
assert.ok(check,'创建成功后提交检查');
check.onerror();
await tick();
assert.notEqual(plan.dataset.state,'submitted','检查失败不能把部分成功显示为已提交');

const moving=browser();
activate(moving);
moving.respond(request(moving,'GET','/api/authors'),[authorA]);
moving.respond(request(moving,'GET','/api/tasks'),[]);
await tick();
select(moving,'weekly');
const movingPlan=moving.byClass('submit-plan');
movingPlan.click();
await tick();
const oldPatch=request(moving,'PATCH','/api/authors/a');
const movingServer=moving.elements.find(el=>el.type==='url');
movingServer.value='https://new.example';moving.elements.find(el=>el.tag==='form').submit();
moving.respond(oldPatch,authorA);
await tick();
const oldCheck=request(moving,'POST','/api/authors/a/check');
assert.equal(new URL(oldCheck.url).origin,'http://nas.example:8090','处理中切换网站不会把检查发送到新网站');
moving.respond(oldCheck,{id:'old-task'});
await tick();
assert.notEqual(movingPlan.dataset.state,'submitted','旧网站的结果不能显示为新网站已提交');

const direct=browser(authorB);
activate(direct);
direct.respond(request(direct,'GET','/api/authors'),[knownB]);
direct.respond(request(direct,'GET','/api/tasks'),[]);
await tick();
direct.find('更新当前订阅').click();
assert.ok(request(direct,'POST','/api/authors/b/check'),'已登记博主直接提交检查，不改订阅');
assert.equal(request(direct,'PATCH','/api/authors/b'),undefined);
assert.equal(request(direct,'POST','/api/authors'),undefined,'更新当前订阅不能创建或修改订阅');
direct.respond(request(direct,'POST','/api/authors/b/check'),{id:'task-b'});
await tick();
assert.equal(direct.byClass('creator-status').dataset.state,'submitted','提交反馈不等待状态刷新');
const refreshed=request(direct,'GET','/api/authors');
assert.equal(direct.requests.filter(r=>r.method==='GET'&&r.url.endsWith('/api/authors')).length,2,'提交后重新获取下次检查时间');
direct.respond(refreshed,[{...knownB,next_check:1924992000}]);
await tick();
assert.match(direct.byClass('next-check').textContent,/2031/,'显示网站重新安排的检查时间');
direct.byClass('refresh-state').click();
direct.respond(request(direct,'GET','/api/authors'),[knownB]);
direct.respond(request(direct,'GET','/api/tasks'),[]);
await tick();
direct.find('更新当前订阅').click();
assert.equal(direct.requests.filter(r=>r.method==='POST'&&r.url.endsWith('/api/authors/b/check')).length,2,'手动刷新后可再次提交');
direct.respond(request(direct,'POST','/api/authors/b/check'),{id:'task-b2'});
await tick();
const serverInput=direct.elements.find(el=>el.type==='url'), form=direct.elements.find(el=>el.tag==='form');
serverInput.value='https://other.example';form.submit();
direct.find('下载全部作品').click();
direct.respond(request(direct,'GET','/api/authors'),[knownB]);
direct.respond(request(direct,'GET','/api/tasks'),[]);
await tick();
const directOther=direct.requests.findLast(r=>r.method==='POST'&&r.url.startsWith('https://other.example'));
assert.ok(directOther,'切换网站后同一博主仍可提交');

const changedToken=browser();
activate(changedToken);
changedToken.respond(request(changedToken,'GET','/api/authors'),[authorA]);
changedToken.respond(request(changedToken,'GET','/api/tasks'),[]);
await tick();
changedToken.find('更新当前订阅').click();
const oldTokenCheck=request(changedToken,'POST','/api/authors/a/check');
changedToken.elements.find(el=>el.type==='password').value='new-test-token';
changedToken.elements.find(el=>el.tag==='form').submit();
changedToken.respond(oldTokenCheck,{id:'old-token-task'});
await tick();
assert.notEqual(changedToken.byClass('creator-status').dataset.state,'submitted','同网站更换密钥后旧响应不能显示提交成功');
assert.doesNotMatch(changedToken.byClass('status').textContent,/已提交/);

const staleState=browser();
activate(staleState);
staleState.respond(request(staleState,'GET','/api/authors'),[authorA]);
staleState.respond(request(staleState,'GET','/api/tasks'),[]);
await tick();
staleState.byClass('refresh-state').click();
const oldAuthors=request(staleState,'GET','/api/authors'),oldTasks=request(staleState,'GET','/api/tasks');
staleState.find('更新当前订阅').click();
staleState.respond(request(staleState,'POST','/api/authors/a/check'),{id:'new-task'});
await tick();
staleState.respond(request(staleState,'GET','/api/authors'),[{...authorA,next_check:1924992000}]);
await tick();
staleState.respond(oldAuthors,[authorA]);staleState.respond(oldTasks,[]);
await tick();
assert.match(staleState.byClass('next-check').textContent,/2031/,'较早的状态请求不能覆盖提交后的新计划');

const missingAuthor=browser();
activate(missingAuthor);
missingAuthor.respond(request(missingAuthor,'GET','/api/authors'),[]);
missingAuthor.respond(request(missingAuthor,'GET','/api/tasks'),[{kind:'author',status:'completed',link:'https://v.douyin.com/short/',updated_at:'2024-01-02T03:04:05Z'}]);
await tick();
missingAuthor.go(`https://www.douyin.com/video/${sample}`);
assert.equal(missingAuthor.byClass('last-success').textContent,'暂无记录','未识别作者时不能把短链接任务当作当前作者');

const inactive=browser(authorB);
activate(inactive);
inactive.respond(request(inactive,'GET','/api/authors'),[{...knownB,enabled:false}]);
inactive.respond(request(inactive,'GET','/api/tasks'),[]);
await tick();
assert.equal(inactive.byClass('subscription-state').textContent,'未订阅','已登记但未启用的博主显示未订阅');
assert.ok(inactive.find('下载全部作品'),'已登记但未订阅时显示下载全部作品');
inactive.find('下载全部作品').click();
assert.ok(request(inactive,'POST','/api/authors/b/check'),'已登记未订阅博主可直接提交下载');
assert.equal(request(inactive,'PATCH','/api/authors/b'),undefined,'下载全部作品不能启用订阅');
assert.equal(request(inactive,'POST','/api/authors'),undefined,'下载全部作品不能创建订阅');

const unknown=browser(authorB);
activate(unknown);
unknown.respond(request(unknown,'GET','/api/authors'),[]);
unknown.respond(request(unknown,'GET','/api/tasks'),[]);
await tick();
assert.ok(unknown.find('下载全部作品'),'未知博主显示下载全部作品');
unknown.find('下载全部作品').click();
const authorTask=request(unknown,'POST','/api/tasks');
assert.equal(body(authorTask).kind,'author');
assert.equal(body(authorTask).link,authorB);
assert.equal(request(unknown,'POST','/api/authors'),undefined,'直接更新不改变订阅');
assert.equal(request(unknown,'PATCH','/api/authors/b'),undefined,'未订阅博主下载全部作品不能开启订阅');

const detail=browser(`https://www.douyin.com/video/${sample}`);
activate(detail);
detail.find('下载当前作品').click();
const detailTask=request(detail,'POST','/api/tasks');
assert.equal(body(detailTask).kind,'detail');
assert.equal(body(detailTask).link,`https://www.douyin.com/video/${sample}`);
let liveWorkId=feedId;
const liveNode={textContent:'直播博主',__reactFiber$test:{memoizedProps:{awemeInfo:{awemeId:feedId,authorInfo:{secUid:'MS4wLjABAAAA_live_author'}}}}};
const liveCard={...feed,getAttribute:()=>liveWorkId,querySelector:s=>s==='[data-e2e="feed-video-nickname"]'?{textContent:'直播博主'}:null,querySelectorAll:()=>[]};
const liveBrowser=()=>browser('https://www.douyin.com/?recommend=1',{feed:liveCard,pageDocument:{querySelector:()=>({querySelector:()=>liveNode})}});
const liveAuthor='https://www.douyin.com/user/MS4wLjABAAAA_live_author';
const liveDownload=liveBrowser();activate(liveDownload);
liveDownload.respond(request(liveDownload,'GET','/api/authors'),[]);liveDownload.respond(request(liveDownload,'GET','/api/tasks'),[]);await tick();
assert.equal(liveDownload.find('下载全部作品').disabled,false,'直播头像无主页链接时仍可下载作者作品');
liveDownload.find('下载全部作品').click();
assert.equal(body(request(liveDownload,'POST','/api/tasks')).link,liveAuthor,'detect通过页面环境读取当前作品作者后提交主页，不能发直播间');
liveDownload.respond(request(liveDownload,'POST','/api/tasks'),{id:'live-once'});await tick();
const livePlan=liveBrowser();activate(livePlan);
livePlan.respond(request(livePlan,'GET','/api/authors'),[]);livePlan.respond(request(livePlan,'GET','/api/tasks'),[]);await tick();
select(livePlan,'weekly');livePlan.find('下载博主并订阅').click();
assert.equal(body(request(livePlan,'POST','/api/authors')).url,liveAuthor);
assert.equal(body(request(livePlan,'POST','/api/authors')).frequency,'weekly');
livePlan.respond(request(livePlan,'POST','/api/authors'),{id:'live-author',url:liveAuthor,enabled:true,interval_minutes:10080});await tick();
assert.ok(request(livePlan,'POST','/api/authors/live-author/check'),'直播博主订阅保存后仍会提交一次更新');
livePlan.respond(request(livePlan,'POST','/api/authors/live-author/check'),{id:'live-weekly'});await tick();
liveWorkId=sample;liveDownload.callbacks.detect();
assert.equal(liveDownload.find('下载全部作品').disabled,true,'切到另一作品时不能复用旧React作者');
assert.equal(liveDownload.find('下载博主一次').disabled,true);
const countBrowser=options=>{const authorNode={...feedAuthor,href:subscribedURL};const scope={...feed,querySelector:s=>s==='a[data-e2e="video-author-name"], a[data-e2e="video-avatar"]'?authorNode:feed.querySelector(s)};const b=browser('https://www.douyin.com/?recommend=1',{feed:scope,...options});return {...b,setAuthor(link){authorNode.href=link;b.callbacks.detect();}}};
const countRequest=b=>request(b,'GET','/api/author-count');
const countReply=(b,r,count,author=subscribedURL)=>b.respond(r,{sec_uid:author.split('/').pop(),count});
const counts=countBrowser();
assert.equal(countRequest(counts),undefined,'面板关闭时先等作者稳定');counts.frame.shift()();assert.ok(countRequest(counts),'面板关闭时后台预读作品数量');activate(counts);countReply(counts,countRequest(counts),1234);await tick();
assert.equal(counts.byClass('work-count').textContent,'· 共 1,234 个作品');
assert.equal(counts.profileRequests.length,0,'推荐页不能抓博主页或要求跳转主页');
assert.equal(new URL(countRequest(counts).url).origin,'http://nas.example:8090');
assert.equal(new URL(countRequest(counts).url).searchParams.get('sec_uid'),subscribedURL.split('/').pop());
assert.equal(countRequest(counts).headers.Authorization,'Bearer test-token','计数请求只向已配置收藏库发送认证');
assert.equal(countRequest(counts).timeout,10000,'超时后及时显示不可用');
counts.callbacks.detect();counts.callbacks.detect();await tick();assert.equal(counts.requests.filter(r=>new URL(r.url).pathname==='/api/author-count').length,1,'重复检测复用当前博主数量');
let feedVisible=true;
const transitionFeed={...feed,getBoundingClientRect:()=>feedVisible?{width:1000,height:700,top:0,bottom:700,left:0}:{width:0,height:0,top:0,bottom:0,left:0}};
const transitionCount=browser('https://www.douyin.com/?recommend=1',{feed:transitionFeed});activate(transitionCount);
feedVisible=false;transitionCount.callbacks.detect();
assert.equal(transitionCount.elements.find(el=>el.getAttribute('aria-label')==='抖音收藏库下载面板').hidden,false,'刷到下一条的空帧不能收起已打开的面板');
assert.equal(transitionCount.find('下载当前作品').disabled,true,'切换中不能提交上一条作品');
feedVisible=true;transitionCount.callbacks.detect();
countReply(transitionCount,countRequest(transitionCount),321,creator.href);await tick();
assert.equal(transitionCount.byClass('work-count').textContent,'· 共 321 个作品','新作品就绪后继续读取，无需重开面板');
const switching=countBrowser();activate(switching);const oldCount=countRequest(switching);
switching.setAuthor(authorB);assert.equal(switching.byClass('work-count').textContent,'· 数量读取中…','换作者立即清除前一位数量');
countReply(switching,oldCount,999);await tick();assert.equal(switching.byClass('work-count').textContent,'· 数量读取中…','前一博主的迟到响应不能覆盖当前博主');
countReply(switching,countRequest(switching),47,authorB);await tick();assert.equal(switching.byClass('work-count').textContent,'· 共 47 个作品');
switching.setAuthor(subscribedURL);assert.equal(switching.byClass('work-count').textContent,'· 共 999 个作品','同次打开切回作者复用数量');
const unavailable=countBrowser();activate(unavailable);await tick();
unavailable.respond(countRequest(unavailable),{detail:'暂时无法读取'},503);await tick();
assert.equal(unavailable.byClass('work-count').textContent,'· 数量暂不可用');
assert.equal(unavailable.find('下载全部作品').disabled,false,'数量读取失败不能阻止下载');
const zero=countBrowser();activate(zero);countReply(zero,countRequest(zero),0);await tick();
assert.equal(zero.byClass('work-count').textContent,'· 共 0 个作品','资料明确返回零作品可显示0');
const refreshedCount=countBrowser();activate(refreshedCount);const beforeRefresh=countRequest(refreshedCount);
refreshedCount.respond(request(refreshedCount,'GET','/api/authors'),[]);refreshedCount.respond(request(refreshedCount,'GET','/api/tasks'),[]);await tick();
refreshedCount.byClass('refresh-state').click();assert.notEqual(countRequest(refreshedCount),beforeRefresh,'显式刷新重新核对作品数');
countReply(refreshedCount,countRequest(refreshedCount),48);await tick();countReply(refreshedCount,beforeRefresh,47);await tick();
assert.equal(refreshedCount.byClass('work-count').textContent,'· 共 48 个作品','同一作者的旧请求不能覆盖刷新后的新数量');
let localInfo=null;
const localDoc={querySelector(){return null},querySelectorAll(){return localInfo?[{__reactFiber$test:{memoizedProps:{userInfo:localInfo,currentTab:'post',setCurrentTab(){}}}}]:[]}};
const localCount=browser(subscribedURL,{pageDocument:localDoc});activate(localCount);await tick();
assert.equal(localCount.profileRequests.length,0,'当前主页尚未就绪时也不能另抓一次主页');
localInfo={secUid:subscribedURL.split('/').pop(),awemeCount:70};localCount.callbacks.detect();
assert.equal(localCount.byClass('work-count').textContent,'· 共 70 个作品','资料稍后渲染成功时自动恢复，不被空缓存锁住');
assert.equal(localCount.profileRequests.length,0,'当前主页直接读取已有数据');
localCount.go(authorB);assert.equal(localCount.byClass('work-count').textContent,'· 数量读取中…','切换URL先于资料更新时不能借用旧作者');
localInfo={secUid:authorB.split('/').pop(),awemeCount:71};localCount.callbacks.detect();
assert.equal(localCount.byClass('work-count').textContent,'· 共 71 个作品');
assert.equal(localCount.profileRequests.length,0);
for(const bad of [{sec_uid:authorB.split('/').pop(),count:7},{sec_uid:subscribedURL.split('/').pop(),count:-1},{sec_uid:subscribedURL.split('/').pop(),count:'1.2万'}]){
 const invalidCount=countBrowser();activate(invalidCount);invalidCount.respond(countRequest(invalidCount),bad);await tick();
 assert.equal(invalidCount.byClass('work-count').textContent,'· 数量暂不可用','拒绝错误作者或无效数量');
 invalidCount.callbacks.detect();assert.equal(invalidCount.requests.filter(r=>new URL(r.url).pathname==='/api/author-count').length,1,'失败不自动循环请求');
}
console.log('PASS: 作品/作者识别、直播博主下载与订阅、作品总数与切换隔离、订阅与提交反馈、媒体凭据边界、视图暂停、图集边界');
