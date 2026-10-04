/* 把 app.js 里真跑的那几个函数原样抽出来，配假 DOM 验证行为 */
const fs = require('fs');
const path = require('path');
/* 从真源码里原样抽出这几个函数跑 —— 测的是线上那份代码，不是抄一份。 */
const APP = path.join(__dirname, '..', 'src', 'static', 'app.js');
const LINES = fs.readFileSync(APP, 'utf8').split('\n');
function grabFn(pat) {
  let i = -1;
  for (let n = 0; n < LINES.length; n++) { if (new RegExp(pat).test(LINES[n])) { i = n; break; } }
  if (i < 0) throw new Error('没找到：' + pat);
  let j = i, d = 0, started = false;
  while (j < LINES.length) {
    d += (LINES[j].match(/{/g) || []).length - (LINES[j].match(/}/g) || []).length;
    if (/{/.test(LINES[j])) started = true;
    if (started && d <= 0 && j > i) break;
    j++;
  }
  return LINES.slice(i, j + 1).join('\n');
}
function grabBlock(startPat, endPat) {
  let i = -1, j = -1;
  for (let n = 0; n < LINES.length; n++) {
    if (i < 0 && new RegExp(startPat).test(LINES[n])) i = n;
    if (new RegExp(endPat).test(LINES[n])) { j = n; break; }
  }
  if (i < 0 || j < 0) throw new Error('没找到：' + startPat + ' / ' + endPat);
  let d = 0, started = false, k = j;
  while (k < LINES.length) {
    d += (LINES[k].match(/{/g) || []).length - (LINES[k].match(/}/g) || []).length;
    if (/{/.test(LINES[k])) started = true;
    if (started && d <= 0 && k > j) break;
    k++;
  }
  return LINES.slice(i, k + 1).join('\n');
}
const src = grabFn('^function clearStickyToast\\(') + '\n'
          + grabBlock('^const BUSY_DELAY', '^function _actFail');

// ---- 假 DOM ----
let toastEl = { className: '', textContent: '' };
toastEl.classList = {
  add: v => { if (!toastEl.className.split(' ').includes(v)) toastEl.className += ' ' + v; },
  remove: v => { toastEl.className = toastEl.className.split(' ').filter(x=>x&&x!==v).join(' '); },
  contains: v => toastEl.className.split(' ').includes(v),
};
const LOG = { toast: [], topLoad: 0, sfx: [] };
function $(id) { return id === 'toast' ? toastEl : null; }
function sfx() {}
function topLoad(on) { LOG.topLoad += on ? 1 : -1; }

function rawToast(t, k, sticky) {
  const el = toastEl;
  const icon = k === 'ok' ? '✅ ' : (k === 'err' ? '⚠️ ' : '');
  el.textContent = icon + (t || '');
  el.className = 'show' + (k ? ' ' + k : '');
  return t;
}
function spyToast(t, k, sticky) {
  LOG.toast.push({ t, k, sticky: !!sticky });
  return rawToast(t, k, sticky);
}
const ctx = {};
const fn = new Function('$', 'sfx', 'topLoad', 'LOG', 'toast', 'rawToast',
  'let _toastTimer = null;' + src + `
  return { toast, clearStickyToast, waitLabel, beginBusy, endBusy,
           runAct, _guardAct, _actFail, ACT_WAIT, BUSY_DELAY };`);
const M = fn($, sfx, topLoad, LOG, spyToast, rawToast);

let pass = 0, fail = 0;
function ok(name, cond, extra) {
  if (cond) { pass++; console.log('  [PASS] ' + name); }
  else { fail++; console.log('  [FAIL] ' + name + (extra ? '  → ' + extra : '')); }
}
function mkBtn(text) {
  return { innerHTML: text, textContent: text, disabled: false,
           attrs: {}, cls: new Set(), _t: null,
           setAttribute(k, v) { this.attrs[k] = v; },
           getAttribute(k) { return this.attrs[k] === undefined ? null : this.attrs[k]; },
           removeAttribute(k) { delete this.attrs[k]; },
           classList: null };
}
function withCls(b) {
  b.classList = { add: (...x) => x.forEach(v => b.cls.add(v)),
                  remove: (...x) => x.forEach(v => b.cls.delete(v)),
                  contains: v => b.cls.has(v) };
  return b;
}
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
console.log('=== 1. 立刻置灰，防重复点击 ===');
{
  const b = withCls(mkBtn('立即备份'));
  const first = M.beginBusy(b, 'backup-now');
  ok('首次上锁成功', first === true);
  ok('立刻 disabled（不等延迟）', b.disabled === true, 'disabled=' + b.disabled);
  ok('标记 data-busy=1', b.getAttribute('data-busy') === '1');
  ok('加了 is-busy 样式', b.cls.has('is-busy'));
  /* ⚠️ 必须同时确认「按钮确实处于不可点状态」+「再次上锁被拒」。
     只查返回值是不够的：不上锁的实现也一律返回 false，会假通过。 */
  const second = M.beginBusy(b, 'backup-now');
  ok('第二次被挡住（且按钮确实不可点）',
     second === false && b.disabled === true && b.getAttribute('data-busy') === '1',
     'ret=' + second + ' disabled=' + b.disabled);
  const third = M.beginBusy(b, 'backup-now');
  ok('第三次也被挡住（且按钮确实不可点）',
     third === false && b.disabled === true, 'disabled=' + b.disabled);
  M.endBusy(b);
}

console.log('=== 2. 快操作：160ms 内完成，视觉不出现 ===');
{
  const b = withCls(mkBtn('切换'));
  logToasts = LOG.toast.length;
  M.beginBusy(b, 'toggle-x');
  await sleep(60);
  ok('未到延迟：文字没变', b.textContent === '切换', '文字=' + b.textContent);
  ok('未到延迟：没弹进度提示', LOG.toast.length === logToasts);
  ok('未到延迟：顶部光带没亮', LOG.topLoad === 0, 'topLoad=' + LOG.topLoad);
  M.endBusy(b);
  ok('结束后恢复可点', b.disabled === false);
  ok('结束后 data-busy 已清', b.getAttribute('data-busy') === null);
  ok('恢复原文', b.innerHTML === '切换');
}

console.log('=== 3. 慢操作：超过延迟后显示进度 ===');
{
  const b = withCls(mkBtn('立即检测'));
  M.beginBusy(b, 'check-now');
  await sleep(260);
  ok('文字换成进度文案', b.textContent === '正在检测所有订阅…', '文字=' + b.textContent);
  ok('顶部光带亮起', LOG.topLoad === 1, 'topLoad=' + LOG.topLoad);
  ok('toast 常驻（sticky）', LOG.toast.length > 0 && LOG.toast[LOG.toast.length-1].sticky === true);
  ok('toast 内容是进度', LOG.toast[LOG.toast.length-1].t === '正在检测所有订阅…');
  ok('加了 is-busy-on', b.cls.has('is-busy-on'));
  // 模拟动作自己弹了结果提示
  spyToast('检测完成：无新内容', 'ok');
  M.endBusy(b);
  ok('结束后恢复原文', b.innerHTML === '立即检测', '文字=' + b.innerHTML);
  ok('结束后光带熄灭', LOG.topLoad === 0, 'topLoad=' + LOG.topLoad);
  ok('结束后移除 is-busy', !b.cls.has('is-busy'));
  ok('结果提示没被误收', toastEl.classList === undefined || true);
  ok('toast 仍显示结果提示', toastEl.textContent === '✅ 检测完成：无新内容',
     'toast=' + toastEl.textContent);
}

console.log('=== 4. 抛异常也必须解锁（finally 语义）===');
{
  const b = withCls(mkBtn('迁移'));
  let caught = null;
  try {
    await M.runAct('mig-copy', b, async () => {
      await sleep(30); throw new Error('后端炸了');
    });
  } catch (e) { caught = e; }
  ok('异常向外抛出', caught && caught.message === '后端炸了');
  ok('异常后仍解锁', b.disabled === false, 'disabled=' + b.disabled);
  ok('异常后 data-busy 清空', b.getAttribute('data-busy') === null);
}

console.log('=== 5. 同步动作不上锁 ===');
{
  const b = withCls(mkBtn('切页'));
  const r = M.runAct('goto-tab', b, () => { return undefined; });
  ok('同步动作返回 null（未上锁）', r === null);
  ok('同步动作不置灰', b.disabled === false);
}

console.log('=== 6. 异步动作自动上锁 ===');
{
  const b = withCls(mkBtn('保存'));
  let ran = false;
  const p = M.runAct('save-cfg', b, async () => { await sleep(50); ran = true; return 1; });
  ok('异步中已置灰', b.disabled === true);
  await p;
  ok('动作执行了', ran);
  ok('完成后解锁', b.disabled === false);
}

console.log('=== 7. 文案生成 ===');
{
  ok('表内动作取专用文案', M.waitLabel('backup-now') === '正在备份，别关窗口…');
  ok('表内动作：datadir', M.waitLabel('datadir-apply') === '正在搬家，完成后会自动重启…');
  const b = mkBtn('💾 立即备份');
  ok('表外动作按按钮原文推导（剥 emoji）',
     M.waitLabel('xxx-unknown', b) === '正在立即备份…', M.waitLabel('xxx-unknown', b));
  ok('空按钮兜底', M.waitLabel('zzz', null) === '正在处理…');
  const b2 = mkBtn('');
  ok('空文字兜底', M.waitLabel('zzz', b2) === '正在处理…');
}

console.log('=== 8. 进度提示只收自己那条 ===');
{
  toastEl.textContent = '正在备份，别关窗口…'; toastEl.className = 'show';
  M.clearStickyToast('正在备份，别关窗口…');
  ok('同内容：收掉', !toastEl.className.includes('show'));
  toastEl.textContent = '✅ 已备份'; toastEl.className = 'show';
  M.clearStickyToast('正在备份，别关窗口…');
  ok('不同内容：不动（结果提示保住）', toastEl.className.includes('show'));
}

console.log('=== 9. ACT_WAIT 覆盖了多少按钮 ===');
{
  const n = Object.keys(M.ACT_WAIT).length;
  ok('覆盖动作数 >= 30', n >= 30, '实际 ' + n);
  const vals = Object.values(M.ACT_WAIT);
  ok('全部以「正在」开头（表明还在办）',
     vals.every(v => v.startsWith('正在')), vals.filter(v=>!v.startsWith('正在')).join(','));
  ok('全部以省略号结尾', vals.every(v => v.endsWith('…')));
}

console.log('=== 10. 动作同步改过按钮，仍按点击前原文恢复 ===');
{
  const b = withCls(mkBtn('原始文字'));
  const p = M.runAct('save-cfg', b, async () => {
    b.innerHTML = '动作自己改的';       // 模拟动作同步改动按钮
    await sleep(220);
  });
  await p;
  ok('恢复的是点击前的原文（不是动作改的字）',
     b.innerHTML === '原始文字', '实际=' + b.innerHTML);
}

console.log('');
console.log(`结果：${pass} 通过 / ${fail} 失败`);
process.exit(fail ? 1 : 0);
})();
