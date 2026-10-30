(function () {
  'use strict';
  function mediaURL(path, token, origin) {
    const url = new URL(path, origin);
    if (url.origin !== new URL(origin).origin || !url.pathname.startsWith('/api/media/')) throw new Error('媒体地址不属于此网站');
    url.searchParams.set('token', token); return url.href;
  }
  function switchView(view, videos) {
    for (const video of videos) {video.playbackWarm=false;video.cancelPreparation?.();if(video.getAttribute)releasePlayback(video);else playVisible(video,false);}
    return ['library', 'favorites', 'tasks', 'authors', 'history', 'settings'].includes(view) ? view : 'library';
  }
  function moveGallery(index, change, count) { return Math.max(0, Math.min(Math.max(0, count - 1), index + change)); }
  function isVideoFile(file) { return /\.(mp4|webm|mov|m4v|mkv)(?:$|\?)/i.test(file.name || file.url || ''); }
  function authorInitial(name) { return Array.from((name || 'C').replace(/^@/, ''))[0] || 'C'; }
  function taskTitle(task) { const item=task.preview?.[0]; return task.kind==='author' ? item?.nickname || '正在识别博主' : item?.desc || (item?.nickname ? `${item.nickname}的作品` : '正在识别作品'); }
  function playVisible(video,active) { const wasActive=video.playbackActive;video.playbackActive=active;if(!active){video.pause();return;}if(video.userPaused)return;if(video.preparePlayback&&!video.playbackReady)return video.preparePlayback();if(wasActive&&!video.paused)return;video.preload='auto';return video.play().catch(()=>video.onAutoplayBlocked?.()); }
  function applySound(videos,muted) { for(const video of videos)video.muted=muted; }
  function preloadVideos(cards,index) { return cards.slice(index+1,index+3).map(card=>card.querySelector('video')).filter(Boolean); }
  function releasePlayback(video) { video.playbackWarm=false;playVisible(video,false);video.cancelPreparation?.();if(video.getAttribute('src')){video.removeAttribute('src');video.load();video.playbackReady=false;} }
  function keyError(key, repeat) {
    if (key.length < 8 || key.length > 128 || /[^\x21-\x7e]/.test(key)) return '请用 8–128 位英文字母、数字或符号，不含空格。';
    return key === repeat ? '' : '两次输入的密钥不一致，请再检查一下。';
  }
  function createCommentsPanel({ document, element, api, binary, objectURL, formatDate }) {
    let active = null, generation = 0, timer = 0, urls = [];
    function releaseImages() { for (const url of urls) objectURL.revokeObjectURL(url); urls = []; }
    const path = id => `/api/works/${encodeURIComponent(id)}/comments`;
    const button = (text, fn) => { const node = element('button', 'secondary', text); node.type = 'button'; node.addEventListener('click', fn); return node; };
    function close(focus = false) {
      generation++; clearTimeout(timer); timer = 0;
      releaseImages();
      if (active) { const previous = active; active = null; previous.panel.remove(); previous.trigger.setAttribute('aria-expanded', 'false'); if (focus) previous.trigger.focus(); }
    }
    function isOpenFor(card) { return Boolean(active && active.card === card); }
    function open(work, card, trigger) {
      close(); const serial = generation, current = () => active && active.serial === serial;
      const panel = element('section', 'comments-panel'); panel.setAttribute('role', 'region'); panel.setAttribute('aria-label', '离线评论'); panel.tabIndex = -1;
      const header = element('div', 'comments-header'), title = element('h2', '', '离线评论');
      const dismiss = button('×', () => close(true)); dismiss.setAttribute('aria-label', '关闭评论'); header.append(title, dismiss);
      const summary = element('p', 'comments-summary'), explanation = element('p', 'hint', '评论为本地累计保存，可能需要分次保存；打开面板不会自动采集。');
      const statusMessage = element('p', 'hint'); statusMessage.setAttribute('role', 'status'); statusMessage.setAttribute('aria-live', 'polite');
      const controls = element('div', 'comments-actions'), exports = element('div', 'comments-actions'), list = element('div', 'comments-list');
      const more = button('加载更多评论', () => loadPage(active.retryReset)); more.hidden = true;
      panel.append(header, summary, explanation, statusMessage, controls, exports, list, more);
      panel.addEventListener('keydown', event => { if (event.key === 'Escape') { event.preventDefault(); close(true); } event.stopPropagation(); });
      active = { serial, card, trigger, panel, offset: 0, canSave: false, busy: false, state: null, pageSerial: 0, renderedSerial: 0, pageLoading: false, retryReset: false, revision: 0 };
      card.append(panel); trigger.setAttribute('aria-expanded', 'true'); panel.focus();
      const localPath = path(work.id); let exporting = false;
      function report(error) { if (current()) statusMessage.textContent = error?.message || '操作失败，请重试。'; }
      async function operation(action) {
        if (!current() || active.busy || !active.canSave) return;
        active.busy = true; active.revision++; active.pageSerial++; active.pageLoading = false;
        try { await api(localPath, 'POST', { action }); if (current()) await loadPage(true); }
        catch (error) { if (current() && active.state) paintStatus(active.state, active.canSave); report(error); }
        finally { if (current()) { active.busy = false; if (!active.pageLoading) more.disabled = false; } }
      }
      async function cancel() {
        if (!current() || active.busy) return;
        active.busy = true; active.revision++; active.pageSerial++; active.pageLoading = false;
        try { await api(`${localPath}/cancel`, 'POST'); if (current()) await loadPage(true); }
        catch (error) { if (current() && active.state) paintStatus(active.state, active.canSave); report(error); }
        finally { if (current()) { active.busy = false; if (!active.pageLoading) more.disabled = false; } }
      }
      async function exportData(format) {
        if (exporting) return;
        exporting = true; for (const node of exports.children) node.disabled = true;
        try {
          const data = await binary(`${localPath}/export?format=${format}`); if (!current()) return;
          const url = objectURL.createObjectURL(data);
          const link = element('a'); link.href = url; link.download = `comments-${work.id}.${format}`; document.body.append(link); link.click(); link.remove();
          const revokeTimer = setTimeout(() => objectURL.revokeObjectURL(url), 60000); revokeTimer?.unref?.();
        } catch (error) { report(error); }
        finally { exporting = false; if (current()) for (const node of exports.children) node.disabled = false; }
      }
      function paintStatus(status, canSave) {
        if (!current()) return;
        active.canSave = Boolean(canSave); active.state = status;
        const total = Number(status.total) || 0, replies = Number(status.replies) || 0;
        const integrity = status.integrity === 'finished-visible' ? '本次可见内容已遍历' : status.integrity === 'partial' ? '部分保存' : '尚未保存';
        summary.textContent = `已保存 ${total} 条评论 · 其中回复 ${replies} 条 · ${integrity}${status.saved_at ? ` · 保存于 ${formatDate(status.saved_at)}` : ''}${status.platform_total != null ? ` · 平台报告数量 ${status.platform_total}` : ''}`;
        const operations = { queued: '等待保存', running: '正在保存', stopped: '已停止', failed: '保存失败', interrupted: '保存中断', finished: '本次保存结束', unsaved: '尚未保存评论' };
        statusMessage.textContent = `${operations[status.operation] || '状态未知'}${status.reason ? ` · ${status.reason}` : ''}${status.queue_paused ? ' · 评论队列已暂停' : ''}${!canSave ? ' · 暂时无法识别原作品，不能保存评论' : ''}`;
        controls.replaceChildren(); exports.replaceChildren();
        if (['queued', 'running'].includes(status.operation)) controls.append(button('停止保存', cancel));
        else if (canSave) {
          const action = status.integrity === 'partial' || ['failed','interrupted','stopped'].includes(status.operation) ? 'continue' : status.integrity === 'unsaved' ? 'save' : 'update';
          const label = status.operation === 'failed' ? '重试' : action === 'save' ? '保存评论' : action === 'continue' ? '继续保存' : '更新评论';
          controls.append(button(label, () => operation(action)));
          if (status.operation === 'failed') { const rescan = button('从头重新扫描', () => operation('update')); rescan.title = '从头读取并保留已有评论'; controls.append(rescan); }
        }
        if (status.integrity !== 'unsaved' || total) for (const format of ['json', 'csv', 'zip']) { const exportButton = button(format === 'zip' ? '导出含图片 ZIP' : `导出 ${format.toUpperCase()}`, () => exportData(format)); exportButton.disabled = exporting; exports.append(exportButton); }
        clearTimeout(timer);
        if (['queued', 'running'].includes(status.operation)) timer = setTimeout(poll, 5000);
      }
      async function poll() {
        const revision = active.revision;
        try {
          const result = await api(`${localPath}?offset=0&limit=50&root_id=`); if (!current() || revision !== active.revision) return;
          const wasActive = ['queued', 'running'].includes(active.state?.operation);
          paintStatus(result.status, result.can_save);
          if (wasActive && !['queued', 'running'].includes(result.status.operation)) await loadPage(true);
        } catch (error) { report(error); if (current()) timer = setTimeout(poll, 10000); }
      }
      function renderComment(item, parent, isReply = false) {
        const renderedSerial = active.renderedSerial, currentPage = () => current() && renderedSerial === active.renderedSerial;
        const row = element('article', 'comment-row'), text = element('p', 'comment-text', item.text || ''), meta = element('p', 'hint', `${item.nickname || '未知用户'}${item.reply_to_user ? ` 回复 ${item.reply_to_user}` : ''} · ${formatDate(item.created_at)} · ${Number(item.likes) || 0} 赞`);
        row.append(meta, text);
        if (item.reply_target_missing) row.append(element('p', 'hint', '上级评论未保存'));
        for (const attachment of item.attachments || []) {
          if (attachment.status !== 'saved') { row.append(element('p', 'hint', `图片未保存${attachment.error ? `：${attachment.error}` : ''}`)); continue; }
          const placeholder = element('span', 'hint', '正在读取本地图片…'); row.append(placeholder);
          binary(`/api/comment-assets/${encodeURIComponent(attachment.id)}`).then(blob => {
            if (!currentPage()) return; const url = objectURL.createObjectURL(blob); urls.push(url);
            const image = element('img', 'comment-image'); image.alt = attachment.kind === 'sticker' ? '评论图片表情' : '评论图片'; image.src = url; image.loading = 'lazy'; placeholder.replaceChildren(image);
          }).catch(() => { if (currentPage()) placeholder.textContent = '本地图片无法读取'; });
        }
        if (!isReply && item.reply_count > 0) {
          const replies = element('div', 'comment-replies'); let offset = 0, loading = false;
          const show = button(`查看 ${item.reply_count} 条回复`, async () => {
            if (loading || !currentPage()) return; loading = true; show.disabled = true;
            try {
              const result = await api(`${localPath}?offset=${offset}&limit=50&root_id=${encodeURIComponent(item.id)}`); if (!currentPage()) return;
              for (const reply of result.items) renderComment(reply, replies, true);
              offset = result.next_offset; if (offset == null) show.remove(); else show.textContent = '加载更多回复';
            } catch (error) { if (currentPage()) report(error); } finally { loading = false; show.disabled = false; }
          }); row.append(show, replies);
        }
        parent.append(row);
      }
      async function loadPage(reset) {
        if (!current() || (!reset && active.pageLoading)) return;
        const pageSerial = reset ? ++active.pageSerial : active.pageSerial, revision = active.revision;
        active.pageLoading = true;
        const offset = reset ? 0 : active.offset;
        more.disabled = true;
        try {
          const result = await api(`${localPath}?offset=${offset}&limit=50&root_id=`); if (!current() || pageSerial !== active.pageSerial || revision !== active.revision) return;
          if (reset) { active.renderedSerial++; releaseImages(); list.replaceChildren(); }
          paintStatus(result.status, result.can_save);
          for (const item of result.items) renderComment(item, list);
          active.offset = result.next_offset; active.retryReset = false; more.textContent = '加载更多评论'; more.hidden = result.next_offset == null;
          if (reset && !result.items.length) {
            const text = result.status.integrity === 'finished-visible' && result.status.operation === 'finished' ? '本次没有可见评论。' : '还没有本地评论内容。';
            list.append(element('p', 'hint', text));
          }
        } catch (error) { if (current() && pageSerial === active.pageSerial && revision === active.revision) { report(error); active.retryReset = reset; more.textContent = '重试读取评论'; more.hidden = false; } }
        finally { if (current() && pageSerial === active.pageSerial && revision === active.revision) { active.pageLoading = false; more.disabled = false; } }
      }
      loadPage(true);
    }
    return { open, close, isOpenFor };
  }
  if (typeof module !== 'undefined' && module.exports) { module.exports = { mediaURL, switchView, moveGallery, isVideoFile, authorInitial, keyError, taskTitle, playVisible, applySound, releasePlayback, preloadVideos, createCommentsPanel }; return; }
  const $ = id => document.getElementById(id);
  if (location.protocol === 'file:') { $('connect-form').hidden = true; $('local-preview').hidden = false; return; }
  const state = { token: localStorage.getItem('library_access_key') || '', muted:false, favoriteKind:'works', auth: null, view: 'library', items: [], total: 0, generation: 0, authorsGeneration: 0, navigation: 0, busy: false, cookieSyncGeneration:0 };
  const frequencies={once:'不订阅',three_days:'每3天更新',weekly:'每7天更新',monthly:'每月更新',quarterly:'每季度更新',yearly:'每年更新'};
  const frequencyValues={1440:'three_days',4320:'three_days',10080:'weekly',43200:'monthly',129600:'quarterly',525600:'yearly'};
  const statusNames = { queued: '已排队', paused:'已暂停', parsing: '正在解析', awaiting_confirmation: '等待画质确认', downloading: '正在下载', completed: '完整保存', failed: '失败', interrupted: '已中断', cancelled: '已取消' };
  const titles = { library: ['作品库', 'FOR YOU'], favorites:['收藏','SAVED WORKS'], tasks: ['下载', 'DOWNLOADS'], authors: ['订阅', 'CREATORS'], history: ['历史', 'HISTORY'], settings: ['设置', 'LIBRARY SETTINGS'] };
  function element(tag, className, text) { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = String(text); return node; }
  function notice(message, error = false) { $('notice').textContent = message; $('notice').className = `notice${error ? ' error' : ''}`; $('notice').hidden = !message; }
  function formatDate(value) {
    if (!value) return '暂无';
    const date = new Date(typeof value === 'number' ? (value < 1e12 ? value * 1000 : value) : value);
    return Number.isNaN(date.getTime()) ? '暂无' : date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
  }
  async function api(path, method = 'GET', body) {
    const token=state.token;
    let response;
    try { response = await fetch(path, { method, headers: { Authorization: `Bearer ${token}`, ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) }, body: body === undefined ? undefined : JSON.stringify(body), cache: 'no-store', signal: AbortSignal.timeout(60000) }); }
    catch (error) { throw new Error(error.name === 'TimeoutError' ? '请求超时，请刷新查看结果，避免重复提交。' : '无法连接网站，请检查 NAS 和网络连接。'); }
    let data; try { data = await response.json(); } catch { throw new Error('网站返回了无法识别的内容，请检查服务状态。'); }
    if (!response.ok) {
      if ((response.status === 401 || response.status === 403) && token===state.token) disconnect();
      throw new Error(typeof data.detail === 'string' ? data.detail : `请求失败（${response.status}），请检查输入或服务状态。`);
    }
    return data;
  }
  function empty(container, title, description) {
    const block = element('div', 'empty'); block.append(element('span', 'empty-symbol', '▧'), element('strong', '', title), element('p', '', description)); container.append(block);
  }
  function action(text, operation, primary = false) {
    const button = element('button', primary ? 'primary' : 'secondary', text); button.type = 'button';
    button.addEventListener('click', async () => { button.disabled = true; try { await operation(); } catch (error) { notice(error.message, true); } finally { button.disabled = false; } }); return button;
  }
  function disconnect() {
    state.authEpoch = (state.authEpoch || 0) + 1;
    state.comments?.close();
    state.cookieSyncGeneration=(state.cookieSyncGeneration||0)+1;
    const syncButton=$('cookie-sync-button'),syncLink=$('cookie-sync-link'),syncStatus=$('cookie-sync-status');if(syncButton)syncButton.disabled=false;if(syncLink)syncLink.hidden=true;if(syncStatus)syncStatus.hidden=true;
    switchView('library', document.querySelectorAll('video'));
    if (localStorage.getItem('library_access_key') === state.token) localStorage.removeItem('library_access_key');
    state.token = ''; state.generation++; state.navigation++;
    $('application').hidden = true; $('connection').hidden = false;
    $('access-key').value = ''; $('script-token').value = ''; $('cookie').value = ''; $('feed').replaceChildren(); $('history-list')?.replaceChildren();
    $('confirm-key').value = ''; $('legacy-key').value = ''; $('admin-key-form').reset();
    taskView.update([]);
    for (const id of ['task-detail', 'task-dialog-body']) $(id).replaceChildren();
    for (const dialog of document.querySelectorAll('dialog[open]')) dialog.close();
    connectionMode();
  }
  function connectionMode() {
    const setup = Boolean(state.auth?.setup_required);
    $('access-eyebrow').textContent = setup ? 'MAKE IT YOURS' : 'WELCOME BACK';
    $('access-title').textContent = setup ? '先设一把自己的密钥' : '进入抖音收藏库';
    $('access-description').textContent = setup ? '首次设置后，你就是这间媒体库的管理员。' : '登录抖音收藏库，继续上次没看完的喜欢。';
    $('access-key-label').textContent = setup ? '设置管理员密钥' : '管理员密钥';
    $('access-key').autocomplete = setup ? 'new-password' : 'current-password';
    $('access-key').minLength = setup ? 8 : 1;
    $('access-key').placeholder = setup ? '8–128 位英文、数字或符号' : '输入你设置的管理员密钥';
    $('confirm-key-field').hidden = !setup; $('confirm-key').required = setup;
    $('legacy-key-field').hidden = !(setup && state.auth.requires_current_key && !state.token);
    $('legacy-key').required = !$('legacy-key-field').hidden;
    $('access-hint').textContent = setup ? '用它登录和管理下载，以后可以在设置中修改。请记好密钥。' : '仅在自己的设备上登录。密钥会保存在当前浏览器。';
    $('connect-button').textContent = setup ? '设置密钥，进入媒体库 ↗' : '进入媒体库 ↗';
  }
  async function initializeConnection() {
    $('connect-button').disabled = true;
    try {
      state.auth = await api('/api/auth');
      if (state.auth.setup_required && state.token) {
        try { await api('/api/status'); } catch { /* An outdated saved key must not block the original-key field. */ }
      }
      connectionMode(); $('connect-message').textContent = '';
      if (!state.auth.setup_required && state.token) await connect();
    } catch (error) { $('connect-message').textContent = error.message; $('connect-button').textContent = '重新连接 ↗'; }
    finally { $('connect-button').disabled = false; }
  }
  async function connect(event) {
    event?.preventDefault();
    if (!state.auth) return initializeConnection();
    const button = $('connect-button'); button.disabled = true; $('connect-message').textContent = '正在连接…';
    try {
      if (event && state.auth.setup_required) {
        const key = $('access-key').value, message = keyError(key, $('confirm-key').value);
        if (message) throw new Error(message);
        if (!$('legacy-key-field').hidden) state.token = $('legacy-key').value;
        await api('/api/auth/setup', 'POST', { key });
        state.token = key; state.auth.setup_required = false; localStorage.setItem('library_access_key', key);
        $('confirm-key').value = ''; $('legacy-key').value = ''; connectionMode();
      } else if (event) state.token = $('access-key').value;
      const status = await api('/api/status'); localStorage.setItem('library_access_key', state.token);
      $('connection').hidden = true; $('application').hidden = false; $('access-key').value = ''; $('connect-message').textContent = '';
      showStatus(status); await navigate(location.hash.slice(1) || 'library');
    } catch (error) { $('connect-message').textContent = error.message; $('connection').hidden = false; $('application').hidden = true; }
    finally { button.disabled = false; }
  }
  function showStatus(status) {
    $('connection-state').textContent = status.core_ready ? '下载核心就绪' : '核心需要检查';
    if (!status.core_ready) notice(status.core_error || '下载核心尚未就绪，请检查容器日志。', true);
    const values = [['版本', status.version], ['下载核心', status.core_ready ? '就绪' : status.core_error || '未就绪'], ['Cookie', status.cookie_configured ? '已配置（不代表仍然有效）' : '尚未配置'], ['新媒体目录', status.media_root], ['已保存作品', status.stats?.works ?? 0]];
    $('system-status').replaceChildren();
    for (const [name, value] of values) $('system-status').append(element('dt', '', name), element('dd', '', value ?? '暂无'));
  }
  async function navigate(view) {
    state.comments?.close();
    const navigation = ++state.navigation;
    if(view==='favorites'&&state.view!==view){$('search-query').value='';$('search-author').value='';}
    state.view = switchView(view, document.querySelectorAll('video'));
    if($('favorite-toolbar'))$('favorite-toolbar').hidden=state.view!=='favorites';
    if($('feed'))$('feed').hidden=state.view==='favorites'&&state.favoriteKind==='authors';
    if($('favorite-authors'))$('favorite-authors').hidden=state.view!=='favorites'||state.favoriteKind!=='authors';
    $('application').dataset.view = state.view;
    state.generation++;
    for (const panel of document.querySelectorAll('.view')) panel.hidden = panel.id !== `view-${state.view==='favorites'?'library':state.view}`;
    for (const button of document.querySelectorAll('[data-view]')) { const active = button.dataset.view === state.view; button.classList.toggle('active', active); button.setAttribute('aria-current', active ? 'page' : 'false'); }
    $('page-title').textContent = titles[state.view][0]; $('page-eyebrow').textContent = titles[state.view][1];
    history.replaceState(null, '', `#${state.view}`); notice('');
    try {
      if (['library','favorites'].includes(state.view)) { $('library-label').textContent=state.view==='favorites'?'我的收藏':'随机推荐';$('shuffle-library').hidden=state.view==='favorites';await Promise.all([loadLibrary(true), loadLegacy()]); }
      else if (state.view === 'tasks') await loadTasks();
      else if (state.view === 'authors') await loadAuthors();
      else if (state.view === 'history') await loadBrowseHistory(true);
      else await loadSettings();
    } catch (error) { if (navigation === state.navigation) notice(error.message, true); }
  }
  let playbackFrame=0,warmTimer=0,activeCard=null,fullscreenCard=null,restoringFullscreen=false;
  async function fetchCommentBlob(path) {
    const token = state.token; let response;
    try { response = await fetch(path, { headers: { Authorization: `Bearer ${token}` }, cache: 'no-store', signal: AbortSignal.timeout(60000) }); }
    catch { throw new Error('无法连接网站，请检查 NAS 和网络连接。'); }
    if (!response.ok) { if ((response.status === 401 || response.status === 403) && token === state.token) disconnect(); throw new Error('无法读取本地评论文件，请重试。'); }
    return response.blob();
  }
  state.comments = createCommentsPanel({ document, element, api, binary: fetchCommentBlob, objectURL: globalThis.URL, formatDate });
  function syncPlayback(){
    playbackFrame=0;if(restoringFullscreen)return;const feed=$('feed'),cards=[...feed.querySelectorAll('.work')];
    const index=Math.max(0,Math.min(cards.length-1,Math.round(feed.scrollTop/feed.clientHeight)));
    const enabled=!document.hidden&&!feed.hidden&&['library','favorites'].includes(state.view);
    const card=!feed.hidden&&['library','favorites'].includes(state.view)?(document.fullscreenElement?.closest('.work')||cards[index]):null;
    const selected=card?cards.indexOf(card):-1;
    if(card!==activeCard){if(activeCard)state.comments?.close();clearTimeout(warmTimer);activeCard=card;}
    cards.forEach((item,i)=>{const v=item.querySelector('video');if(!v)return;
      if(i!==selected)v.autoAdvancePending=false;
      if(!enabled){v.playbackWarm=false;v.cancelPreparation?.();playVisible(v,false);}
      else if(i<selected-1||i>selected+2)releasePlayback(v);
      else {v.playbackWarm=v.playbackWarm&&i>selected;if(i!==selected||!v.playbackActive||!v.playbackReady)playVisible(v,i===selected);if(i===selected&&v.autoAdvancePending)v.advancePlayback?.();}
    });
    clearTimeout(warmTimer);
    if(card&&(!card.querySelector('video')||card.querySelector('video').playbackReady))warmTimer=setTimeout(()=>{
      if(activeCard!==card||document.hidden)return;
      for(const next of preloadVideos(cards,selected))if(!next.playbackReady){next.playbackWarm=true;next.preparePlayback?.();}
    },200);
  }
  function schedulePlayback(){if(!playbackFrame)playbackFrame=requestAnimationFrame(syncPlayback);}
  const feedObserver=new IntersectionObserver(schedulePlayback,{root:$('feed'),threshold:[.5]});
  document.addEventListener('fullscreenchange',()=>{
    if(document.fullscreenElement){schedulePlayback();return;}
    if(!fullscreenCard)return;
    const card=fullscreenCard;fullscreenCard=null;restoringFullscreen=true;
    const feed=$('feed');feed.style.scrollSnapType='none';
    requestAnimationFrame(()=>{if(card.isConnected)feed.scrollTop=[...feed.children].indexOf(card)*feed.clientHeight;
      requestAnimationFrame(()=>{feed.style.scrollSnapType='';restoringFullscreen=false;schedulePlayback();});});
  });
  function renderWork(work, position) {
    const card = element('article', 'work'); card.dataset.work = String(work.id);
    const files = Array.isArray(work.files) ? work.files : [];
    if (!files.length) { empty(card, '媒体文件不可用', '请到下载任务查看原因并重试。'); return card; }
    const gallery = !['视频', 'video'].includes(work.type) && (files.length > 1 || !isVideoFile(files[0]));
    let media,advancing=false;
    function currentVideo(video){
      const feed=$('feed'),cards=[...feed.querySelectorAll('.work')];
      const visible=document.fullscreenElement?.closest('.work')||cards[Math.round(feed.scrollTop/feed.clientHeight)];
      return card.isConnected&&media===video&&activeCard===card&&visible===card&&!document.hidden&&!feed.hidden&&['library','favorites'].includes(state.view)&&video.playbackActive&&!video.userPaused&&video.ended&&!state.comments?.isOpenFor(card);
    }
    async function nextWork(video=null){
      if(video&&(advancing||!currentVideo(video))){if(!advancing)video.autoAdvancePending=false;return;}
      if(video&&!card.nextElementSibling&&state.loadingLibrary)return;
      if(video)advancing=true;
      try{
        if(!card.nextElementSibling&&state.hasMore)await loadLibrary();
        if(video&&!currentVideo(video))return;
        const next=card.nextElementSibling;
        if(video&&next&&document.fullscreenElement?.closest('.work')===card){const previous=fullscreenCard;fullscreenCard=null;try{await document.exitFullscreen();}catch(error){if(document.fullscreenElement?.closest('.work')===card)fullscreenCard=previous;throw error;}if(!currentVideo(video))return;}
        next?.scrollIntoView({behavior:'smooth',block:'start'});
      }finally{if(video){video.autoAdvancePending=false;advancing=false;}}
    }
    function showMedia(index) {
      if(media?.tagName==='VIDEO')releasePlayback(media);media?.remove();
      card.querySelector('.player-controls')?.remove();
      card.querySelector('.playback-state')?.remove();
      media = element(isVideoFile(files[index]) ? 'video' : 'img', 'work-media');
      if (media.tagName === 'VIDEO') {
        media.controls = false; media.playsInline = true; media.preload = 'none'; media.loop = gallery || work.type==='实况';media.muted=state.muted;
        const current=media, message=element('div','playback-state');message.setAttribute('role','status');
        let checking=false,poll=0,started=0;
        current.cancelPreparation=()=>{clearTimeout(poll);message.remove();};
        const controls=element('div','player-controls'),toggle=element('button','','▶'),clock=element('span','player-time','0:00 / 0:00'),seek=element('input','player-seek'),fullscreen=element('button','','⛶');
        seek.type='range';seek.min='0';seek.max='100';seek.step='.1';seek.value='0';seek.setAttribute('aria-label','播放进度');
        toggle.type=fullscreen.type='button';toggle.setAttribute('aria-label','播放');fullscreen.setAttribute('aria-label','全屏播放');
        const time=value=>`${Math.floor(value/60)}:${String(Math.floor(value%60)).padStart(2,'0')}`;
        function updateControls(){const duration=Number.isFinite(current.duration)?current.duration:0;clock.textContent=`${time(current.currentTime)} / ${time(duration)}`;seek.disabled=!duration;seek.value=duration?String(current.currentTime/duration*100):'0';toggle.textContent=current.paused?'▶':'Ⅱ';toggle.setAttribute('aria-label',current.paused?'播放':'暂停');}
        toggle.addEventListener('click',()=>{if(current.paused){current.userPaused=false;current.playbackActive=false;playVisible(current,true);}else {current.userPaused=true;current.pause();}});
        current.addEventListener('click',()=>toggle.click());
        seek.addEventListener('input',()=>{if(Number.isFinite(current.duration))current.currentTime=current.duration*Number(seek.value)/100;});
        fullscreen.addEventListener('click',()=>{if(!document.fullscreenElement)fullscreenCard=card;(document.fullscreenElement?document.exitFullscreen():card.requestFullscreen())?.catch(()=>{fullscreenCard=null;});});
        for(const event of ['timeupdate','durationchange','play','pause'])current.addEventListener(event,updateControls);
        controls.append(toggle,clock,seek,fullscreen);card.append(controls);
        current.onAutoplayBlocked=()=>{message.textContent='点击画面播放';card.append(message);};
        message.addEventListener('click',()=>{if(!message.querySelector('button')){message.remove();current.userPaused=false;current.playbackActive=false;playVisible(current,true);}});
        function failed(text){
          if(!current.isConnected)return;
          message.replaceChildren(element('span','',text),action('重试播放',async()=>{message.replaceChildren();current.playbackReady=false;await current.preparePlayback(true);}));card.append(message);
        }
        current.preparePlayback=async(retry=false)=>{
          if(checking||(!current.playbackActive&&!current.playbackWarm)||!current.isConnected)return;
          clearTimeout(poll);if(!started)started=performance.now();
          checking=true;
          try{
            const query=new URLSearchParams();if(current.forceCompatibility)query.set('force','true');if(retry)query.set('retry','true');
            if(current.playbackWarm&&!current.playbackActive)query.set('warm','true');
            const result=await api(`${files[index].url}/playback?${query}`);
            if(!current.isConnected||current!==media||(!current.playbackActive&&!current.playbackWarm))return;
            if(result.status==='ready'){
              current.dataset.playbackMode=result.mode;current.playbackStreaming=Boolean(result.streaming);current.playbackReady=true;message.remove();
              if(current.playbackActive){current.src=mediaURL(result.url,state.token,location.origin);playVisible(current,true);schedulePlayback();}
              else if(!result.streaming){current.src=mediaURL(result.url,state.token,location.origin);current.preload='auto';}
              else {current.playbackReady=false;poll=setTimeout(()=>current.preparePlayback(),800);}
            }else if(result.status==='failed')failed(result.message);
            else{
              if(current.playbackActive&&performance.now()-started>1200){message.textContent=result.status==='waiting'?'缓冲中…':`缓冲中${result.progress>0?` · ${Math.floor(result.progress)}%`:'…'}`;card.append(message);}
              poll=setTimeout(()=>current.preparePlayback(),800);
            }
          }catch(error){failed(error.message);}finally{checking=false;}
        };
        function fallback(){
          if(!current.forceCompatibility){current.forceCompatibility=true;current.playbackReady=false;current.removeAttribute('src');current.load();current.preparePlayback();}
          else failed('这个视频暂时无法播放，原文件已保留。');
        }
        current.addEventListener('error',fallback);
        current.addEventListener('loadeddata',()=>{if(!current.videoWidth)fallback();});
        current.addEventListener('playing',()=>{message.remove();schedulePlayback();});
        current.advancePlayback=()=>{if(current.autoAdvancePending)nextWork(current).catch(error=>notice(error.message,true));};
        current.addEventListener('ended',()=>{if(!current.loop&&!state.comments?.isOpenFor(card)){current.autoAdvancePending=true;current.advancePlayback();}});
      } else { media.alt = work.description || '作品图集'; media.loading = position === 0 || index > 0 ? 'eager' : 'lazy'; }
      if(media.tagName!=='VIDEO'){
        media.addEventListener('error', () => { notice('图片无法打开，请检查原文件是否仍存在。', true); });
        try { media.src = mediaURL(files[index].url, state.token, location.origin); } catch (error) { notice(error.message, true); }
      }
      card.prepend(media);
      if(media.tagName==='VIDEO'&&['library','favorites'].includes(state.view)){const r=card.getBoundingClientRect(),f=$('feed').getBoundingClientRect();if(r.height&&r.top<f.bottom&&r.bottom>f.top&&Math.min(r.bottom,f.bottom)-Math.max(r.top,f.top)>.6*r.height)playVisible(media,!document.hidden);}
    }
    showMedia(0);
    const count = element('span', 'work-index', gallery ? `1 / ${files.length}` : work.type === '实况' ? '实况' : '视频'); card.append(count);
    if (gallery && files.length > 1) {
      let index = 0;
      const previous = element('button', 'gallery-arrow prev', '‹'); previous.setAttribute('aria-label', '上一张');
      const next = element('button', 'gallery-arrow next', '›'); next.setAttribute('aria-label', '下一张');
      function move(change) {
        index = moveGallery(index, change, files.length);
        showMedia(index);
        count.textContent = `${index + 1} / ${files.length}`; previous.disabled = index === 0; next.disabled = index === files.length - 1;
      }
      previous.addEventListener('click', () => move(-1)); next.addEventListener('click', () => move(1)); previous.disabled = true;
      card.addEventListener('keydown', event => { if(event.target.tagName==='INPUT')return;if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') { event.preventDefault(); move(event.key === 'ArrowLeft' ? -1 : 1); } });
      card.tabIndex = 0; card.append(previous, next);
    }
    const info = element('div', 'work-info'); const author = element('button', '', `@${work.author || '未知作者'}`);
    author.addEventListener('click', () => { const value = String(work.author_id || work.author || ''); if (![...$('search-author').options].some(option => option.value === value)) $('search-author').append(new Option(work.author || value, value)); $('search-query').value='';$('search-author').value = value;(state.view==='library'?loadLibrary(true):navigate('library')).catch(error => notice(error.message, true)); });
    info.append(author, element('p', '', work.description || '这条作品没有文字描述'), element('small', '', `${work.source === 'legacy' ? '原有收藏 · ' : ''}${work.type || '作品'} · ${formatDate(work.created_at)} · 抖音收藏库`)); card.append(info);
    const tools = element('div', 'work-tools');
    const avatar = action(authorInitial(work.author), () => author.click()); avatar.className = 'author-avatar'; avatar.setAttribute('aria-label', `查看${work.author || '作者'}的作品`);
    const original = element('a', 'work-tool', '↗'); original.href = `https://www.douyin.com/${work.type === '视频' ? 'video' : 'note'}/${work.id}`; original.target = '_blank'; original.rel = 'noopener noreferrer'; original.setAttribute('aria-label', '打开抖音原作'); original.title = '原作';
    original.hidden = work.source === 'legacy';
    const up = action('⌃', () => card.previousElementSibling?.scrollIntoView({ behavior: 'smooth', block: 'start' })); up.className = 'work-tool'; up.setAttribute('aria-label', '上一条作品');
    const down = action('⌄', () => nextWork()); down.className = 'work-tool'; down.setAttribute('aria-label', '下一条作品');
    const comment = action('☰', () => state.comments.open(work, card, comment)); comment.className = 'work-tool'; comment.setAttribute('aria-label', '查看离线评论'); comment.setAttribute('aria-expanded', 'false'); comment.title = '评论';
    const favorite=action('',async()=>{const value=!work.favorite, generation=state.generation, loading=state.loadingLibrary;await api(`/api/favorites/${encodeURIComponent(work.id)}`,'PUT',{favorite:value});work.favorite=value;paintFavorite();if(state.view==='favorites'&&!value){if(state.comments?.isOpenFor(card))state.comments.close();card.querySelector('video')?.pause();feedObserver.unobserve(card);card.remove();state.items=state.items.filter(w=>w.id!==work.id);state.total--;state.nextOffset=Math.max(0,state.nextOffset-1);if(loading||state.loadingLibrary||generation!==state.generation||!state.items.length)await loadLibrary(true);}});favorite.className='work-tool favorite-tool';
    function paintFavorite(){favorite.textContent=work.favorite?'♥':'♡';favorite.setAttribute('aria-label',work.favorite?'取消收藏作品':'收藏作品');favorite.setAttribute('aria-pressed',String(Boolean(work.favorite)));}paintFavorite();
    tools.append(avatar, favorite, comment, original, up, down); card.append(tools);
    feedObserver.observe(card); return card;
  }
  async function loadLibrary(reset = false) {
    if(state.view==='favorites'&&state.favoriteKind==='authors')return loadAuthors(true);
    if(state.loadingLibrary&&!reset)return;
    state.loadingLibrary = true;
    const generation = ++state.generation;
    const offset = reset ? 0 : state.nextOffset;
    if(reset){state.seed=crypto.getRandomValues(new Uint32Array(1))[0];state.ceiling=null;}
    const query = new URLSearchParams({ q: $('search-query').value.trim(), author: $('search-author').value, offset: String(offset), limit: '20' });
    if(state.view==='favorites')query.set('favorite','true');
    else {query.set('seed',state.seed);if(state.ceiling!==null)query.set('ceiling',state.ceiling);}
    try {
      const data = await api(`/api/library?${query}`); if (generation !== state.generation || !['library','favorites'].includes(state.view)) return;
      if (reset) { state.comments?.close();for(const v of $('feed').querySelectorAll('video'))releasePlayback(v);activeCard=null;feedObserver.disconnect(); $('feed').replaceChildren(); $('feed').scrollTop = 0; state.items = []; }
      state.items.push(...data.items); state.total = data.total;state.nextOffset=data.next_offset;state.ceiling=data.ceiling;state.hasMore=data.has_more;
      for (const [index, work] of data.items.entries()) {
        $('feed').append(renderWork(work, offset + index));
        const value = String(work.author_id || work.author || '');
        if (value && ![...$('search-author').options].some(option => option.value === value)) $('search-author').append(new Option(work.author || value, value));
      }
      if (!state.items.length) empty($('feed'), state.view==='favorites'?'还没有收藏作品':$('search-query').value || $('search-author').value ? '没有找到这些作品' : '把喜欢的内容收进来', state.view==='favorites'?'在作品右侧点爱心，喜欢的内容会保存在这里。':'添加一条作品链接，或从抖音网页发送。');
      const author=$('search-author').value;
      $('library-label').textContent=author?`@${$('search-author').selectedOptions[0].textContent.replace(/^@+/,'')}`:state.view==='favorites'?'我的收藏':$('search-query').value.trim()?'搜索结果':'随机推荐';
      $('back-recommendations').hidden=!author;$('shuffle-library').hidden=Boolean(author)||state.view==='favorites';
      $('clear-search').hidden = !$('search-query').value && !$('search-author').value;
    } finally { if(generation===state.generation){state.loadingLibrary = false;schedulePlayback();} }
  }
  function taskCard(task) {
    const card = element('article', 'record'); const content = element('div', 'record-content'); const heading = element('div', 'record-title');
    const stateClass = task.status === 'completed' ? 'good' : ['failed', 'interrupted'].includes(task.status) ? 'bad' : 'pending';
    heading.append(element('h2', '', taskTitle(task)), element('span', `badge ${stateClass}`, statusNames[task.status] || task.status));
    const source=element('a','source-link',task.kind==='author'?'查看博主主页 ↗':'查看作品链接 ↗');source.href=task.link.startsWith('https://')?task.link:`https://www.douyin.com/video/${task.link}`;source.target='_blank';source.rel='noopener noreferrer';
    content.append(heading,source,element('p', '', task.message || '等待处理'));
    if (Number(task.total) > 0) {
      const bar = element('div', 'progress'); const fill = element('span'); fill.style.width = `${Math.min(100, Math.max(0, Number(task.completed) / Number(task.total) * 100))}%`; bar.append(fill);
      content.append(bar, element('p', 'muted', `已完整保存 ${task.completed} / ${task.total} 条`));
    }
    content.append(element('p', 'hint', `${task.kind==='author'?'博主全部作品':'单个作品'} · 更新于 ${formatDate(task.updated_at || task.created_at)} · 任务 ${task.id}`));
    const actions = element('div', 'record-actions');
    if (['failed', 'interrupted', 'cancelled'].includes(task.status)) actions.append(action('重试', async () => { await api(`/api/tasks/${encodeURIComponent(task.id)}/retry`, 'POST'); await loadTasks(); }));
    if (task.status === 'awaiting_confirmation') {
      content.append(element('p', 'hint', '目标画质或无水印尚不能确认。接受仅适用于此任务，也可取消。'));
      if (Array.isArray(task.preview)) {
        for (const item of task.preview.slice(0, 5)) {
          const dimensions = item.width > 0 && item.height > 0 ? `${item.width} × ${item.height}` : '分辨率未确认';
          content.append(element('p', 'muted', `${item.nickname || '未知作者'} · ${item.desc || item.id} · ${dimensions}`));
        }
      }
      actions.append(action('接受此任务原项目优选', async () => { await api(`/api/tasks/${encodeURIComponent(task.id)}/confirm`, 'POST'); await loadTasks(); }, true));
    }
    if (!['completed','cancelled'].includes(task.status)) actions.append(action('取消', async () => { await api(`/api/tasks/${encodeURIComponent(task.id)}/cancel`, 'POST'); await loadTasks(); }));
    actions.append(action('删除',async()=>{await api(`/api/tasks/${encodeURIComponent(task.id)}`,'DELETE');await loadTasks();notice('任务记录已删除，媒体文件保留。');}));
    card.append(content, actions); return card;
  }
  const taskView = window.CloudriftTaskView.create({ application: $('application'), makeTaskCard: taskCard, title: taskTitle, onBatch:async(action,ids)=>{
    try{let changed=0,skipped=0;for(let i=0;i<ids.length;i+=1000){const result=await api('/api/tasks/batch','POST',{action,ids:ids.slice(i,i+1000)});changed+=result.changed.length;skipped+=result.skipped.length;}await loadTasks();notice(`已处理 ${changed} 个任务${skipped?`，${skipped} 个因状态变化或同链接正在处理而跳过`:''}。媒体文件保留。`);}catch(error){notice(error.message,true);}
  } });
  async function loadBrowseHistory(reset = false) {
    if (!reset && state.historyBusy) return;
    if (reset) {
      state.historySerial = (state.historySerial || 0) + 1;
      state.historyCursor = null; state.historyDay = ''; state.historyBusy = false;
      $('history-list').replaceChildren(); $('history-count').textContent = '';
    }
    const serial = state.historySerial, navigation = state.navigation;
    const current = () => serial === state.historySerial && navigation === state.navigation && state.view === 'history';
    state.historyBusy = true; $('history-more').hidden = true; $('history-retry').hidden = true;
    $('history-status').textContent = '正在读取浏览历史…';
    try {
      const path = '/api/browse-history?limit=50' + (state.historyCursor ? `&before=${encodeURIComponent(state.historyCursor)}` : '');
      const result = await api(path);
      if (!current()) return;
      $('history-count').textContent = `共 ${Number(result.total).toLocaleString('zh-CN')} 条浏览记录`;
      for (const item of result.items) {
        const date = new Date(item.viewed_at), day = date.toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric' });
        if (day !== state.historyDay) { $('history-list').append(element('h2', 'history-day', day)); state.historyDay = day; }
        const card = element('article', 'record history-record'), content = element('div', 'record-content'), actions = element('div', 'record-actions');
        content.append(element('h2', '', item.title || '抖音作品'), element('p', 'muted', `${date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })} · ${item.author_name || '未知作者'}`));
        if (/^https:\/\/www\.douyin\.com\/(video|note)\/\d{19}$/.test(item.url)) {
          const link = element('a', 'secondary', '查看原作品 ↗'); link.href = item.url; link.target = '_blank'; link.rel = 'noopener noreferrer'; actions.append(link);
        }
        card.append(content, actions); $('history-list').append(card);
      }
      state.historyCursor = result.next_cursor;
      if (reset && !result.items.length) empty($('history-list'), '还没有浏览历史', '安装并连接抖音收藏库助手后，浏览的作品会自动记录；旧抖音历史不会自动补齐。');
      $('history-status').textContent = ''; $('history-more').hidden = !state.historyCursor;
    } catch (error) {
      if (current()) { $('history-status').textContent = error.message || '读取失败，请重试。'; $('history-retry').hidden = false; }
    } finally { if (serial === state.historySerial) state.historyBusy = false; }
  }
  async function loadTasks() {
    const navigation = state.navigation, tasks = await api('/api/tasks');
    if (navigation === state.navigation && state.view === 'tasks') taskView.update(tasks);
  }
  async function loadAuthors(favoritesOnly=false) {
    const generation = ++state.authorsGeneration, navigation = state.navigation, authors = await api('/api/authors/history');
    if (generation !== state.authorsGeneration || navigation !== state.navigation || state.view !== (favoritesOnly?'favorites':'authors')) return;
    const container=$(favoritesOnly?'favorite-author-list':'author-list');container.replaceChildren();
    const order=$('author-sort').value,visible=authors.filter(a=>!favoritesOnly||a.favorite);
    visible.sort((a,b)=>Number(Boolean(b.starred))-Number(Boolean(a.starred))||(order==='name'?(a.name||'').localeCompare(b.name||'','zh-CN'):order==='next'?(a.enabled?a.next_check:Infinity)-(b.enabled?b.next_check:Infinity):order==='last'?new Date(typeof b.last_checked==='number'?b.last_checked*1000:b.last_checked||0)-new Date(typeof a.last_checked==='number'?a.last_checked*1000:a.last_checked||0):Number(b.enabled)-Number(a.enabled)));
    container.append(element('h2','creator-heading',favoritesOnly?'收藏博主':`订阅中 · ${authors.filter(a=>a.enabled).length}　历史博主 · ${authors.filter(a=>!a.enabled).length}`));
    async function updateAuthor(author,values){let saved=author;if(!author.registered)saved=await api('/api/authors','POST',{url:author.url,name:author.name,frequency:'once'});await api(`/api/authors/${encodeURIComponent(saved.id)}`,'PATCH',values);await loadAuthors(favoritesOnly);}
    for (const author of visible) {
      const card = element('article', 'record'); const content = element('div', 'record-content'); const heading = element('div', 'record-title');
      const current = author.enabled ? (frequencyValues[author.interval_minutes] || 'quarterly') : 'once';
      const names = frequencies;
      heading.append(element('h2', '', author.name || '未命名博主'), element('span', `badge ${author.enabled ? 'good' : ''}`, names[current]));
      const link=element('a','source-link','查看博主主页 ↗');link.href=author.url;link.target='_blank';link.rel='noopener noreferrer';
      content.append(heading,link,element('p', 'muted', `上次下载／检查 ${formatDate(author.last_checked)}`), element('p', 'hint', author.enabled ? `下次更新 ${formatDate(author.next_check)}` : '已留在历史里，不会自动下载。'));
      if (author.last_error) content.append(element('p', 'error', author.last_error));
      const actions = element('div', 'record-actions');
      const star=action(author.starred?'★ 星标':'☆ 星标',()=>updateAuthor(author,{starred:!author.starred}));star.classList.toggle('is-starred',Boolean(author.starred));star.setAttribute('aria-pressed',String(Boolean(author.starred)));
      const favorite=action(author.favorite?'♥ 已收藏':'♡ 收藏博主',()=>updateAuthor(author,{favorite:!author.favorite}));favorite.setAttribute('aria-pressed',String(Boolean(author.favorite)));favorite.classList.toggle('is-favorite',Boolean(author.favorite));
      actions.append(star,favorite,action('查看已存作品',async()=>{await navigate('library');const select=$('search-author');if(![...select.options].some(o=>o.value===author.name))select.append(new Option(author.name,author.name));select.value=author.name;$('search-query').value='';await loadLibrary(true);}));
      const frequency = element('select'); frequency.setAttribute('aria-label', `${author.name || '此博主'}的后续更新`);
      for (const [value, label] of Object.entries(names)) if (value !== 'legacy' || current === 'legacy') frequency.append(new Option(label, value));
      frequency.value = current;
      actions.append(frequency, action('保存方式', async () => { if (frequency.value === 'legacy') return; if(author.registered)await api(`/api/authors/${encodeURIComponent(author.id)}`, 'PATCH', {frequency:frequency.value});else await api('/api/authors','POST',{url:author.url,name:author.name,frequency:frequency.value}); await loadAuthors(favoritesOnly); notice('后续更新方式已保存。'); }), action('现在补齐作品', async () => { if(author.registered)await api(`/api/authors/${encodeURIComponent(author.id)}/check`, 'POST');else await api('/api/tasks','POST',{link:author.url,kind:'author'}); await navigate('tasks'); notice('博主下载已提交，已有作品自动跳过。'); }));
      card.append(content, actions); container.append(card);
    }
    if(!visible.length)empty(container,favoritesOnly?'还没有收藏博主':'还没有历史博主','在订阅页点“收藏博主”，或添加博主页。收藏不会自动开启下载。');
  }
  async function loadSettings() {
    const navigation = state.navigation;
    await Promise.all([
      api('/api/settings').then(settings => {
        if (navigation !== state.navigation || state.view !== 'settings') return;
        $('cookie-state').textContent = settings.cookie_configured ? '已配置' : '未配置'; $('cookie-state').className = `badge ${settings.cookie_configured ? 'good' : ''}`;
        $('cookie').value = ''; $('clear-cookie').checked = false; $('quality-policy').value = settings.quality_policy || 'upstream';
        $('download-dir').value = settings.download_dir; $('download-dir-hint').textContent = `可用范围：${settings.media_root} 及其子目录。修改不搬迁已有文件；更换 NAS 磁盘请修改 Compose 挂载。`;
        $('script-server').value = location.origin; $('script-token').value = settings.script_token || state.token; $('script-token').type = 'password'; $('reveal-token').textContent = '显示';
      }),
      api('/api/status').then(status => { if (navigation === state.navigation && state.view === 'settings') showStatus(status); }),
      loadLegacy()
    ]);
  }
  async function loadLegacy() {
    const navigation = state.navigation, scan = await api('/api/legacy');
    if (navigation !== state.navigation) return;
    $('legacy-panel').hidden = !scan.configured;
    if (!scan.configured) return;
    const counts = `${Number(scan.scanned_files || 0).toLocaleString()} 个文件 · ${Number(scan.indexed_works || 0).toLocaleString()} 个作品`;
    const names = {idle:'等待扫描',scanning:'正在建立旧库目录',completed:'旧库目录已更新',interrupted:'扫描已中断，可继续',failed:'扫描遇到问题'};
    $('legacy-state').textContent = `${names[scan.status] || scan.status}：${counts}${scan.error ? '。'+scan.error : ''}`;
    $('scan-legacy').disabled = scan.status === 'scanning';
    if (state.view === 'library' && !state.items.length && scan.indexed_works > 0 && !state.loadingLibrary && !$('search-query').value && !$('search-author').value) await loadLibrary(true);
  }
  $('scan-legacy').addEventListener('click', async () => {
    $('scan-legacy').disabled = true;
    try { await api('/api/legacy/scan', 'POST'); await loadLegacy(); notice('正在后台更新旧库目录，原文件保持原样。'); }
    catch (error) { notice(error.message, true); $('scan-legacy').disabled = false; }
  });
  $('connect-form').addEventListener('submit', connect); $('disconnect').addEventListener('click', disconnect);
  for (const button of document.querySelectorAll('[data-view]')) button.addEventListener('click', () => navigate(button.dataset.view));
  document.querySelector('.brand').addEventListener('click', event => { event.preventDefault(); navigate('library'); });
  $('search-form').addEventListener('submit', event => { event.preventDefault(); loadLibrary(true).catch(error => notice(error.message, true)); });
  $('clear-search').addEventListener('click', () => { $('search-query').value = ''; $('search-author').value = ''; loadLibrary(true).catch(error => notice(error.message, true)); });
  $('shuffle-library').addEventListener('click',()=>{$('search-query').value='';$('search-author').value='';loadLibrary(true).catch(error=>notice(error.message,true));});
  $('back-recommendations').addEventListener('click',()=>{$('search-query').value='';$('search-author').value='';loadLibrary(true).catch(error=>notice(error.message,true));});
  function showCookieSync(message,error=false){const status=$('cookie-sync-status');$('cookie-sync-message').textContent=message;status.className=`notice${error?' error':''}`;status.hidden=!message;}
  async function syncBrowserCookie(){
    const button=$('cookie-sync-button'),link=$('cookie-sync-link');if(button.disabled)return;
    const token=state.token,run=++state.cookieSyncGeneration,current=()=>Boolean(token)&&state.token===token&&state.cookieSyncGeneration===run;
    if(!token){showCookieSync('请先连接收藏库。',true);return;}
    button.disabled=true;link.hidden=true;link.removeAttribute('href');let tab=null,opened=false,returned=false;
    const returnToLibrary=()=>{link.hidden=true;try{tab?.close();}catch{}if(!returned){returned=true;try{window.focus();}catch{}}};
    try{
      try{tab=window.open('about:blank','_blank');if(tab)tab.opener=null;}catch{try{tab?.close();}catch{}tab=null;}
      showCookieSync('正在准备同步…');
      const created=await api('/api/cookie-sync','POST');
      if(!current()){try{tab?.close();}catch{}return;}
      if(typeof created.id!=='string'||!/^[A-Za-z0-9_-]{43}$/.test(created.id))throw new Error('invalid request');
      const messages={waiting:'正在读取抖音登录信息…',validating:'正在同步…',waiting_apply:'等待当前下载结束后保存…',applying:'正在保存…'};
      const needsOpen=created.status==='waiting';
      if(needsOpen){
        const url=`https://www.douyin.com/jingxuan#library-cookie-sync=${created.id}`;
        try{if(tab&&!tab.closed){tab.location.href=url;opened=true;}}catch{}
        if(!opened){try{tab?.close();}catch{}link.href=url;link.hidden=false;}
        showCookieSync(opened?messages.waiting:'浏览器阻止了弹窗，请点击链接继续。');
      }else{try{tab?.close();}catch{}showCookieSync(messages[created.status]||'同步任务正在进行…');}
      const deadline=Date.now()+35*60*1000;
      const failures={permission_denied:'未获得 Cookie 读取权限，请在脚本猫中允许后重试。',not_logged_in:'请先在当前浏览器登录抖音，再重试。',read_failed:'无法读取 Cookie，请确认已安装助手并允许读取。',invalid_cookie:'读取的 Cookie 不完整，请先登录抖音再试。',verify_failed:'无法验证 Cookie，请检查抖音登录状态及网络后重试。',expired:'等待超时，请重新同步。',changed:'Cookie 或管理员密钥已变更，请重新同步。',apply_failed:'保存失败，原 Cookie 保留；请检查下载服务后重试。',rollback_failed:'更新未完成，原设置或下载核心需要检查。'};
      while(current()&&Date.now()<deadline){
        await new Promise(resolve=>setTimeout(resolve,1200));if(!current())return;
        const result=await api(`/api/cookie-sync/${created.id}`);if(!current())return;
        if(result.status!=='waiting')returnToLibrary();
        if(result.status==='waiting'&&opened&&tab?.closed){returnToLibrary();showCookieSync('同步未完成，请确认助手已连接此收藏库并已允许读取抖音 Cookie。',true);return;}
        if(result.status==='success'){$('cookie-state').textContent='已配置';$('cookie-state').className='badge good';showCookieSync('已同步。');return;}
        if(result.status==='failed'){showCookieSync(failures[result.reason]||'同步未完成，请检查抖音登录和助手权限后重试。',true);return;}
        if(!messages[result.status])throw new Error('invalid status');
        showCookieSync(result.status==='waiting'&&needsOpen&&!opened?'浏览器阻止了弹窗，请点击链接继续。':messages[result.status]);
      }
      if(current()){returnToLibrary();showCookieSync('同步等待超时，请重试。',true);}
    }catch{returnToLibrary();if(current())showCookieSync('无法完成同步，请检查网站连接和助手设置后重试。',true);}
    finally{if(state.cookieSyncGeneration===run){button.disabled=false;link.hidden=true;}}
  }
  $('cookie-sync-button').addEventListener('click',syncBrowserCookie);
  function paintSound(){const button=$('sound-toggle');button.textContent=state.muted?'声音：关':'声音：开';button.setAttribute('aria-pressed',String(!state.muted));button.title=state.muted?'打开所有视频声音':'关闭所有视频声音';}
  paintSound();
  $('sound-toggle').addEventListener('click',()=>{state.muted=!state.muted;applySound($('feed').querySelectorAll('video'),state.muted);paintSound();const v=activeCard?.querySelector('video');if(v){v.playbackActive=false;playVisible(v,true);}});
  $('refresh-tasks').addEventListener('click', () => loadTasks().catch(error => notice(error.message, true)));
  $('history-more').addEventListener('click', () => loadBrowseHistory());
  $('history-retry').addEventListener('click', () => loadBrowseHistory(!state.historyCursor));
  $('author-sort').addEventListener('change',()=>loadAuthors().catch(error=>notice(error.message,true)));
  for(const button of document.querySelectorAll('[data-favorite-kind]'))button.addEventListener('click',async()=>{
    state.generation++;
    state.favoriteKind=button.dataset.favoriteKind;
    for(const tab of document.querySelectorAll('[data-favorite-kind]'))tab.setAttribute('aria-pressed',String(tab===button));
    for(const video of $('feed').querySelectorAll('video'))playVisible(video,false);
    $('feed').hidden=state.favoriteKind==='authors';$('favorite-authors').hidden=state.favoriteKind!=='authors';
    try{await loadLibrary(true);}catch(error){notice(error.message,true);}
  });
  $('favorite-author-form').addEventListener('submit',async event=>{
    event.preventDefault();const button=event.target.querySelector('button');button.disabled=true;
    try{const url=$('favorite-author-url').value.trim();await api('/api/authors','POST',{url,favorite:true});event.target.reset();await loadAuthors(true);notice('博主已收藏，原订阅设置保留。');}catch(error){notice(error.message,true);}finally{button.disabled=false;}
  });
  $('open-submit').addEventListener('click', () => { $('submit-message').textContent = ''; $('submit-dialog').showModal(); });
  $('close-submit').addEventListener('click', () => $('submit-dialog').close());
  $('task-form').addEventListener('submit', async event => {
    event.preventDefault(); const button = event.target.querySelector('[type="submit"]'); button.disabled = true; $('submit-message').textContent = '正在提交…';
    try { const task = await api('/api/tasks', 'POST', { link: $('task-link').value.trim() }); event.target.reset(); $('submit-dialog').close(); await navigate('tasks'); notice(`网站已收到任务 #${task.id}，下载后到作品库观看。`); }
    catch (error) { $('submit-message').textContent = error.message; } finally { button.disabled = false; }
  });
  $('author-form').addEventListener('submit', async event => {
    event.preventDefault(); const button = event.target.querySelector('[type="submit"]'); button.disabled = true;
    try { const author = await api('/api/authors', 'POST', { url: $('author-url').value.trim(), name: $('author-name').value.trim(), frequency:$('author-frequency').value }); await api(`/api/authors/${encodeURIComponent(author.id)}/check`, 'POST'); event.target.reset(); await navigate('tasks'); notice('博主下载已提交，后续更新按你选择的方式执行。'); }
    catch (error) { notice(error.message, true); } finally { button.disabled = false; }
  });
  $('settings-form').addEventListener('submit', async event => {
    event.preventDefault(); const button = event.target.querySelector('[type="submit"]'); button.disabled = true;
    const body = { quality_policy: $('quality-policy').value, download_dir: $('download-dir').value.trim() }; if ($('clear-cookie').checked) body.cookie = ''; else if ($('cookie').value.trim()) body.cookie = $('cookie').value.trim();
    try { await api('/api/settings', 'PUT', body); $('cookie').value = ''; await loadSettings(); notice('设置已保存。Cookie 仅显示配置状态。'); }
    catch (error) { notice(error.message, true); } finally { button.disabled = false; }
  });
  $('admin-key-form').addEventListener('submit', async event => {
    event.preventDefault(); const button = event.target.querySelector('[type="submit"]'); button.disabled = true;
    const key = $('new-admin-key').value, message = keyError(key, $('repeat-admin-key').value), token = state.token, epoch = state.authEpoch || 0;
    const current = () => (state.authEpoch || 0) === epoch && state.token === token;
    try {
      if (message) throw new Error(message);
      await api('/api/auth/key', 'PUT', { key });
      if (!current()) return;
      state.token = key; localStorage.setItem('library_access_key', key); state.auth.setup_required = false;
      event.target.reset(); await loadSettings();
      if ((state.authEpoch || 0) !== epoch || state.token !== key) return;
      $('admin-key-message').textContent = '已修改。当前浏览器保持登录，请把 抖音收藏库助手 中的旧密钥换成新密钥。';
      notice('管理员密钥已修改，旧密钥已失效。');
    } catch (error) { if (current() || ((state.authEpoch || 0) === epoch && state.token === key)) $('admin-key-message').textContent = error.message; }
    finally { button.disabled = false; }
  });
  $('reveal-token').addEventListener('click', () => { const visible = $('script-token').type === 'password'; $('script-token').type = visible ? 'text' : 'password'; $('reveal-token').textContent = visible ? '隐藏' : '显示'; });
  $('feed').addEventListener('keydown', event => {
    if (!['ArrowUp', 'ArrowDown'].includes(event.key) || ['VIDEO','INPUT'].includes(event.target.tagName)) return;
    event.preventDefault(); $('feed').scrollBy({ top: $('feed').clientHeight * (event.key === 'ArrowUp' ? -1 : 1), behavior: 'smooth' });
  });
  $('feed').addEventListener('scroll',()=>{schedulePlayback();const f=$('feed');if(state.hasMore&&!state.loadingLibrary&&f.scrollHeight-f.scrollTop-f.clientHeight<f.clientHeight*2)loadLibrary().catch(error=>notice(error.message,true));},{passive:true});
  document.addEventListener('visibilitychange',syncPlayback);
  setInterval(() => {
    if (!state.token || document.hidden || state.busy) return;
    state.busy = true;
    const operation = state.view === 'tasks' ? loadTasks() : ['library','settings'].includes(state.view) ? loadLegacy() : Promise.resolve();
    operation.catch(error => notice(error.message, true)).finally(() => { state.busy = false; });
  }, 5000);
  initializeConnection();
})();
