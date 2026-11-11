(function (root) {
  'use strict';
  function create({ api, document, notice, openWindow = typeof root.open === 'function' ? root.open.bind(root) : null, confirm = typeof root.confirm === 'function' ? root.confirm.bind(root) : () => false }) {
    const $ = id => document.getElementById(id);
    const form = $('comment-channel-form'), message = $('comment-channel-message');
    const panel = $('comment-batch-panel'), status = $('comment-batch-status'), stopButton = $('comment-batch-stop'), resumeButton = $('comment-batch-resume'), link = $('comment-batch-link');
    let latest = null, generation = 0, serial = 0, timer = null, busy = false, watching = false;
    const active = batch => ['running', 'waiting_browser'].includes(batch?.status);
    function accountCheck(accounts) {
      if (accounts?.paused) throw new Error(`评论已暂停，请到设置处理${accounts.reason ? `：${accounts.reason}` : '。'}`);
      if (!accounts?.primary?.bound || !accounts?.secondary?.bound) throw new Error('请先到设置绑定大号和小号，并解除评论暂停。');
    }
    async function readChannel() {
      const result = await api('/api/comment-channel');
      if (!['browser', 'core'].includes(result?.channel)) throw new Error('评论通道状态无效，请到设置重试。');
      return { channel: result.channel };
    }
    async function selection(scope) {
      const [selected, accounts] = await Promise.all([readChannel(), api('/api/comment-accounts')]);
      accountCheck(accounts);
      if (selected.channel === 'browser') return 'secondary';
      if (!confirm(`本次使用后台大号更新${scope}的评论。确认继续？`)) throw new Error('已取消本次后台大号操作。');
      return 'primary-core';
    }
    async function refreshSettings() {
      const epoch = generation, selected = await readChannel();
      if (epoch !== generation) return selected;
      for (const radio of form.querySelectorAll('input[name="comment-channel"]')) radio.checked = radio.value === selected.channel;
      return selected;
    }
    form.addEventListener('submit', async event => {
      event.preventDefault();
      const channel = form.querySelector('input[name="comment-channel"]:checked')?.value, epoch = generation;
      if (!['browser', 'core'].includes(channel)) { message.textContent = '请选择评论下载通道。'; return; }
      try { await api('/api/comment-channel', 'PUT', { channel }); if (epoch === generation) message.textContent = '评论下载通道已保存。'; }
      catch (error) { if (epoch === generation) message.textContent = error?.message || '保存失败，请重试。'; }
    });
    function paint(batch) {
      latest = batch; panel.hidden = !batch; link.hidden = true; link.removeAttribute('href');
      if (!batch) return;
      const names = {running:'正在补全',waiting_browser:'等待小号浏览器',paused:'已中断，等待继续',stopped:'已停止',completed:'本次补全完成'};
      const channel = batch.channel === 'core' ? '软件自带 · 服务器后台' : '浏览器补全 · 需保持抖音助手页';
      status.textContent = `${batch.author_name || '此订阅'} · ${names[batch.status] || '状态未知'}\n${channel}\n已处理 ${batch.completed || 0} / ${batch.total || 0} 个作品 · 新增 ${batch.added || 0} 条评论 · 跳过 ${batch.skipped || 0} · 重复 ${batch.duplicates || 0}${batch.identity_limited ? '\n部分作品作者身份不明，未纳入。' : ''}${batch.reason ? `\n${batch.reason}` : ''}`;
      stopButton.disabled = busy || !['running','waiting_browser','paused'].includes(batch.status);
      resumeButton.hidden = batch.status !== 'paused'; resumeButton.disabled = busy;
    }
    function browserURL(result, id) {
      if (result?.batch?.id !== id || result.batch.channel !== 'browser' || !active(result.batch) || !result.work) return null;
      if (result.work.claimed) return null;
      if (!/^\d{19}$/.test(result.work.work_id || '') || !/^[a-f0-9]{32}$/.test(result.work.token || '') || !/^[a-f0-9]{32}$/.test(id)) throw new Error('浏览器任务地址无效，请停止后重试。');
      return `https://www.douyin.com/video/${result.work.work_id}#library-comments=${result.work.token}&library-comment-batch=${id}`;
    }
    async function readBrowser(batch) { return browserURL(await api(`/api/comment-batches/${batch.id}/browser`), batch.id); }
    function schedule() {
      clearTimeout(timer); timer = null;
      if (watching) { timer = setTimeout(() => refreshBatch().catch(() => {}), 5000); timer.unref?.(); }
    }
    async function refreshBatch() {
      watching = true;
      const epoch = generation, request = ++serial;
      try {
        const result = await api('/api/comment-batches/current');
        if (epoch !== generation || request !== serial || !watching) return;
        paint(result.batch || null);
        if (active(latest) && latest.channel === 'browser') {
          const url = await readBrowser(latest);
          if (epoch !== generation || request !== serial || !watching) return;
          if (url) { link.href = url; link.hidden = false; }
        }
      } catch (error) {
        if (epoch === generation && request === serial && watching) { panel.hidden = false; status.textContent = `进度暂时无法读取，服务器任务不会因此停止：${error.message}`; }
      } finally { if (epoch === generation && request === serial) schedule(); }
    }
    function blankTab() { try { const tab = openWindow?.('about:blank', '_blank') || null; if (tab) tab.opener = null; return tab; } catch { return null; } }
    async function showBrowser(batch, tab, epoch) {
      if (batch.channel !== 'browser' || !active(batch)) { try { tab?.close(); } catch {} return; }
      const url = await readBrowser(batch);
      if (epoch !== generation) { try { tab?.close(); } catch {} return; }
      if (url) {
        link.href = url; link.hidden = false;
        try { if (!tab || tab.closed) throw new Error(); tab.location.href = url; }
        catch { notice?.('请点击“打开抖音继续补全”，并保持助手页面运行。'); }
      } else { try { tab?.close(); } catch {} }
    }
    async function batch(author) {
      if (busy) return;
      if (!author?.id) { notice?.('无法识别订阅，请刷新后重试。'); return; }
      busy = true; const epoch = generation, tab = blankTab(); let submitted = false;
      try {
        const chosen = await selection('该订阅全部已收录作品');
        if (epoch !== generation) return;
        const result = await api(`/api/authors/${encodeURIComponent(author.id)}/comment-batches`, 'POST', {channel:chosen === 'primary-core' ? 'core' : 'browser',allow_primary:chosen === 'primary-core',request_id:Array.from(root.crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('')});
        submitted = true;
        if (epoch !== generation) return;
        paint(result.batch); await showBrowser(result.batch, tab, epoch);
      } catch (error) { if (epoch === generation) notice?.(error.message, true); }
      finally { if (!submitted || epoch !== generation) { try { tab?.close(); } catch {} } if (epoch === generation) { busy = false; if (watching) await refreshBatch(); } }
    }
    async function stop() {
      if (busy || !latest) return;
      busy = true; const epoch = generation, id = latest.id; paint(latest);
      try { await api(`/api/comment-batches/${id}/stop`, 'POST', {}); }
      catch (error) { if (epoch === generation) notice?.(`停止未确认：${error.message}`, true); }
      finally { if (epoch === generation) { busy = false; if (watching) await refreshBatch(); } }
    }
    async function resume() {
      if (busy || latest?.status !== 'paused') return;
      busy = true; const epoch = generation, batch = latest, tab = batch.channel === 'browser' ? blankTab() : null; let resumed = false;
      try {
        accountCheck(await api('/api/comment-accounts'));
        if (epoch !== generation) return;
        const primary = batch.channel === 'core';
        if (primary && !confirm('本次继续使用后台大号补全此订阅剩余作品的评论。确认继续？')) return;
        const result = await api(`/api/comment-batches/${batch.id}/resume`, 'POST', {allow_primary:primary}); resumed = true;
        if (epoch !== generation) return;
        paint(result.batch); await showBrowser(result.batch, tab, epoch);
      } catch (error) { if (epoch === generation) notice?.(error.message, true); }
      finally { if (!resumed || epoch !== generation) { try { tab?.close(); } catch {} } if (epoch === generation) { busy = false; if (watching) await refreshBatch(); } }
    }
    stopButton.addEventListener('click', stop); resumeButton.addEventListener('click', resume);
    function leave() { watching = false; ++serial; clearTimeout(timer); timer = null; }
    function reset() { ++generation; leave(); latest = null; busy = false; panel.hidden = true; link.hidden = true; link.removeAttribute('href'); message.textContent = ''; }
    return {refreshSettings,readChannel,selection,batch,stop,resume,refreshBatch,leave,reset,get running() { return active(latest); }};
  }
  const exported = {create};
  if (typeof module !== 'undefined' && module.exports) module.exports = exported;
  root.CloudriftCommentEntry = exported;
})(globalThis);
