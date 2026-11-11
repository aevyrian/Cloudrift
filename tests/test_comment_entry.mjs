import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
const {create} = createRequire(import.meta.url)('../app/static/comment-entry.js');
class Node {
  constructor() { this.hidden=false;this.textContent='';this.events={};this.checked=false; }
  addEventListener(type,callback) { this.events[type]=callback; }
  removeAttribute(name) { delete this[name]; }
}
function harness(server, options={}) {
  const ids=['comment-channel-form','comment-channel-message','comment-batch-panel','comment-batch-status','comment-batch-stop','comment-batch-resume','comment-batch-link'];
  const nodes=Object.fromEntries(ids.map(id=>[id,new Node()])), radios=[new Node(),new Node()], calls=[], tabs=[], notices=[];
  radios[0].value='browser';radios[1].value='core';
  nodes['comment-channel-form'].querySelectorAll=()=>radios;
  nodes['comment-channel-form'].querySelector=()=>radios.find(r=>r.checked);
  const api=async(path,method='GET',body)=>{
    calls.push({path,method,body});
    if(path==='/api/comment-channel'){if(method==='PUT')server.channel=body.channel;return{channel:server.channel};}
    if(path==='/api/comment-accounts')return{paused:Boolean(server.paused),primary:{bound:true},secondary:{bound:true}};
    if(path==='/api/comment-batches/current')return{batch:server.batch?{...server.batch}:null};
    if(path.endsWith('/comment-batches')){server.batch={id:'a'.repeat(32),author_id:'author',author_name:'博主',channel:body.channel,status:body.channel==='browser'?'waiting_browser':'running',completed:0,total:3,added:0};return{batch:{...server.batch}};}
    if(path.endsWith('/browser'))return{batch:{...server.batch},work:{work_id:'7431234567890123456',token:server.badToken?'bad':'b'.repeat(32),claimed:Boolean(server.claimed)}};
    if(path.endsWith('/stop')){server.batch.status='stopped';return{batch:{...server.batch}};}
    if(path.endsWith('/resume')){server.batch.status=server.batch.channel==='browser'?'waiting_browser':'running';return{batch:{...server.batch}};}
    throw Error('Unexpected API '+path);
  };
  const openWindow=options.blocked?()=>null:()=>{const tab={closed:false,location:{href:''},close(){this.closed=true;}};tabs.push(tab);return tab;};
  const entry=create({api,document:{getElementById:id=>nodes[id]},notice:(...args)=>notices.push(args),openWindow,confirm:options.confirm||(()=>true)});
  return{entry,nodes,radios,calls,tabs,notices};
}
{
  const server={channel:'core'},first=harness(server);await first.entry.refreshBatch();await first.entry.batch({id:'author'});
  const created=first.calls.find(c=>c.path.endsWith('/comment-batches'));
  assert.match(created.body.request_id,/^[a-f0-9]{32}$/);assert.equal(created.body.allow_primary,true);
  assert.ok(!first.calls.some(c=>/\/api\/works\//.test(c.path)),'网页不再调度单作品');
  first.entry.reset();assert.equal(server.batch.status,'running');assert.ok(!first.calls.some(c=>c.path.endsWith('/stop')),'关闭/退出不会取消服务器任务');
  server.batch.completed=2;server.batch.added=15;
  const reopened=harness(server);await reopened.entry.refreshBatch();
  assert.match(reopened.nodes['comment-batch-status'].textContent,/2 \/ 3/);assert.equal(reopened.nodes['comment-batch-stop'].disabled,false);
  await reopened.entry.stop();assert.equal(server.batch.status,'stopped');assert.equal(reopened.nodes['comment-batch-stop'].disabled,true);reopened.entry.reset();
}
{
  const server={channel:'core'}, h=harness(server,{confirm:()=>false});await h.entry.batch({id:'author'});
  assert.ok(!h.calls.some(c=>c.method==='POST'));h.entry.reset();
}
{
  const server={channel:'browser'},h=harness(server,{blocked:true});await h.entry.refreshBatch();await h.entry.batch({id:'author'});
  assert.equal(h.nodes['comment-batch-link'].hidden,false);assert.match(h.nodes['comment-batch-link'].href,/#library-comments=b{32}&library-comment-batch=a{32}$/);
  server.claimed=true;await h.entry.refreshBatch();assert.equal(h.nodes['comment-batch-link'].hidden,true,'已领取不引导再开标签');
  h.entry.reset();
}
{
  const server={channel:'core',batch:{id:'c'.repeat(32),channel:'core',status:'paused',total:3,completed:1}},h=harness(server,{confirm:()=>false});
  await h.entry.refreshBatch();assert.equal(h.nodes['comment-batch-resume'].hidden,false);await h.entry.resume();
  assert.ok(!h.calls.some(c=>c.path.endsWith('/resume')));assert.equal(server.batch.status,'paused');h.entry.reset();
  const allowed=harness(server);await allowed.entry.refreshBatch();await allowed.entry.resume();
  assert.equal(allowed.calls.find(c=>c.path.endsWith('/resume')).body.allow_primary,true);assert.equal(server.batch.status,'running');allowed.entry.reset();
}
{
  const server={channel:'browser',paused:true},h=harness(server);await h.entry.batch({id:'author'});
  assert.ok(!h.calls.some(c=>c.path.endsWith('/comment-batches')));h.entry.reset();
}
{
  const server={channel:'browser'},h=harness(server);await h.entry.refreshSettings();assert.equal(h.radios[0].checked,true);
  h.radios[0].checked=false;h.radios[1].checked=true;await h.nodes['comment-channel-form'].events.submit({preventDefault(){}});
  assert.deepEqual(h.calls.find(c=>c.method==='PUT').body,{channel:'core'});h.entry.reset();
}
console.log('Persistent comment progress, reopen, stop, resume and channel checks passed');
