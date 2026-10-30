(function () {
  'use strict';
  const ACTIVE = ['queued', 'parsing', 'downloading', 'paused'];
  const ATTENTION = ['failed', 'interrupted', 'awaiting_confirmation'];
  const FILTERS = [['all', '全部任务'], ['active', '进行中'], ['attention', '需处理'], ['completed', '已完成'], ['cancelled', '已取消']];
  function groupMatches(task, filter) {
    return filter === 'all' || (filter === 'active' ? ACTIVE.includes(task.status) : filter === 'attention' ? ATTENTION.includes(task.status) : task.status === filter);
  }
  function filterTasks(tasks, filter, query, title) {
    const search = query.trim().toLocaleLowerCase();
    return tasks.filter(task => groupMatches(task, filter) && [title(task), task.id, ...(task.preview || []).map(item => item.nickname || '')].join(' ').toLocaleLowerCase().includes(search));
  }
  function selectedTask(tasks, id) {
    return tasks.find(task => String(task.id) === String(id)) || tasks.find(task => ATTENTION.includes(task.status)) || tasks[0];
  }
  function taskCounts(tasks) { return Object.fromEntries(FILTERS.map(([filter]) => [filter, tasks.filter(task => groupMatches(task, filter)).length])); }
  function bulkEligible(task, action) {
    return action === 'retry' ? ['failed','interrupted','cancelled'].includes(task.status) : action === 'cancel' ? !['completed','cancelled'].includes(task.status) : true;
  }
  function create({ application, makeTaskCard, title, onBatch = async () => {} }) {
    const byId = id => document.getElementById(id);
    const node = (tag, className, text) => { const result = document.createElement(tag); if (className) result.className = className; if (text !== undefined) result.textContent = String(text); return result; };
    const list = byId('task-list'), detail = byId('task-detail'), dialog = byId('task-detail-dialog'), dialogBody = byId('task-dialog-body');
    const query = byId('task-query'), filters = byId('task-filters'), summary = byId('task-summary');
    const filterButtons = new Map(), summaryButtons = new Map();
    let tasks = [], filter = 'all', selected = null, lastData = null, batching = false;
    const bulkButtons = new Map();
    if (byId('task-bulk-actions')) for (const [value,label] of [['retry','全部重试'],['cancel','全部取消'],['delete','全部删除']]) {
      const button=node('button','secondary',label);button.type='button';
      button.title='仅处理当前筛选和搜索结果；删除只移除任务记录，保留媒体文件';
      button.addEventListener('click',async()=>{
        if (batching) return;
        const ids=filterTasks(tasks,filter,query.value,title).filter(task=>bulkEligible(task,value)).map(task=>task.id);
        batching=true;render();try{await onBatch(value,ids);}finally{batching=false;render();}
      });byId('task-bulk-actions').append(button);bulkButtons.set(value,button);
    }
    application.dataset.taskLayout = 'a';
    for (const [value, label] of FILTERS) {
      const button = node('button', 'task-filter'); button.type = 'button'; button.dataset.filter = value;
      button.append(node('span', '', label), node('span', 'task-filter-count', '0'));
      button.addEventListener('click', () => { filter = value; render(); }); filters.append(button); filterButtons.set(value, button);
    }
    for (const [value, label, help] of [['active', '进行中', '下载与排队'], ['attention', '需要处理', '重试或确认画质'], ['completed', '完整保存', '已进入作品库']]) {
      const button = node('button', 'task-summary-item'); button.type = 'button';
      button.append(node('span', '', label), node('strong', '', '0'), node('small', '', help));
      button.addEventListener('click', () => { filter = value; render(); }); summary.append(button); summaryButtons.set(value, button);
    }
    const summaryNote = node('div', 'task-summary-note'); summaryNote.append(node('strong', '', '下载一次，安心保存'), node('p', '', '已有作品自动跳过，追更由你决定。')); summary.append(summaryNote);
    query.addEventListener('input', render);
    byId('task-dialog-close').addEventListener('click', () => dialog.close());
    dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
    function detailedCard(task) {
      const card = makeTaskCard(task); card.classList.add('task-detailed-card'); card.dataset.status = task.status;
      const mark = node('span', 'task-letter', title(task).slice(0, 1)); mark.setAttribute('aria-hidden', 'true'); card.prepend(mark); return card;
    }
    function showDetail(task, openDialog) {
      if (!task) { detail.replaceChildren(); detail.hidden = true; if (dialog.open) dialog.close(); return; }
      selected = task.id; detail.hidden = true;
      detail.replaceChildren(node('p', 'task-detail-label', '任务详情'), detailedCard(task));
      if (openDialog || dialog.open) {
        dialogBody.replaceChildren(detailedCard(task));
        if (!dialog.open) dialog.showModal();
      }
    }
    function render() {
      const counts = taskCounts(tasks), visible = filterTasks(tasks, filter, query.value, title);
      for(const [value,button] of bulkButtons)button.disabled=batching||!visible.some(task=>bulkEligible(task,value));
      for (const [value, button] of filterButtons) { button.setAttribute('aria-pressed', String(filter === value)); button.lastElementChild.textContent = counts[value]; }
      for (const [value, button] of summaryButtons) button.querySelector('strong').textContent = String(counts[value]).padStart(2, '0');
      byId('task-result-count').textContent = `${visible.length} 个任务`;
      list.replaceChildren();
      const chosen = selectedTask(visible, selected);
      if (!visible.length) {
        const empty = node('div', 'empty task-empty'), filtered = Boolean(query.value.trim() || filter !== 'all');
        empty.append(node('span', 'empty-symbol', '↓'), node('strong', '', filtered ? '没有匹配的任务' : '还没有下载任务'), node('p', '', filtered ? '换个关键词，或查看全部任务。' : '添加作品链接，或在抖音网页点击抖音收藏库助手发送。'));
        const button = node('button', 'secondary', filtered ? '查看全部任务' : '添加下载'); button.type = 'button';
        button.addEventListener('click', () => { if (filtered) { filter = 'all'; query.value = ''; render(); } else byId('open-submit').click(); });
        empty.append(button); list.append(empty);
      }
      for (const task of visible) {
        const card = detailedCard(task), button = node('button', 'secondary task-open-detail', '查看详情 →'); button.type = 'button';
        button.addEventListener('click', () => showDetail(task, true)); card.querySelector('.record-actions').append(button); list.append(card);
      }
      if (dialog.open) showDetail(chosen, false);
      detail.hidden = true;
    }
    return {
      update(nextTasks) {
        if (!Array.isArray(nextTasks)) throw new Error('任务列表格式不正确，请刷新重试。');
        const data = JSON.stringify(nextTasks); if (data === lastData) return;
        lastData = data; tasks = nextTasks; render();
      }
    };
  }
  const exports = { groupMatches, filterTasks, selectedTask, taskCounts, bulkEligible, create };
  if (typeof module !== 'undefined' && module.exports) module.exports = exports;
  else window.CloudriftTaskView = exports;
})();
