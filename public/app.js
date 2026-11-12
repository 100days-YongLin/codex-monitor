const $ = (id) => document.getElementById(id);
const labels = { running: '执行中', completed: '已完成', interrupted: '已中断', failed: '执行失败', unknown: '状态待确认' };
let tasks = [], connected = false, offset = 0, lastSuccess = 0, signature = '', wakeLock;
let initialized = false, celebrationTimeout, celebrating = [];
const announcedCompletions = new Set();
let cleared = new Set();
try { cleared = new Set(JSON.parse(localStorage.getItem('codex-monitor-cleared') || '[]')); } catch {}
const demo = new URLSearchParams(location.search).has('demo');
const key = (t) => `${t.id}:${t.turnId}`;
function dismissCelebration() {
  clearTimeout(celebrationTimeout);
  $('celebration').hidden = true;
  celebrating = [];
}
function celebrate(completed) {
  celebrating.push(...completed);
  const count = celebrating.length;
  $('celebration-heading').textContent = count === 1 ? '任务完成' : `${count} 个任务已完成`;
  const titles = $('celebration-titles');
  titles.replaceChildren(...celebrating.slice(-3).map(task => element('div', '', task.title)));
  if (count > 3) titles.append(element('div', 'celebration-more', `还有 ${count - 3} 个任务`));
  $('celebration').hidden = false;
  $('celebration').classList.remove('celebrating');
  // Restart the eight-second animation when another task finishes.
  void $('celebration').offsetWidth;
  $('celebration').classList.add('celebrating');
  clearTimeout(celebrationTimeout);
  celebrationTimeout = setTimeout(dismissCelebration, 8000);
}
function receiveTasks(next) {
  const completed = next.filter(task => task.status === 'completed' && !announcedCompletions.has(key(task)));
  for (const task of completed) announcedCompletions.add(key(task));
  if (initialized && completed.length) celebrate(completed);
  initialized = true;
  tasks = next;
}
function element(tag, className, text) {
  const el = document.createElement(tag); el.className = className;
  if (text !== undefined) el.textContent = text;
  return el;
}
function duration(task) {
  if (task.startedAt == null) return '—';
  const end = task.endedAt ?? (connected ? (Date.now() / 1000 + offset) : lastSuccess);
  const total = Math.max(0, Math.floor(end - task.startedAt));
  const mins = Math.floor(total / 60), secs = String(total % 60).padStart(2, '0');
  return `${String(mins).padStart(2, '0')}:${secs}`;
}
function render() {
  const visible = tasks.filter(t => t.status === 'running' || !cleared.has(key(t)));
  const counts = $('counts'); counts.replaceChildren();
  const running = visible.filter(t => t.status === 'running').length;
  const completed = visible.filter(t => t.status === 'completed').length;
  counts.append(element('b', '', String(running)), ' 执行中 · ', element('b', 'green', String(completed)), ' 已完成');
  const other = visible.length - running - completed;
  if (other) counts.append(` · ${other} 待关注`);
  const nextSignature = JSON.stringify(visible.map(t => [key(t), t.status, t.title, t.project]));
  if (nextSignature === signature) return;
  signature = nextSignature;
  const container = $('tasks'); container.replaceChildren();
  if (!visible.length) {
    const empty = element('div', 'empty');
    empty.append(element('div', 'empty-orbit', '◎'), element('h2', '', '一切就绪，等待下一次开始'), element('p', '', '任务运行后会自动出现在这里'));
    container.append(empty); return;
  }
  for (const task of visible) {
    const card = element('article', `task ${task.status}`); card.dataset.key = key(task);
    const state = element('div', 'state');
    state.append(element('span', task.status === 'running' ? 'spinner' : 'symbol', task.status === 'running' ? '' : task.status === 'completed' ? '✓' : '!'), document.createTextNode(labels[task.status]));
    const title = element('h2', '', task.title); title.title = task.title;
    const metric = element('div', 'metric');
    if (task.status === 'completed') metric.append(element('div', 'checkmark', '✓'));
    else if (task.status === 'running') metric.append(element('div', 'timer', duration(task)));
    else metric.append(element('div', 'timer', task.status === 'unknown' ? '?' : '!'));
    if (task.status !== 'running') {
      const caption = task.status === 'completed' ? `总耗时 ${duration(task)}` : task.status === 'unknown' ? '未收到结束事件，请查看 Codex' : `${labels[task.status]} · ${duration(task)}`;
      metric.append(element('div', 'caption', caption));
    }
    card.append(state, title, metric);
    container.append(card);
  }
}
function tick() {
  const now = new Date();
  $('clock-hm').textContent = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;
  $('clock-s').textContent = `:${String(now.getSeconds()).padStart(2, '0')}`;
  $('date').textContent = `${now.getMonth() + 1}月${now.getDate()}日 · ${['星期日','星期一','星期二','星期三','星期四','星期五','星期六'][now.getDay()]}`;
  for (const task of tasks) {
    if (task.status !== 'running') continue;
    const card = [...$('tasks').children].find(c => c.dataset.key === key(task));
    if (!card) continue;
    const timer = card.querySelector('.timer');
    timer.textContent = duration(task); timer.classList.toggle('long', timer.textContent.length > 6);
  }
}
function connection(ok, loading = false) {
  connected = ok; document.body.classList.toggle('stale', !ok && !loading);
  $('connection').className = `connection ${ok ? 'online' : loading ? '' : 'offline'}`;
  const label = demo ? '演示模式，非实时任务' : ok ? 'Mac 已连接' : loading ? '正在读取 Codex' : '连接中断，计时已暂停，正在重连';
  $('connection').querySelector('span').textContent = label;
  $('connection').title = label;
}
async function poll() {
  if (demo) return;
  try {
    const response = await fetch('/api/status', { cache: 'no-store', signal: AbortSignal.timeout(8000) });
    if (!response.ok) throw Error('HTTP');
    const data = await response.json();
    if (data.connected) {
      offset = data.serverTime - Date.now() / 1000;
      lastSuccess = data.serverTime; receiveTasks(data.tasks);
    }
    connection(data.connected, data.loading);
    render(); tick();
  } catch { connection(false); }
  setTimeout(poll, 2000);
}
$('clear').addEventListener('click', () => {
  for (const task of tasks) if (['completed', 'failed', 'interrupted'].includes(task.status)) cleared.add(key(task));
  try { localStorage.setItem('codex-monitor-cleared', JSON.stringify([...cleared].slice(-2000))); } catch {}
  render();
  document.querySelector('.menu').open = false;
});
async function keepAwake() {
  try { if ('wakeLock' in navigator && document.visibilityState === 'visible') wakeLock = await navigator.wakeLock.request('screen'); } catch {}
}
$('fullscreen').addEventListener('click', async () => {
  document.querySelector('.menu').open = false;
  try {
    if (document.fullscreenElement) await document.exitFullscreen();
    else if (document.documentElement.requestFullscreen) await document.documentElement.requestFullscreen();
    else { $('fullscreen').textContent = 'Safari → 分享 → 添加到主屏幕'; }
    await keepAwake();
  } catch { $('fullscreen').textContent = '请添加到主屏幕'; }
});
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') keepAwake(); });
$('dismiss-celebration').addEventListener('click', dismissCelebration);
document.addEventListener('keydown', event => { if (event.key === 'Escape') dismissCelebration(); });
if (demo) {
  const now = Date.now() / 1000;
  tasks = [{ id:'demo1',turnId:'1',title:'构建监控页面',project:'Codex Monitor',status:'running',startedAt:now-512,endedAt:null },
    { id:'demo2',turnId:'1',title:'优化任务同步',project:'Local Bridge',status:'running',startedAt:now-197,endedAt:null },
    { id:'demo3',turnId:'1',title:'修复登录问题',project:'Web App',status:'completed',startedAt:now-546,endedAt:now-200 }];
  if (new URLSearchParams(location.search).get('demo') === 'stress') {
    tasks[0].title = '这是一个用来检查弹性布局的非常长的中文任务名称，不应该挤出任务卡片或遮挡计时';
    tasks[0].startedAt = now - 123456;
    tasks[1].title = 'VeryLongUnbrokenTaskNameToVerifyResponsiveCardLayoutAndTimerVisibility';
    tasks.push({...tasks[2], id:'demo4', title:'多任务横向滚动检查'});
  }
  receiveTasks(tasks); connection(true); render();
  if (new URLSearchParams(location.search).get('demo') === 'celebrate') {
    setTimeout(() => {
      receiveTasks(tasks.map((task, index) => index === 0 ? {...task, status:'completed', endedAt:Date.now()/1000} : task));
      render();
    }, 2000);
  }
}
tick(); setInterval(tick, 1000); poll(); keepAwake();
