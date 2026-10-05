/* 锁定「全部跑一遍」期间的按钮锁行为（v2.2.5）。
   从真源码里原样抽出 lockPushBtns / unlockPushBtns 跑，配假 DOM。 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const APP = path.join(__dirname, '..', 'src', 'static', 'app.js');
const SRC = fs.readFileSync(APP, 'utf8');

function grabFn(name) {
  const lines = SRC.split('\n');
  let i = -1;
  for (let n = 0; n < lines.length; n++) {
    if (lines[n].indexOf('function ' + name + '(') >= 0) { i = n; break; }
  }
  if (i < 0) throw new Error('没找到函数：' + name);
  let j = i, d = 0, started = false;
  while (j < lines.length) {
    d += (lines[j].match(/{/g) || []).length - (lines[j].match(/}/g) || []).length;
    if (/{/.test(lines[j])) started = true;
    if (started && d <= 0 && j > i) break;
    j++;
  }
  return lines.slice(i, j + 1).join('\n');
}

let PASS = 0, FAIL = 0;
const FAILED = [];
function ck(cond, name) {
  if (cond) { PASS++; console.log('[PASS] ' + name); }
  else { FAIL++; FAILED.push(name); console.log('[FAIL] ' + name); }
}

/* ---- 假 DOM ---- */
function mkBtn(act, id) {
  const cls = new Set();
  return {
    _id: id, _act: act, disabled: false,
    classList: {
      add: function () { for (const a of arguments) cls.add(a); },
      remove: function () { for (const a of arguments) cls.delete(a); },
      contains: function (c) { return cls.has(c); },
    },
    _cls: cls,
    getAttribute: function (k) { return k === 'data-act' ? this._act : null; },
  };
}

function mkDoc(buttons) {
  return {
    querySelectorAll: function (sel) {
      // 只支持形如 [data-act="xxx"] 的选择器 —— 够用，且能真正验证
      // 「只锁 push-test」这件事（不过滤的话那条断言等于没测）。
      const m = /^\[data-act="([^"]+)"\]$/.exec(String(sel || '').trim());
      if (!m) return [];
      return buttons.filter(function (b) { return b._act === m[1]; });
    },
  };
}

function load() {
  const sandbox = { document: null, console: console };
  vm.createContext(sandbox);
  vm.runInContext(grabFn('lockPushBtns') + '\n' + grabFn('unlockPushBtns'), sandbox);
  return sandbox;
}

/* ---- 1. 锁住除触发者外的所有推送按钮 ---- */
{
  const s = load();
  const all = mkBtn('push-test', 'all');
  const b1 = mkBtn('push-test', 'k1');
  const b2 = mkBtn('push-test', 'k2');
  const b3 = mkBtn('push-test', 'k3');
  s.document = mkDoc([all, b1, b2, b3]);
  const locked = s.lockPushBtns(all);
  ck(locked.length === 3, '锁住 3 个单独按钮（触发者自己不锁）');
  ck(b1.disabled && b2.disabled && b3.disabled, '被锁的按钮都 disabled');
  ck(!all.disabled, '触发者「全部跑一遍」不被这里锁（由 runAct 管）');
  ck(b1._cls.has('is-busy'), '被锁的按钮加了 is-busy 样式');
}

/* ---- 2. 解锁 ---- */
{
  const s = load();
  const all = mkBtn('push-test', 'all');
  const b1 = mkBtn('push-test', 'k1');
  s.document = mkDoc([all, b1]);
  const locked = s.lockPushBtns(all);
  s.unlockPushBtns(locked);
  ck(!b1.disabled, '解锁后按钮恢复可点');
  ck(!b1._cls.has('is-busy'), '解锁后 is-busy 样式被摘掉');
}

/* ---- 3. 不误伤其它 data-act 的按钮 ---- */
{
  const s = load();
  const all = mkBtn('push-test', 'all');
  const other = mkBtn('check-now', 'other');
  s.document = mkDoc([all, other]);
  const locked = s.lockPushBtns(all);
  ck(locked.length === 0 && !other.disabled,
     '只锁 push-test，其它动作按钮不受影响');
}

/* ---- 4. 解锁空列表 / undefined 不抛 ---- */
{
  const s = load();
  let ok = true;
  try { s.unlockPushBtns(null); s.unlockPushBtns([]); s.unlockPushBtns(undefined); }
  catch (e) { ok = false; }
  ck(ok, 'unlockPushBtns 传入空值不抛异常（finally 里必须安全）');
}

/* ---- 5. 页面里真的只有一个 push-test 时也不出错 ---- */
{
  const s = load();
  const all = mkBtn('push-test', 'all');
  s.document = mkDoc([all]);
  const locked = s.lockPushBtns(all);
  ck(locked.length === 0, '只有触发者自己时返回空列表');
}

/* ---- 6. 源码里「全部跑一遍」分支确实成对调用了锁/解锁 ---- */
{
  const iAll = SRC.indexOf("if (kind === 'all') {");
  const seg = SRC.slice(iAll, iAll + 3000);
  ck(seg.indexOf('lockPushBtns(btn)') > 0, '跑批分支调用了 lockPushBtns(btn)');
  ck(seg.indexOf('unlockPushBtns(locked)') > 0, '跑批分支调用了 unlockPushBtns(locked)');
  const iTry = seg.indexOf('try {', seg.indexOf('lockPushBtns(btn)'));
  const iFin = seg.indexOf('} finally {');
  ck(iTry > 0 && iFin > iTry, '解锁放在 finally 里（异常也要恢复）');
}

console.log('\n' + PASS + ' 通过 / ' + FAIL + ' 失败');
if (FAIL) { console.log('失败项：' + FAILED.join('、')); process.exit(1); }
