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
  const document={hidden:false,fullscreenElement:null,addEventListener:(event,fn)=>{listeners[event]=fn},exitFullscreen:async()=>{document.fullscreenElement=null;listeners.fullscreenchange?.()}};
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

const b=browser();
b.state.view='favorites'; b.state.nextOffset=20; b.state.total=21;
const all=Array.from({length:21},(_,i)=>({...work(String(i+1)),favorite:true}));
for(const item of all.slice(0,20)) b.append(item);
b.setPending(Promise.resolve({favorite:false}));
b.feed.children[0].querySelector('.favorite-tool').click();await tick();
assert.equal(b.state.items.length,19);
assert.equal(b.state.nextOffset,19,'取消收藏后下一页从剩余作品的正确位置开始');
const remaining=all.slice(1), nextPage=remaining.slice(b.state.nextOffset,b.state.nextOffset+20);
assert.equal(nextPage[0].id,'21','下一页不能跳过原第21条');
assert.equal(remaining[19].id,'21');
{
  const player=browser(), first=player.append(work('first'));player.append(work('second'));const video=player.ready(first);
  let open=true;player.state.comments={isOpenFor:card=>open&&card===first,close(){open=false;}};
  video.ended=true;video.dispatch('ended');await tick();
  assert.equal(player.requested.length,0,'阅读评论时播完不得自动切换');
  open=false;player.test.syncPlayback();await tick();
  assert.equal(player.requested.length,0,'关闭评论后不得补触发刚才的自动切换');
}
{
  const player=browser(), first=player.append(work('first')), video=player.ready(first);let closed=0;
  player.state.comments={isOpenFor:()=>true,close(){closed++;}};player.document.hidden=true;player.test.syncPlayback();
  assert.equal(closed,0,'切换浏览器标签时保留评论面板');assert.equal(video.paused,true,'切换标签仍暂停播放');
}
{
  const moving=browser();moving.state.view='favorites';moving.state.nextOffset=20;moving.state.total=21;moving.state.hasMore=true;
  for(const item of all.slice(0,20))moving.append({...item,favorite:true});
  let resolveOld,resolveFresh;
  moving.setPending(new Promise(resolve=>{resolveOld=resolve;}));const oldPage=moving.test.loadLibrary();
  moving.setPending(Promise.resolve({favorite:false}));moving.feed.children[0].querySelector('.favorite-tool').click();
  moving.setPending(new Promise(resolve=>{resolveFresh=resolve;}));await tick();
  resolveOld({items:[all[20]],total:21,next_offset:21,ceiling:null,has_more:false});await oldPage;
  assert.equal(moving.state.items.length,19,'旧分页请求不能在取消后写入');
  resolveFresh({items:all.slice(1),total:20,next_offset:20,ceiling:null,has_more:false});await tick();
  assert.ok(moving.state.items.some(item=>item.id==='21'),'取消收藏时分页在途也不能漏掉作品');
}


const persisted=new Map([['library_access_key','new-test-token']]);
const staleState={token:'old-test-token',generation:0,navigation:0};
const dummy=()=>({replaceChildren(){},reset(){},value:'',hidden:false});
const logoutContext=vm.createContext({state:staleState,localStorage:{getItem:k=>persisted.get(k),removeItem:k=>persisted.delete(k)},
 $:()=>dummy(),document:{querySelectorAll:()=>[]},switchView(){},taskView:{update(){}},connectionMode(){},AbortSignal,
 fetch:async()=>({ok:false,status:401,json:async()=>({detail:'expired'})})});
vm.runInContext(source.slice(source.indexOf('  async function api('),source.indexOf('  function empty('))+source.slice(source.indexOf('  function disconnect('),source.indexOf('  function connectionMode('))+';globalThis.api=api',logoutContext);
await assert.rejects(logoutContext.api('/api/legacy'));
assert.equal(persisted.get('library_access_key'),'new-test-token','旧标签登出不能删掉新标签保存的密钥');
staleState.token='current-test-token';persisted.set('library_access_key',staleState.token);
await assert.rejects(logoutContext.api('/api/legacy'));
assert.equal(persisted.get('library_access_key'),undefined,'同一标签登出仍应清理自身密钥');
console.log('UI regressions passed: favorites pagination and cross-tab login isolation.');
