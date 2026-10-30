import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';

const source=readFileSync(new URL('../app/static/app.js',import.meta.url),'utf8');
const helpers=createRequire(import.meta.url)('../app/static/app.js');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const work=id=>({id,type:'视频',author:'作者',files:[{name:`${id}.mp4`,url:`/api/media/${id}/0`}]});

function browser(){
  const frames=[], listeners={}, notices=[];
  let requested=[], pending;
  class Node {
    constructor(tag='div',name='',text=''){this.tagName=tag.toUpperCase();this.className=name;this.textContent=text;this.children=[];this.dataset={};this.style={};this.hidden=false;this.paused=true;this.ended=false;this.events={};this.attrs={};}
    append(...nodes){for(const n of nodes){n.parentElement=this;this.children.push(n)}}
    prepend(n){n.parentElement=this;this.children.unshift(n)}
    replaceChildren(...nodes){for(const n of this.children)n.parentElement=null;this.children=[];this.append(...nodes)}
    remove(){if(this.parentElement){this.parentElement.children=this.parentElement.children.filter(n=>n!==this);this.parentElement=null}}
    setAttribute(key,val){this.attrs[key]=String(val)}
    getAttribute(key){return this.attrs[key]??null}
    removeAttribute(key){delete this.attrs[key]}
    addEventListener(event,fn){(this.events[event]??=[]).push(fn)}
    dispatch(event){for(const fn of this.events[event]||[])fn({target:this,preventDefault(){}})}
    click(){this.dispatch('click')}
    querySelector(selector){return this.querySelectorAll(selector)[0]||null}
    querySelectorAll(selector){const result=[];const test=n=>selector==='video'?n.tagName==='VIDEO':selector==='button'?n.tagName==='BUTTON':selector.startsWith('.')?n.className.split(/\s+/).includes(selector.slice(1)):false;const visit=n=>{for(const child of n.children){if(test(child))result.push(child);visit(child)}};visit(this);return result}
    getBoundingClientRect(){return {height:0,top:0,bottom:0}}
    get isConnected(){for(let n=this;n;n=n.parentElement)if(n===feed)return true;return false}
    get nextElementSibling(){const siblings=this.parentElement?.children||[];return siblings[siblings.indexOf(this)+1]||null}
    get previousElementSibling(){const siblings=this.parentElement?.children||[];return siblings[siblings.indexOf(this)-1]||null}
    scrollIntoView(){feed.scrollTop=feed.children.indexOf(this)*feed.clientHeight;requested.push(this.dataset.work)}
    play(){this.paused=false;return Promise.resolve()}
    pause(){this.paused=true}
    load(){}
    closest(selector){return selector==='.work'&&this.className.split(/\s+/).includes('work')?this:null}
  }
  const feed=new Node();feed.clientHeight=100;feed.scrollTop=0;
  const author=new Node('select');author.value='';author.options=[];author.selectedOptions=[];
  const nodes={feed,'search-query':{value:''},'search-author':author,'library-label':{},'back-recommendations':{},'shuffle-library':{},'clear-search':{}};
  const state={view:'library',favoriteKind:'works',items:[],generation:0,hasMore:false,loadingLibrary:false,nextOffset:0,ceiling:null,seed:1,token:'test',muted:false};
  const document={hidden:false,fullscreenElement:null,addEventListener:(event,fn)=>{listeners[event]=fn},exitFullscreen:async()=>{document.fullscreenElement=null;feed.clientHeight=100;listeners.fullscreenchange?.()}};
  feed.requestFullscreen=async()=>{document.fullscreenElement=feed;feed.clientHeight=200;listeners.fullscreenchange?.()};feed.focus=()=>{};
  const context=vm.createContext({state,document,$:id=>nodes[id],element:(...args)=>new Node(...args),
    action:(label,operation)=>{const el=new Node('button','',label);el.addEventListener('click',async()=>{try{await operation()}catch(error){notices.push(error.message)}});return el},
    ...helpers,mediaURL:path=>path,formatDate:()=>'',empty(){},notice:text=>notices.push(text),
    api:()=>pending,URLSearchParams,Option:function(label,value){return {textContent:label,value}},
    crypto:{getRandomValues:array=>array},performance:{now:()=>0},
    IntersectionObserver:class{observe(){}unobserve(){}disconnect(){}},
    setTimeout:()=>1,clearTimeout(){},requestAnimationFrame:fn=>{frames.push(fn);return frames.length}});
  vm.runInContext(source.slice(source.indexOf('  let playbackFrame='),source.indexOf('  function taskCard('))+
    '\nglobalThis.test={renderWork,syncPlayback,loadLibrary,setActive(value){activeCard=value},setFullscreen(value){fullscreenCard=value}};',context);
  function append(item){const card=context.test.renderWork(item,feed.children.length);feed.append(card);state.items.push(item);return card}
  function ready(card){const video=card.querySelector('video');video.playbackReady=true;video.playbackActive=true;video.paused=false;context.test.setActive(card);return video}
  function flush(){while(frames.length)frames.shift()()}
  return {state,feed,document,append,ready,flush,notices,requested,test:context.test,setPending(value){pending=value}};
}

{
  const b=browser(),first=b.append(work('one'));b.append(work('two'));const video=b.ready(first);
  assert.equal(video.muted,false,'作品默认有声音');
  assert.equal(video.loop,false,'普通视频播完不能循环，必须切下一条');
  video.ended=true;video.dispatch('ended');await tick();
  assert.equal(b.requested.at(-1),'two','真实 ended 事件切到下一条作品');
}
{
  const b=browser(),card=b.append({id:'live',type:'实况',files:[{name:'a.mp4',url:'/api/media/live/0'},{name:'b.jpg',url:'/api/media/live/1'}]});b.append(work('two'));
  const video=b.ready(card);assert.equal(video.loop,true,'图集或实况内视频保持循环');
  video.ended=true;video.dispatch('ended');await tick();assert.equal(b.requested.length,0,'图集内视频结束不能跳过其余图片');
}
{
  const b=browser(),card=b.append({id:'live',type:'实况',files:[{name:'a.mp4',url:'/api/media/live/0'}]});b.append(work('two'));
  const video=b.ready(card);assert.equal(video.loop,true,'只有视频文件的实况也应保持循环');
  video.ended=true;video.dispatch('ended');await tick();assert.equal(b.requested.length,0,'实况不应自动跳过');
}
for(const reason of ['background','paused','different card','scrolling','different view']){
  const b=browser(),first=b.append(work('one')),second=b.append(work('two')),video=b.ready(first);
  if(reason==='background')b.document.hidden=true;
  if(reason==='paused')video.userPaused=true;
  if(reason==='different card'){b.feed.scrollTop=100;b.test.setActive(second)}
  if(reason==='scrolling')b.feed.scrollTop=100;
  if(reason==='different view')b.state.view='tasks';
  video.ended=true;video.dispatch('ended');await tick();
  assert.equal(b.requested.length,0,`${reason} 时旧视频结束不得切换`);
}
{
  const b=browser(),first=b.append(work('one')),video=b.ready(first);b.state.hasMore=true;
  let resolve;b.setPending(new Promise(r=>resolve=r));
  video.ended=true;video.dispatch('ended');await tick();
  b.state.view='tasks';b.state.generation++;
  resolve({items:[work('two')],total:2,next_offset:2,ceiling:null,has_more:false});await tick();b.flush();await tick();
  assert.equal(b.requested.length,0,'自动加载下一页后切走，不得跳回旧作品');
}
{
  const b=browser(),first=b.append(work('one')),video=b.ready(first);b.state.hasMore=true;
  let resolve;b.setPending(new Promise(r=>resolve=r));const loading=b.test.loadLibrary();
  video.ended=true;video.dispatch('ended');await tick();assert.equal(b.requested.length,0,'等待正在加载的下一页');
  resolve({items:[work('two')],total:2,next_offset:2,ceiling:null,has_more:false});await loading;b.flush();await tick();
  assert.equal(b.requested.at(-1),'two','下一页加载完成后继续播放');
}
{
  const b=browser(),first=b.append(work('one')),video=b.ready(first);b.state.hasMore=true;
  let resolve;b.setPending(new Promise(r=>resolve=r));const loading=b.test.loadLibrary();
  video.ended=true;video.dispatch('ended');b.state.view='tasks';b.state.generation++;
  resolve({items:[work('two')],total:2,next_offset:2,ceiling:null,has_more:false});await loading;b.flush();await tick();
  assert.equal(b.requested.length,0,'异步加载期间离开作品库不能跳回旧作品');
}
{
  const b=browser(),first=b.append(work('one'));b.append(work('two'));const video=b.ready(first);
  b.document.fullscreenElement=first;b.test.setFullscreen(first);video.ended=true;video.dispatch('ended');await tick();
  assert.equal(b.document.fullscreenElement,null,'全屏播完退出当前作品全屏');
  assert.equal(b.requested.at(-1),'two','退出全屏后切换下一作品');
}
{
  const b=browser();b.append(work('one'));const second=b.append(work('two'));const third=b.append(work('three'));
  b.feed.scrollTop=100;const video=b.ready(second),controls=second.querySelector('.player-controls');
  controls.children.find(el=>el.getAttribute('aria-label')==='全屏播放').click();b.flush();
  assert.equal(b.document.fullscreenElement,b.feed,'全屏作用于作品流');
  assert.equal(b.feed.scrollTop,200,'进入全屏时保持原作品');
  video.ended=true;video.dispatch('ended');await tick();
  assert.equal(b.document.fullscreenElement,b.feed,'播完切换仍保持全屏');
  assert.equal(b.requested.at(-1),'three');b.test.syncPlayback();
  await b.document.exitFullscreen();b.flush();
  assert.equal(b.feed.scrollTop,200,'退出全屏后保持切换后的作品');
  assert.equal(b.document.fullscreenElement,null);
  assert.equal(third.dataset.work,'three');
}
{
  const b=browser(),gallery=b.append({id:'gallery',type:'图集',files:[{name:'a.jpg',url:'/api/media/gallery/0'},{name:'b.jpg',url:'/api/media/gallery/1'}]});
  const tools=gallery.querySelector('.work-tools');tools.children.find(el=>el.getAttribute('aria-label')==='全屏查看').click();b.flush();
  assert.equal(b.document.fullscreenElement,b.feed,'图集可直接进入作品流全屏');
  gallery.events.keydown[0]({target:{closest:()=>null},key:'ArrowRight',preventDefault(){}});
  assert.equal(gallery.querySelector('.work-index').textContent,'2 / 2','全屏仍可使用左右键翻图');
  gallery.events.keydown[0]({target:{closest:()=>({})},key:'ArrowLeft',preventDefault(){throw Error('控制条不应被拦截')}});
  assert.equal(gallery.querySelector('.work-index').textContent,'2 / 2','操作控件时不误触图集翻页');
}
{
  const listeners={},moves=[],feed={clientHeight:200,addEventListener:(name,fn)=>listeners[name]=fn,scrollBy:value=>moves.push(value)};
  const start=source.indexOf("  $('feed').addEventListener('keydown',"),end=source.indexOf("  $('feed').addEventListener('scroll',",start);
  vm.runInNewContext(source.slice(start,end),{$:()=>feed});
  let prevented=false;listeners.keydown({key:'ArrowDown',target:{closest:()=>null},preventDefault(){prevented=true}});
  assert.equal(prevented,true);assert.equal(moves[0].top,200,'上下键切换一屏作品');
  for(const kind of ['button','input','select','textarea','a','video','[contenteditable="true"]','.comments-panel']){
    let selector='';listeners.keydown({key:'ArrowUp',target:{closest:value=>{selector=value;return {}}},preventDefault(){throw Error('控件不得拦截')}});
    assert.ok(selector.includes(kind));
  }
  assert.equal(moves.length,1,'控件或评论中按键不切换作品');
}
console.log('Autoplay checks passed: normal videos, galleries, pause, background, loading and fullscreen.');
