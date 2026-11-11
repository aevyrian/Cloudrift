import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';

const source = readFileSync(new URL('../app/static/app.js', import.meta.url), 'utf8');
const { playVisible, releasePlayback, preloadVideos } = createRequire(import.meta.url)('../app/static/app.js');
let cardReads = 0, allCardReads = 0, closed = 0, timerId = 0;
const timers = new Map();
const cards = Array.from({ length: 2000 }, (_, id) => {
  const video = {
    id, paused: true, ready: false, src: '', cancelled: 0,
    play() { this.paused = false; return Promise.resolve(); },
    pause() { this.paused = true; },
    getAttribute(name) { return name === 'src' ? this.src : null; },
    removeAttribute() { this.src = ''; },
    load() {},
    cancelPreparation() { this.cancelled++; },
    preparePlayback() { this.playbackReady = true; this.src = 'prepared'; if (this.playbackActive && !this.userPaused) this.play(); }
  };
  return { dataset: { work: String(id) }, querySelector(selector) { assert.equal(selector, 'video'); cardReads++; return video; }, video };
});
const feed = { children: cards, scrollTop: 0, clientHeight: 100, hidden: false,
  querySelectorAll(selector) { assert.equal(selector, '.work'); allCardReads += cards.length; return cards; } };
const state = { view: 'library', comments: { close() { closed++; } } };
const document = { hidden: false, fullscreenElement: null };
const context = vm.createContext({ state, document, $: () => feed, playVisible, releasePlayback, preloadVideos,
  setTimeout(fn) { const id = ++timerId; timers.set(id, fn); return id; }, clearTimeout(id) { timers.delete(id); } });
const globals = source.slice(source.indexOf('  let playbackFrame='), source.indexOf('  async function fetchCommentBlob('));
const sync = source.slice(source.indexOf('  function syncPlayback()'), source.indexOf('  function schedulePlayback()'));
vm.runInContext(globals + sync + '\nglobalThis.syncPlayback=syncPlayback;', context);
const runTimers = () => { const pending = [...timers.values()]; timers.clear(); for (const fn of pending) fn(); };

context.syncPlayback(); runTimers();
assert.equal(cards[0].video.playbackActive, true, '第一条应开始准备');
assert.equal(cards[0].video.paused, false, '第一条可以播放');
assert.equal(cards[1].video.playbackWarm, true, '下一条预热');
assert.equal(cards[2].video.playbackWarm, true, '第二条预热');
assert.ok(cardReads < 30 && allCardReads === 0, `2000条列表仅应查询相邻视频，实际 ${cardReads}/${allCardReads}`);

cardReads = 0; feed.scrollTop = 100000; context.syncPlayback();
assert.equal(cards[0].video.paused, true, '跳转后旧视频停止');
assert.equal(cards[0].video.src, '', '跳转后旧视频释放');
assert.equal(cards[1].video.src, '', '跳转后预热视频释放');
assert.equal(cards[2].video.playbackWarm, false, '跳转后预热任务取消');
assert.equal(cards[1000].video.playbackActive, true, '跳转到目标作品');
assert.equal(closed, 1, '切换作品时关闭旧评论面板');
runTimers();
assert.equal(cards[1001].video.playbackWarm, true);
assert.equal(cards[1002].video.playbackWarm, true);
assert.ok(cardReads < 30 && allCardReads === 0, `跳转不得遍历所有作品，实际 ${cardReads}/${allCardReads}`);

document.hidden = true; context.syncPlayback(); runTimers();
for (const id of [1000, 1001, 1002]) {
  assert.equal(cards[id].video.paused, true, `后台时视频${id}停止`);
  assert.equal(cards[id].video.playbackWarm, false, `后台时视频${id}停止预热`);
}
assert.ok(cards[1001].video.cancelled, '后台时取消预热准备');
document.hidden = false; cards[1000].video.userPaused = true; context.syncPlayback();
assert.equal(cards[1000].video.paused, true, '返回前台不覆盖手动暂停');

document.fullscreenElement = { closest: () => cards[1500] }; context.syncPlayback();
assert.equal(cards[1500].video.playbackActive, true, '独立作品全屏时跟随全屏作品');
assert.equal(cards[1000].video.src, '', '全屏切换释放旧作品');
document.fullscreenElement = null; feed.scrollTop = 120000; context.syncPlayback();
feed.scrollTop = 130000; context.syncPlayback(); runTimers();
assert.notEqual(cards[1201].video.playbackWarm, true, '快速切换后旧的预热定时器不能生效');
assert.equal(cards[1301].video.playbackWarm, true, '最后选中作品的预热仍正常');
feed.children = [cards[6], cards[7]]; feed.scrollTop = 0; context.syncPlayback(); runTimers();
assert.equal(cards[1300].video.src, '', '列表重置后旧视频连接必须释放');
assert.equal(cards[1301].video.playbackWarm, false, '列表重置后旧预热必须取消');
assert.equal(cards[6].video.playbackActive, true, '列表重置后可播放新的首条');
console.log('Feed performance: bounded lookup, scroll jumps, warm cancellation, background, manual pause and fullscreen.');
