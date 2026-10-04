/* ============================================================
   OnOB站通知订阅工具（OnO Bilibili Notifier / OnOBN）· 管理面板 前端
   事件驱动：所有按钮用 data-act 分发，避免逐个元素绑定；
   元素缺失时返回替身而不是 null，一处出错不影响整页。
   ============================================================ */

/* ---------- 元素获取（带缺失保护） ---------- */
const _MISSING = new Set();
function _dummy(id) {
  if (!_MISSING.has(id)) {
    _MISSING.add(id);
    console.warn('[bili] 页面缺少元素 #' + id);
  }
  return {
    _missing: true, id: id,
    style: {}, dataset: {},
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    addEventListener() {}, removeEventListener() {},
    appendChild() {}, remove() {}, focus() {}, click() {},
    scrollIntoView() {}, querySelector() { return null; },
    querySelectorAll() { return []; },
    getAttribute() { return null; }, setAttribute() {},
    value: '', checked: false, textContent: '', innerHTML: '',
  };
}
const $ = id => document.getElementById(id) || _dummy(id);

/* ---------- 工具 ---------- */
// 直播间号显示：两个号都有就都显示（原/短），只有一个就直接显示数字。
// 放在 esc 之前，下面几处显示都能用。
function roomText(longId, shortId) {
  const a = parseInt(longId || 0, 10) || 0;
  let b = parseInt(shortId || 0, 10) || 0;
  if (b && b === a) b = 0;          // 短号和长号一样 → 其实只有这一个
  if (!a && !b) return '';
  if (a && b) return '原：' + a + '　短：' + b;
  return String(a || b);
}

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"]/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

/* 属性值转义：esc 已把 " 转成 &quot;，塞进 data-tip="…" 是安全的。
   单独取个名字，提醒调用处"这是往属性里写"，别再套一层引号。 */
function escAttr(s) { return esc(s); }

/* ---------- 音效 ----------
   sfx() 是全局唯一入口，好处是三件事都收在一处：
     1. sound.js 没加载 / 浏览器不支持时静默失败，不会报错打断界面；
     2. 音效名写错也只是不响，不影响功能；
     3. 以后想统一静音，改这一个函数就够。 */
function sfx(name) {
  if (!name) return false;
  try {
    if (window.Sound && typeof window.Sound.play === 'function') {
      return window.Sound.play(name);
    }
  } catch (_) { /* 出声失败不该影响任何功能 */ }
  return false;
}

/* 音效档位存浏览器本地（纯界面偏好，不占订阅数据）。
   读不到就当作「很轻」，不阻塞界面。 */
function soundLevel() {
  try {
    if (window.Sound && typeof window.Sound.level === 'function') {
      return window.Sound.level() || 'off';
    }
  } catch (_) {}
  return '1';
}

function syncSoundSelect() {
  const el = $('cSound');
  if (!el || el._missing) return;
  el.value = soundLevel();
}

let _toastTimer = null;
/* 第三参 sticky=true → 常驻，不自动消失。
   ⚠️ 进度类提示（"正在检测…""正在备份…"）必须常驻：
      它代表"事情还在办"，2.8 秒后自己消失会让人以为已经办完了，
      而实际上后台可能还在跑。结束时由 endBusy 主动收掉。 */
function toast(text, kind, sticky) {
  const el = $('toast');
  if (!el || el._missing) return;
  /* 结果类提示加个图标：颜色之外再给一层形状区分，
     色弱用户也能一眼看出是成功还是出错。
     用 textContent 赋值，图标是普通字符、不涉及 HTML，安全。 */
  const icon = kind === 'ok' ? '✅ ' : (kind === 'err' ? '⚠️ ' : '');
  el.textContent = icon + (text || '');
  /* ⚠️ kind 以前**收了但没用**：toast(msg,'err') 只加 show，
     于是"检测失败"跟"已保存"长得一模一样，看不出是成功还是出错。
     现在 kind 落到 class 上（#toast.ok / #toast.err 已有配色）。
     用 className 整体赋值而不是 add，避免上一轮的 ok/err 残留。 */
  el.className = 'show' + (kind ? ' ' + kind : '');
  /* 提示条弹入时配一声：成功是柔和上行，出错是下行，不看文字也知道结果。
     普通提示（没给 kind）不响，避免后台自动刷新时无端出声。 */
  sfx(kind === 'err' ? 'err' : (kind === 'ok' ? 'ok' : ''));
  clearTimeout(_toastTimer);
  // 进度类提示（sticky）不设自动消失，由 endBusy 主动收掉
  if (!sticky) _toastTimer = setTimeout(() => el.classList.remove('show'), 2800);
}

/* 收掉某条**特定内容**的常驻提示。
   ⚠️ 只收内容完全相同的那条：动作自己弹的结果提示（"已保存""检测完成"）
      不能误收 —— 否则忙了半天，最后一条提示刚出来就被抹掉。 */
function clearStickyToast(text) {
  const el = $('toast');
  if (!el || el._missing) return;
  if (!el.classList.contains('show')) return;
  if (el.textContent !== text) return;
  el.classList.remove('show');
}

function showMsg(id, text, kind) {
  const el = $(id);
  if (!el || el._missing) return;
  el.textContent = text || '';
  el.className = 'msg show' + (kind ? ' ' + kind : '');
  sfx(kind === 'err' ? 'err' : (kind === 'ok' ? 'ok' : ''));
}

/* 网络类异常的中文说明。
   ⚠️ 以前这里直接 String(e) 抛给用户，而浏览器原生异常就是
      "TypeError: Failed to fetch" —— 一串英文，既看不出是后端挂了，
      也不知道该怎么办。现在按情形说人话。 */
function netErrText(e) {
  const s = String((e && e.message) || e || '');
  if (/abort/i.test(s)) return '请求超时——面板这会儿没响应，等几秒再试';
  if (/Failed to fetch|NetworkError|Load failed|TypeError/i.test(s)) {
    return '连不上管理面板（后端没响应）：机器人可能没在跑，或正在重启。刷新页面重试';
  }
  return '请求失败：' + s;
}

/* 未捕获异常的兜底。
   ⚠️ post() 会把网络异常重新抛出，调用方万一漏了 catch，
      界面上就什么都不显示、控制台只剩一句
      "TypeError: Failed to fetch" —— 看着像"点了没反应"。
      这里统一接住，翻成中文提示，原文仍打进控制台方便排查。 */
window.addEventListener('unhandledrejection', (ev) => {
  const e = (ev && ev.reason) || {};
  const s = String(e.message || e || '');
  if (!/Failed to fetch|NetworkError|Load failed|abort|timeout/i.test(s)) return;
  try { ev.preventDefault(); } catch (_) {}
  toast(netErrText(e), 'err');
  console.error('[未处理的网络异常]', e);
});

function api(path, opts) {
  opts = opts || {};
  const isGet = !opts.method || opts.method === 'GET';
  const timeout = opts.timeout || (isGet ? 15000 : 30000);
  let timer = null;
  // 超时控制：以前后端卡住时请求会一直挂着，界面永远转圈
  if (typeof AbortController === 'function') {
    const ctrl = new AbortController();
    opts.signal = ctrl.signal;
    timer = setTimeout(() => { try { ctrl.abort(); } catch (_) {} }, timeout);
  }
  // ⚠️ 传 FormData（上传备份包）时不能塞 Content-Type：
  //    浏览器要自己带 boundary，写死了服务端就解析不出文件内容。
  const isForm = (typeof FormData === 'function') &&
                 (opts.body instanceof FormData);
  const base = isForm ? {} : { 'Content-Type': 'application/json' };
  opts.headers = Object.assign(base, {
    'X-Requested-With': 'bili-notify',
  }, opts.headers || {});
  const clear = () => { if (timer) { clearTimeout(timer); timer = null; } };
  return fetch(path, opts).then(r => {
    clear();
    if (!r.ok) {
      // 非 2xx 时把正文片段带上，比只报一个状态码有用得多
      return r.text().then(t => {
        const snip = String(t || '').replace(/\s+/g, ' ').trim().slice(0, 120);
        return { ok: false, msg: '面板返回 ' + r.status + (snip ? '：' + snip : '') };
      }, () => ({ ok: false, msg: '面板返回 ' + r.status }));
    }
    return r.json().catch(() => ({ ok: false, msg: '返回解析失败（不是 JSON）' }));
  }).catch(e => { clear(); return { ok: false, msg: netErrText(e) }; });
}

/* 顶部加载光带：只在**用户主动操作**（提交/点按钮）时亮，
   自动轮询刷新不亮 —— 否则每 15 秒闪一次反而更闹。 */
let _tlBusy = 0;
function topLoad(on) {
  const el = document.getElementById('topLoad');
  if (!el) return;
  _tlBusy = Math.max(0, _tlBusy + (on ? 1 : -1));
  el.classList.toggle('on', _tlBusy > 0);
}

const post = (path, body) => {
  topLoad(true);
  const done = () => topLoad(false);
  return api(path, {
    method: 'POST', body: JSON.stringify(body || {}),
  }).then(r => { done(); return r; }, e => { done(); throw e; });
};

/* ---------- 主题 ---------- */
const root = document.documentElement;
function initTheme() {
  const saved = localStorage.getItem('bili-theme');
  let t = saved;
  if (!t) {
    t = (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches)
      ? 'dark' : 'light';
  }
  applyTheme(t);

  const btn = $('themeBtn');
  if (btn && !btn._missing) {
    btn.onclick = () => {
      const now = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
      /* ⚠️ 必须查 .tg：按钮里的图标是 <span class="tg">，
         CSS 也是 .theme-btn .tg。以前写成 .theme-icon，查出来是 null，
         于是图标永远不换、也不转 —— 表现为"点了没反应"。 */
      const icon = btn.querySelector('.tg');
      /* 圆形扩散：从按钮那一点铺满屏幕。
         ⚠️ 不支持 View Transitions 的浏览器直接瞬切，不做半吊子动画。 */
      const reduce = window.matchMedia &&
        window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      const doSwitch = () => {
        applyTheme(now);
        localStorage.setItem('bili-theme', now);
        if (icon) {
          icon.textContent = now === 'dark' ? '☀️' : '🌙';
          icon.style.transform = 'rotate(360deg)';
          setTimeout(() => { if (icon) icon.style.transform = ''; }, 400);
        }
      };
      /* 不支持 View Transitions（旧内核 / 部分 Electron）：
         直接 doSwitch 就是深↔浅一帧硬切 —— 那就是"闪屏"。
         退回整页颜色交叉淡入：先加 .theming（样式里那条统一过渡），
         ⚠️ 中间必须强制回流，否则"加过渡"和"换颜色"在同一次样式计算里完成，
            浏览器当成初始状态，根本不会补间。 */
      /* 系统开了"减少动效"：硬切，不加任何补间。 */
      if (reduce) { doSwitch(); return; }
      if (!document.startViewTransition) {
        root.classList.add('theming');
        void root.offsetWidth;            /* 强制回流，让过渡先生效 */
        doSwitch();
        setTimeout(() => root.classList.remove('theming'), 380);
        return;
      }
      const r = btn.getBoundingClientRect();
      const x = r.left + r.width / 2, y = r.top + r.height / 2;
      const far = Math.hypot(
        Math.max(x, window.innerWidth - x),
        Math.max(y, window.innerHeight - y));
      root.style.setProperty('--theme-x', x + 'px');
      root.style.setProperty('--theme-y', y + 'px');
      /* ⚠️ 半径必须留余量：终态若比视口对角差 1px，最后一帧的角落就还
         是旧快照，等伪元素销毁才纠正成新主题 —— 收尾那一下的闪就是它。 */
      root.style.setProperty('--theme-r',
        (Math.ceil(far * 1.06) + 12) + 'px');
      /* 锁掉元素自身的过渡：新快照是实时的，不锁的话铺开的那一片
         颜色还在补间，铺到一半才追上。 */
      root.classList.add('theme-lock');
      /* ⚠️ 「闪在收尾」的第三条：伪元素销毁和真 DOM 首帧绘制挤在同一帧。
         此时若同时撤掉 theme-lock，几十个元素在同一帧恢复 transition，
         会立刻起一批颜色补间 —— 整页在收尾时又"重新染一遍色"。
         让真 DOM 先在「仍然锁着」的状态下画满两帧，再解锁。 */
      const unlock = () => {
        requestAnimationFrame(() => requestAnimationFrame(() => {
          root.classList.remove('theme-lock');
        }));
      };
      let vt;
      try { vt = document.startViewTransition(doSwitch); }
      catch (e) { root.classList.remove('theme-lock'); doSwitch(); return; }
      if (vt.ready) vt.ready.catch(() => {});
      if (vt.finished) vt.finished.then(unlock, unlock);
      setTimeout(unlock, 900);           /* 兜底：VT 卡住也别把过渡永久锁死 */
    };
  }
}
function applyTheme(t) {
  root.setAttribute('data-theme', t);
  const icon = document.querySelector('.theme-btn .tg');
  if (icon) icon.textContent = t === 'dark' ? '☀️' : '🌙';
  /* 侧栏那行字要跟着变，否则永远显示 HTML 里写死的「夜间」 */
  const tx = document.getElementById('themeTx');
  if (tx) tx.textContent = t === 'dark' ? '夜间' : '日间';
}

/* ---------- 五大板块（v1.31.0） ----------
   ⚠️ 旧的 7 个 sec（subs/setup/bili/status/logs/safe/changelog）
   已重组成 5 个：home / cred / subs / diag / setup。
   B站登录 并进「凭据」，安全 并进「凭据」，
   运行状态 + 日志 并进「测试与诊断」。 */
const SEC_TITLE = {
  home: '概况', cred: '凭据', subs: '订阅',
  diag: '测试与诊断', setup: '设置', changelog: '更新日志',
};

/* 板块 → 默认选项卡：切换板块时自动选中第一个有内容的选项卡 */
const SEC_TABS = {
  home: null, cred: 'qq', subs: 'list', diag: 'push', setup: 'gen',
  changelog: null,
};

function showSec(sec) {
  // ⚠️ 新版是常驻侧边栏 + 主区直接显示板块，**没有 #sheet / #sheetBody**。
  // 之前这里一开头就 `if (!sheet || sheet._missing) return false;`，
  // 而 sheet 已经随宫格一起移除 —— 于是每次切换都在第一行直接返回 false，
  // 板块永远切不动。现在只依赖 .sec 本身。
  const secs = document.querySelectorAll('.sec');
  let hit = false;
  secs.forEach(s => {
    const on = s.getAttribute('data-sec') === sec;
    if (on) hit = true;
    // ⚠️ show 和 on 都要加：style.css 用 .sec.show，布局样式用 .panel.on。
    // 只加一个会让另一套规则把面板藏起来（表现就是主区空白）。
    s.classList.toggle('show', on);
    s.classList.toggle('on', on);
  });
  if (!hit) return false;
  // 侧边栏高亮跟着走
  document.querySelectorAll('.nav-i').forEach(n => {
    n.classList.toggle('on', n.getAttribute('data-sec') === sec);
  });
  // 主区滚回顶部：从一个长板块切到另一个时不该停在中间
  const main = document.querySelector('.main');
  if (main) main.scrollTop = 0;
  const w = window; if (w && w.scrollTo) { try { w.scrollTo(0, 0); } catch (e) {} }
  // 换板块时把上一个板块的选项卡收干净，避免残留
  const dft = SEC_TABS[sec];
  if (dft) showTab(sec, dft);
  return true;
}

/* 切选项卡：只显示当前板块内的目标子面板。
   ⚠️ 严格隔离 —— 先关掉**该板块内所有**子面板，再打开目标。
   不能靠"加 class 覆盖"，否则会出现两个面板同时可见。 */
function showTab(sec, tab) {
  const s = document.querySelector('.sec[data-sec="' + sec + '"]');
  if (!s) return;
  s.querySelectorAll('.sub-panel').forEach(p => {
    const on = p.getAttribute('data-tab') === tab;
    p.classList.toggle('on', on);
    p.classList.toggle('show', on);
  });
  s.querySelectorAll('.tab').forEach(t => {
    t.classList.toggle('on', t.getAttribute('data-tab') === tab);
  });
}

function openSec(sec) {
  if (!showSec(sec)) return false;
  // ⚠️ 切换功能前必须先把可能开着的文案面板关掉。
  // 实测现象：面板关掉后点别的功能，面板仍盖在上面（看着就是"关不掉"），
  // 只有刷新页面才消失。所以这里强制关一次，切功能一定回到干净状态。
  closeSheet();
  const tab = SEC_TABS[sec];
  if (tab) showTab(sec, tab);
  // 打开时才加载对应数据，省请求
  if (sec === 'home') { loadState(); }
  else if (sec === 'subs') loadState();
  else if (sec === 'diag') { loadState(); loadLog(); safe('run', loadRun); }
  else if (sec === 'cred') { loadState(); loadBili(); loadSecurity(); loadHarden(); }
  else if (sec === 'changelog') loadVersion();
  else if (sec === 'setup') { loadState(); }
  // 打开面板后做一次滚动渐入（内容长时一块块浮上来）
  setTimeout(afterSecOpen, 30);
  return true;
}

// 最近聚焦过的文案输入框 —— 快捷插入变量时用
let LAST_VAR_INPUT = null;

// 打开文案编辑弹层
//
// ⚠️ 用的是**独立容器** #tplSheet，不是主面板的 #sheetBody。
// 以前共用一个容器：往 sheetBody 里塞 innerHTML 会把所有功能页（.sec）
// 整块覆盖掉，于是打开过一次文案编辑后，再点任何功能都切不出内容 ——
// 表现就是"关不掉"，只能刷新页面。
function openSheet(title, html) {
  const sheet = $('tplSheet');
  if (!sheet || sheet._missing) return;
  const t = $('tplSheetTitle'), body = $('tplSheetBody');
  if (t && !t._missing) t.textContent = title;
  if (body && !body._missing) body.innerHTML = html;
  sheet.classList.add('show');
  sheet.setAttribute('aria-hidden', 'false');
  if (body && !body._missing) body.scrollTop = 0;
  LAST_VAR_INPUT = null;
  bindVarInputs();
  setTimeout(afterSecOpen, 30);
}

function closeSheet() {
  const sheet = $('tplSheet');
  if (!sheet || sheet._missing) return;
  sheet.classList.remove('show');
  sheet.setAttribute('aria-hidden', 'true');
  // 内容一并清掉：下次打开不会先闪一下上次的旧内容
  const body = $('tplSheetBody');
  if (body && !body._missing) body.innerHTML = '';
  LAST_VAR_INPUT = null;
}

// 返回概况（主面板的返回/✕、Esc）
// ⚠️ 新版主区是**常驻**的，不能再靠移除 show 来"关闭" ——
// 那会把整个主区藏掉，页面只剩侧边栏（看着就是白屏）。
// 所以这里不是"关闭"，而是跳回概况；顺手关掉可能开着的文案弹层。
function goHome() {
  // 顺手把可能还开着的文案弹层关掉，回到干净状态
  closeSheet();
  openSec('home');
}

// 文案弹层当前是否开着（Esc 要先关它，再关主面板）
function sheetOpen() {
  const sheet = $('tplSheet');
  return !!(sheet && !sheet._missing && sheet.classList.contains('show'));
}

// 记住光标最后所在的文案输入框，快捷插入变量时用它
function bindVarInputs() {
  const body = $('tplSheetBody');
  if (!body || body._missing) return;
  body.querySelectorAll('input.utpl-in, input.stpl-in').forEach(el => {
    el.addEventListener('focus', () => { LAST_VAR_INPUT = el; });
  });
}

// 在光标处插入文本（不是追加到末尾 —— 追加会让用户以为只能放最后）
function insertVar(text) {
  const el = LAST_VAR_INPUT;
  if (!el) { toast('先点一下要填的输入框，再点变量'); return; }
  const s = (el.selectionStart == null) ? el.value.length : el.selectionStart;
  const e = (el.selectionEnd == null) ? el.value.length : el.selectionEnd;
  el.value = el.value.slice(0, s) + text + el.value.slice(e);
  const pos = s + text.length;
  try {
    el.focus();
    el.setSelectionRange(pos, pos);
  } catch (err) { /* 某些浏览器不支持，忽略 */ }
  // 手动触发一次 input 事件，让"未保存"提示之类的逻辑能感知
  try { el.dispatchEvent(new Event('input', { bubbles: true })); } catch (err) {}
}

// 文案可用的变量。合集多两个（合集名、小节名）
//
// ⚠️ 两组都插到**光标位置**（不是追加到末尾），光标位置由
// LAST_VAR_INPUT 记录 —— 所以提示语要写清"先点输入框"。
// 快捷插入：按**这个类目实际能用的变量**生成按钮。
//
// ⚠️ 以前只给 {name} {title} 两个，想在开播提醒里写直播间号、分区、
// 人气都不行 —— 而且自己敲一个不支持的变量（比如 {room}）会让
// 整条消息发不出去。现在变量表由后端给出，每个类目各不相同。
//
// ⚠️ 样式以前是"丢的"，不是没写：
//    ① 容器只有 .row（样式表里根本没有 display:flex / gap），
//       而按钮是 join('') 拼的、**中间连个空格都没有** ——
//       于是所有按钮粘成一坨，连 .row.wrap 的 flex-wrap 都不生效；
//    ② 小标题「可用变量…」和按钮挤在同一行，被按钮顶到中间去。
//    现在改用专门的 .var-row（flex + gap），小标题独占一行。
function varButtons(vars, labelMap, phrases) {
  const list = (vars || []).filter(Boolean);
  // 变量：中文名 + 半透明的 {xxx} 代码，比全白一坨好认
  const varBtn = (v) =>
    '<button class="mini varbtn" data-act="ins-var" data-var="' + esc(v) + '">' +
    ((labelMap && labelMap[v]) ? esc(labelMap[v]) + ' ' : '') +
    '<span class="vc">' + esc(v) + '</span></button>';
  // 短语：整句都是要插进去的文本，不再套半透明
  const phrBtn = (p) =>
    '<button class="mini varbtn phr" data-act="ins-var" data-var="' + esc(p) + '">' +
    esc(p) + '</button>';
  const varHtml = list.length
    ? '<div class="var-row">' +
      '<span class="mini-label">可用变量（先点一下要填的输入框）</span>' +
      list.map(varBtn).join('') +
      '</div>'
    : '';
  const phraseHtml = (phrases && phrases.length)
    ? '<div class="var-row">' +
      '<span class="mini-label">常用短语</span>' +
      phrases.map(phrBtn).join('') +
      '</div>'
    : '';
  return varHtml + phraseHtml;
}

// 常用短语按类目给（不同类目说法不同）
const TPL_PHRASES = {
  // ⚠️ 第 4 个短语原来写「点击进入直播间」，但这个类目同时管开播和下播
  //    文案，下播了直播间已经关了、点进去什么都没有。改成通用的主页说法。
  live: ['【直播订阅】', '开播啦！记得看噢~', '已下播啦~', '点击访问主页'],
  video: ['【视频订阅】', '有新投稿', '记得看噢~', '点击观看'],
  dynamic: ['【动态订阅】', '发新动态了', '置顶了一条动态', '点击查看'],
  top_comment: ['【置顶评论】', '有新的置顶评论', '点击查看'],
  season: ['【合集更新】', '【新增视频】', '更新了', '点击观看'],
};

function initSheets() {
  // ⚠️ 宫格入口 .th-item 已随旧首页移除，改由侧边栏 .nav-i 切换板块
  //（走 data-act="goto-sec" 的事件委托，这里不用再单独绑）。
  document.querySelectorAll('.nav-i').forEach(btn => {
    btn.addEventListener('click', () => {
      const sec = btn.getAttribute('data-sec');
      if (sec) openSec(sec);
    });
  });
  // ESC：先关文案弹层，再关主面板（一次只关一层）
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (sheetOpen()) { closeSheet(); return; }
    goHome();
  });
}
function goto(sec) { return openSec(sec); }

/* 面板打开后让下面的区块错峰浮上来 */
function afterSecOpen() { safe('reveal', initReveal); }



/* ---------- 全局状态 ---------- */
let STATE = { groups: [], kinds: [], config: {}, templates: {} };
// 文案模板：输入框 id -> 配置键。9 项要和 notify.DEFAULT_TEMPLATES 对上，
// 之前只有 6 项，下播和置顶评论改不了；后来补到 8 项又漏了 season，
// 于是 #tSeason 输入框虽然在页面上显示，却既不回填也存不进去。
const TPL_FIELDS = [
  ['tLive', 'live'], ['tOffline', 'offline'], ['tVideo', 'video'],
  ['tDyn', 'dynamic'], ['tDynNo', 'dynamic_no_title'],
  ['tDynPin', 'dynamic_pinned'], ['tDynPinNo', 'dynamic_pinned_no_title'],
  ['tDynLive', 'dynamic_live'], ['tDynLiveNo', 'dynamic_live_no_title'],
  ['tTopCmt', 'top_comment'], ['tSeason', 'season'],
];
let LOOKUP = null;
/* 查询结果里顺带带回来的合集清单：
   「点 UP 主加 UP 主、点合集加合集」用的就是它。
   单独存一份，群下拉框换了要重画时不用再查一次。 */
let LOOKUP_SEASONS = [];
let LOOKUP_SERIES_N = 0;
let LOOKUP_SEA_ERR = '';
let LOG_DATA = null;
let BILI_TIMER = null;
const FORM_DIRTY = {};
const CFG_FIELDS = ['cAppid', 'cSecret', 'cPoll', 'cSlow',
                    'cSandbox', 'cSandbox2', 'cOffline', 'cImage', 'cImgMode',
                    'cSizeHint', 'cHintW', 'cHintH', 'cHintMaxH', 'cHintCell', 'cGridMode',
                    'cImgMax',
                    'cPlainMsg', 'cOnlyUpCmt', 'cSandboxGroup',
                    'cTimeout', 'cDedupe',
                    // ⚠️ 这几个以前**没登记**，后果是：改了不进 FORM_DIRTY，
                    //    loadState() 一刷就用后台值把输入框覆盖回去（用户正在填也会被冲掉）。
                    'cMinReqInt', 'cPollMax', 'cLogTrans', 'cDebugLog'];

/* 文案预设：整套一次套用。default = 留空（用 notify 内置默认文案）。 */
const TPL_PRESETS = {
  default: {
    live: '', offline: '', video: '', dynamic: '', dynamic_no_title: '',
    dynamic_pinned: '', dynamic_pinned_no_title: '', top_comment: '', season: '',
    dynamic_live: '', dynamic_live_no_title: '',
  },
  simple: {
    live: '{name} 开播了',
    offline: '{name} 下播了',
    video: '{name} 投稿了《{title}》',
    dynamic: '{name} 发了动态《{title}》',
    dynamic_no_title: '{name} 发了动态',
    dynamic_pinned: '{name} 置顶了《{title}》',
    dynamic_pinned_no_title: '{name} 置顶了一条动态',
    dynamic_live: '{name} 正在直播《{title}》',
    dynamic_live_no_title: '{name} 开播了',
    top_comment: '{name} 有新置顶评论',
    season: '{name} 的《{season}》更新了《{title}》',
  },
  detail: {
    live: '【直播】{name} 开播啦！\n标题：{title}\n快来围观~',
    offline: '【直播】{name} 已下播~',
    video: '【投稿】{name} 新视频《{title}》已发布，记得三连~',
    dynamic: '【动态】{name} 更新了《{title}》',
    dynamic_no_title: '【动态】{name} 更新了动态',
    dynamic_pinned: '【置顶动态】{name} 置顶了《{title}》',
    dynamic_pinned_no_title: '【置顶动态】{name} 置顶了一条动态',
    dynamic_live: '【直播】{name} 正在直播《{title}》',
    dynamic_live_no_title: '【直播】{name} 开播了，记得看噢~',
    top_comment: '【置顶评论】{name} 的《{title}》下有新置顶评论',
    season: '【合集】{name} 的《{season}》新增《{title}》',
  },
};



function updateStatusBar() {
  const alive = !!(STATE.heartbeat || {}).alive;
  const txt = $('liveText');
  if (txt && !txt._missing) {
    txt.textContent = !STATE.creds_ready ? '待填凭据'
      : (alive ? '运行中' : '未运行');
  }
}

/* ---------- 载入状态 ---------- */
async function loadState() {
  const d = await api('/api/state');
  if (!d.ok) return d;
  STATE = d;
  renderHero();
  renderStats();
  updateStatusBar();
  renderGuide();
  renderGroups();
  fillSelects();
  fillCfg();
  // v1.31.0：顶部状态条 / 沙盒群 / 健康度 / 待办 / 最近动态 / 测试 UID
  GROUP_NAME = {};
  (d.groups || []).forEach(function (g) { GROUP_NAME[g.gid] = g.name || g.gid; });
  safe('topbar', renderTopBar);
  safe('sandbox', syncSandbox);
  safe('health', renderHealth);
  safe('todo', renderTodo);
  safe('recent', renderRecent);
  safe('mirror', syncMirror);
  safe('chkuid', loadChkUid);
  safe('grplist', renderGrpList);
  // 订阅数据变了，"按 UP 主 / 按合集"的候选条目跟着变
  safe('subfilterpick', renderSubFilterPick);
  safe('backup', loadBackupInfo);
  safe('about', loadAbout);
  return d;
}

/* ---------- 数据与备份 ----------
   数据文件跟程序目录是分开的，用户不知道它到底在哪，
   也就没法主动保护。这里把路径、条目数、备份情况直接摆出来。 */
async function loadBackupInfo() {
  const pEl = document.getElementById('bkPath');
  if (!pEl) return;
  const d = await api('/api/data/info');
  let home = null;
  try { home = await api('/api/data/home'); } catch (e) { home = null; }
  if (home && home.ok) { d.home = home.home; d.source = home.source; d.images = home.images; }
  if (!d.ok) {
    pEl.textContent = '（读不到）';
    return;
  }
  pEl.textContent = d.path || '（未知）';
  const s = d.stats || {};
  const stEl = document.getElementById('bkStats');
  if (stEl) {
    stEl.innerHTML = d.exists
      ? `群 <b>${s.groups || 0}</b> 个 · 订阅 <b>${s.subs || 0}</b> 条 · `
        + `UP 主 <b>${s.ups || 0}</b> 位 · 合集 <b>${s.seasons || 0}</b> 个 · `
        + `文案 <b>${s.templates || 0}</b> 条`
        + (d.mtime ? `<br>最后写入：${d.mtime}` : '')
      : '（还没有数据文件，添加订阅后会自动生成）';
  }
  const hEl = document.getElementById('bkHomeNow');
  if (hEl) {
    const img = d.images || {};
    const fmt = (o) => o && o.n ? `${o.n} 张 / ${((o.size || 0) / 1024).toFixed(0)} KB` : '0 张';
    hEl.innerHTML = `当前：<b>${esc(d.home || '（未知）')}</b>`
      + `　·　来源：${d.source === 'env' ? '环境变量' : (d.source === 'pointer' ? '你自己选的' : '默认位置')}`
      + (img.dir ? `<br>图片目录：<b>${esc(img.dir)}</b>（头像 ${fmt(img.avatars)} · 封面 ${fmt(img.push)} · 界面图 ${fmt(img.ui)}）` : '');
    const inp = document.getElementById('bkHome');
    if (inp && !inp.value) inp.placeholder = d.home || '留空=默认位置';
  }
  const iEl = document.getElementById('bkInfo');
  if (iEl) {
    const full = d.full || [];
    const snap = d.snapshots || [];
    const newest = full[0];
    iEl.innerHTML = (newest ? `最近一次完整备份：<b>${esc(newest.name)}</b>`
                            : '还没有完整备份，建议现在点一次「立即备份」')
      + `　·　完整备份 ${full.length} 份 / 自动快照 ${snap.length} 份`;
  }
  /* 「💾 数据与备份」折起来时，摘要里给出数据位置和备份份数 ——
     最关心的两件事不用展开就能看到。 */
  const smEl = document.getElementById('bkCardSum');
  if (smEl) {
    const _dir = (d.path || '').replace(/[\\/]+$/, '');
    const _nm = _dir ? _dir.split(/[\\/]/).pop() : '';
    smEl.textContent = d.exists
      ? (_nm || '（未知）') + ' · 备份 ' + ((d.full || []).length) + ' 份'
      : '（还没有数据文件）';
  }
  loadLegacyInfo();
}

/* ---------- 关于 ----------
   反馈 bug 时最常被问"哪个版本 / 数据在哪 / 装了哪些依赖"，
   以前这些信息散在日志和配置文件里，得来回翻。这里一次性摆出来，
   并给一键复制，反馈时直接贴。 */
let _aboutCache = null;
async function loadAbout() {
  const vEl = document.getElementById('abVer');
  if (!vEl) return;
  const d = await api('/api/about');
  if (!d || !d.ok) {
    vEl.textContent = '（读不到）';
    return;
  }
  _aboutCache = d;
  const s = d.stats || {};
  vEl.innerHTML = `<b>v${esc(d.version || '')}</b>`
    + (d.started ? `　·　本次启动：${esc(d.started)}` : '')
    + (s.groups != null
        ? `<br>群 <b>${s.groups}</b> 个 · 订阅 <b>${s.subs || 0}</b> 条 · `
          + `UP 主 <b>${s.ups || 0}</b> 位 · 合集 <b>${s.seasons || 0}</b> 个`
        : '');
  const eEl = document.getElementById('abEnv');
  if (eEl) {
    eEl.innerHTML = `Python <b>${esc(d.python || '?')}</b>　·　`
      + `平台 <b>${esc(d.platform || '?')}</b>`;
  }
  const dEl = document.getElementById('abData');
  if (dEl) {
    const srcMap = { env: '环境变量指定', pointer: '你自己选的', '': '默认位置' };
    dEl.innerHTML = `数据目录：<b>${esc(d.data_dir || '（未知）')}</b>`
      + `（${esc(srcMap[d.home_source] || '默认位置')}）`
      + (d.state_path ? `<br>数据文件：<b>${esc(d.state_path)}</b>` : '')
      + (d.log_dir ? `<br>日志目录：<b>${esc(d.log_dir)}</b>` : '');
  }
  const pEl = document.getElementById('abDeps');
  if (pEl) {
    const deps = d.deps || {};
    const must = ['aiohttp', 'botpy', 'yaml', 'flask'];
    const opt = ['playwright'];
    const fmt = (ks) => ks.map(k => `${k} ${deps[k] ? '✅' : '❌'}`).join('　');
    pEl.innerHTML = `必需：${fmt(must)}<br>可选：${fmt(opt)}`
      + '<br><span class="hint">必需项缺了会导致启动失败，跑一次「安装依赖」；'
      + '可选项只影响浏览器登录（装不了就用手动粘贴 Cookie）</span>';
  }
}

/* 旧版 data.db —— v1.92 及更早用 SQLite 文件库存订阅，新版落盘换成了 JSON。
   ⚠️ 块注释一定要成对闭合：曾经这里只写了开头没写结尾，把下面的
      loadLegacyInfo / renderHero 整段吞进注释，页面点刷新就报
      "renderHero is not defined"（tests/test_js_comment_balance.py 盯着这条）。 */
async function loadLegacyInfo() {
  const el = document.getElementById('lgInfo');
  if (!el) return;
  const box = document.getElementById('lgList');
  let r = null;
  try { r = await api('/api/data/legacy'); } catch (e) { r = null; }
  if (!r || !r.ok) {
    el.textContent = '（读不到）';
    if (box) box.innerHTML = '';
    return;
  }
  const items = r.items || [];
  if (!items.length) {
    el.textContent = '没有发现旧版数据文件 —— 把旧版的 data.db 放进程序文件夹，'
                   + '重启面板或刷新这里就会出现';
    if (box) box.innerHTML = '';
    return;
  }
  el.innerHTML = `发现 <b>${items.length}</b> 个旧数据文件（导入是合并，不是覆盖）`;
  if (!box) return;
  box.innerHTML = items.map((it) => {
    const sm = it.summary || {};
    const kb = ((it.size || 0) / 1024).toFixed(0);
    return '<div class="row"><label>' + esc(it.name) + '</label>'
      + `<div class="hint">群 <b>${sm.groups || 0}</b> · 订阅 <b>${sm.subs || 0}</b> · `
      + `合集 <b>${sm.seasons || 0}</b> · UP <b>${sm.ups || 0}</b> · `
      + `文案 <b>${sm.templates || 0}</b>　（${kb} KB）</div>`
      + (it.imported
         ? '<div class="hint">✅ 这份已经导入过了</div>'
         : '<div class="btns"><button class="btn" data-act="legacy-import" '
           + 'data-path="' + escAttr(it.path) + '">📥 合并进来</button></div>')
      + '</div>';
  }).join('');
}

function renderHero() {
  const hb = STATE.heartbeat || {};
  const alive = !!hb.alive;
  const ready = !!STATE.creds_ready;
  const box = $('heroBox');
  // ⚠️ Hero 下面原本还有一行状态条，把「运行中 / 最后心跳 / 群数 / 订阅数 /
  //    轮询间隔」挤在一行里。概况页的指标卡和健康度里这些数字全都另有显示，
  //    它是纯冗余，v1.64.0 整行移除，这里也不再赋值。
  //    （注释里不再写那几个元素名：之前踩过"注释提到旧名字被朴素断言误判
  //      成还在用"的坑，不重复。）
  // v1.65.0：顶栏右侧那几个徽章（订阅数 / 已填 / 已登录）已移除 ——
  //   订阅数写死不更新、凭据与 B站状态与「⚙️ 凭据」页完全重复，只保留刷新。

  let emoji, title, desc, cls = '';
  if (!ready) {
    emoji = '🐣'; cls = 'warn';
    title = '还差一步：填写 AppID';
    desc = '机器人还没拿到凭据。在「⚙️ 设置」里填上 AppID 和 AppSecret，' +
           '保存后这里会自动变成在线。';
  } else if (alive) {
    emoji = '🎉';
    title = '机器人运行中';
    var _iv = pollActual(hb) ||
              parseInt((STATE.config || {}).poll_interval || 0, 10) || 0;
    desc = hb.detail ||
      '一切正常，正在每 ' + (_iv || '设定') + ' 秒检查一轮开播 / 投稿 / 动态。';
  } else {
    emoji = '😴'; cls = 'err';
    title = '机器人没在运行';
    desc = (hb.detail || '还没收到心跳') +
           '。确认是用「启动机器人.bat」或 python start.py 拉起的；' +
           '机器人启动后需要几秒才会显示在线。';
  }
  box.innerHTML =
    '<div class="hero-emoji">' + emoji + '</div>' +
    '<div class="hero-text"><div class="hero-title">' + esc(title) + '</div>' +
    '<div class="hero-desc">' + esc(desc) + '</div></div>';
  box.className = 'hero' + (cls ? ' ' + cls : '');
}

/* 运行时长：heartbeat.started_at 是 unix 秒 */
function fmtUptime(ts) {
  const t = Number(ts || 0);
  if (!t) return '';
  const s = Math.max(0, Math.floor(Date.now() / 1000 - t));
  if (s < 60) return s + ' 秒';
  if (s < 3600) return Math.floor(s / 60) + ' 分钟';
  if (s < 86400) return Math.floor(s / 3600) + ' 小时';
  return Math.floor(s / 86400) + ' 天';
}

function renderStats() {
  /* ⚠️ 结构和口径都对齐最终版：
     四张卡分别是 机器人 / 登记群 / 订阅 / 近期错误，
     每张都是 .kpi.jump（整张可点，点了跳到对应选项卡），
     内部用 .lb（名称）/ .vl（数值）/ .sb（一行小字 + › 提示）。
     以前用的是另一套 .kpi-v / .kpi-k（只在旧 style.css 里有），
     跟最终版的 .kpi 样式对不上，卡片看着和完整版不一样，也不能点。 */
  const g = STATE.groups || [];
  const hb = STATE.heartbeat || {};
  const alive = !!hb.alive;
  /* ⚠️ 订阅数必须**去重**后统计。
     同一个 UP 主/合集常被好几个群订阅，直接把每个群的订阅条数加起来，
     N 个群订同一个 UP 就数成 N 条 —— 卡片上写着 10，实际可能只有 4 个。

     ⚠️ 口径以后端 sub_stats 为准（Store.sub_stats），后端跟机器人的
        all_ups() 是同一套：
        1) 合集按 season_id 去重（B 站合集 ID 全局唯一）。以前按
           "uid:season_id" 拼键，某个群没存 uid（0）就变成 ':3175959'
           和 '123:3175959' 两个 —— 一个合集数成两个。
        2) 非数字 uid 不算 UP 主（以前算，于是面板 7 位、日志 6 位）。
     下面这份本地兜底只在拿不到 sub_stats 时用，规则与后端保持一致。 */
  const st = STATE.sub_stats;
  let nUp, nSea;
  if (st && (st.ups != null || st.seasons != null)) {
    nUp = Number(st.ups || 0);
    nSea = Number(st.seasons || 0);
  } else {
    const upSet = new Set(), seaSet = new Set();
    g.forEach(x => {
      (x.subs || []).forEach(s => {
        const u = parseInt(String((s && s.uid) != null ? s.uid : ''), 10);
        if (u > 0) upSet.add(u);          // 与后端一致：非数字 / 0 都不算
      });
      (x.seasons || []).forEach(sv => {
        if (!sv) return;
        /* ⚠️ 键用**字符串**而不是 parseInt：
           parseInt 只认纯数字，ID 里只要带一点非数字（界面上偶尔出现的
           写法、或者后端返回了字符串）就变成 NaN 被整条丢掉 ——
           合集在卡片上数成 0，但下面的群里明明有。
           数字型 ID 转字符串后依然唯一，去重效果一样；
           同时跟 subFilterItems / subFilterMatch 保持同一个键，
           「按合集筛群」选中的条目才对得上。 */
        const sid = String(sv.season_id == null ? '' : sv.season_id).trim();
        if (sid && sid !== '0') seaSet.add(sid);
      });
    });
    nUp = upSet.size; nSea = seaSet.size;
  }
  const errN = ERR_RECENT || 0;
  const pushed = (hb.pushes != null ? hb.pushes : (hb.pushed || 0));
  const up = fmtUptime(hb.started_at);
  const items = [
    { lb: '机器人', vl: alive ? '在线' : '未运行', cls: alive ? 'ok' : '',
      sb: (alive ? '已运行 ' + (up || '刚刚') : (hb.detail || '没收到心跳')) +
          (pushed ? ' · 已推 ' + pushed + ' 条' : '') + ' ›',
      sec: 'diag', tab: 'run' },
    { lb: '登记群', vl: g.length, cls: '', sb: '点击去管理 ›',
      sec: 'subs', tab: 'grp' },
    { lb: '订阅', vl: nUp + nSea, cls: '',
      sb: 'UP 主 ' + nUp + ' · 合集 ' + nSea
          + (g.length > 1 ? '（已去重 · ' + g.length + ' 个群）' : '')
          + ' ›', sec: 'subs', tab: 'list' },
    { lb: '近期错误', vl: errN, cls: errN ? 'wn' : '', sb: '最近 1 小时 ›',
      sec: 'diag', tab: 'log' },
  ];
  const box = $('statsBox');
  if (!box || box._missing) return;
  const first = !box.dataset.ready;
  box.innerHTML = items.map(x =>
    '<div class="kpi' + (x.cls ? ' ' + x.cls : '') + ' jump"' +
    ' data-act="goto-tab" data-sec="' + x.sec + '" data-tab="' + x.tab + '">' +
    '<div class="lb">' + esc(x.lb) + '</div>' +
    '<div class="vl"' + (typeof x.vl === 'number' ? ' data-n="' + x.vl + '"' : '') + '>' +
      '<span class="kpi-num">' + esc(String(x.vl)) + '</span></div>' +
    '<div class="sb">' + esc(x.sb) + '</div></div>').join('');
  box.dataset.ready = '1';
  if (first) return;                      // 第一次渲染不滚，避免开页面就抖
  box.querySelectorAll('.vl[data-n]').forEach(el => {
    countUp(el.querySelector('.kpi-num'), Number(el.getAttribute('data-n')));
  });
}

function renderGuide() {
  const box = $('guideBox');
  if (STATE.creds_ready) { box.innerHTML = ''; return; }
  box.innerHTML =
    '<div class="card" style="border-color:var(--accent)">' +
    '<h2>👋 第一次使用？</h2>' +
    '<div class="hint-block">' +
    '按顺序做两件事就能跑起来：<br>' +
    '<b>1.</b> 填 AppID / AppSecret（QQ 开放平台 → 机器人 → 开发设置）<br>' +
    '<b>2.</b> 在群里 @一下机器人，群就会自动登记，然后来「💖 订阅」添加 UP 主' +
    '</div>' +
    '<button class="btn-primary" data-act="goto-setup">👉 现在去填写</button>' +
    '</div>';
}

function fillSelects() {
  const opts = (STATE.groups || []).map(g => {
    // 没拿到名字时别只显示一截 ID —— 标成"未命名"更好认
    const label = g.name ? g.name : ('未命名群 ' + g.gid.slice(0, 8));
    const n = (g.subs || []).length;
    return '<option value="' + esc(g.gid) + '">' + esc(label) +
           (n ? ' · 已订 ' + n : '') + '</option>';
  }).join('');
  ['addGroup'].forEach(id => {
    const el = $(id), cur = el.value;
    el.innerHTML = opts || '<option value="">（还没有群）</option>';
    if (cur && (STATE.groups || []).some(g => g.gid === cur)) el.value = cur;
    /* ⚠️ 两个「目标群」下拉（添加订阅 / 订阅合集）其实是同一个意思，
       以前各管各的：上面选了 A 群，下面可能还停在 B 群，
       在结果里点合集就订到了别的群。现在改一个另一个跟着变。
       换群还要重画一次结果清单 —— 已订过的标记属于具体某个群。 */
    if (!el._lkBound) {
      el._lkBound = true;
      el.addEventListener('change', () => {
        const sgEl = $('seasonGroup');
        if (sgEl && !sgEl._missing) sgEl.value = el.value;
        renderLookupPick();
      });
    }
  });
  ['migSrc', 'migDst', 'seasonGroup'].forEach(id => {
    const el = $(id);
    if (!el || el._missing) return;
    const cur = el.value;
    el.innerHTML = opts || '<option value="">（还没有群）</option>';
    if (cur && (STATE.groups || []).some(g => g.gid === cur)) el.value = cur;
    // 换了源群就要重画可勾选清单（勾选状态本来就属于源群）
    if (id === 'seasonGroup' && !el._lkBound) {
      el._lkBound = true;
      el.addEventListener('change', () => {
        const ag = $('addGroup');
        if (ag && !ag._missing) ag.value = el.value;
        renderLookupPick();
      });
    }
    if (id === 'migSrc' && !el._migBound) {
      el._migBound = true;
      el.addEventListener('change', () => {
        if (MIG_SCOPE === 'part') renderMigPick();
      });
    }
  });
  /* ⚠️ loadState 会周期性跑，无条件重画会把用户刚勾好的东西清掉
     —— 只在源群真的变了时才重画。 */
  if (MIG_SCOPE === 'part') {
    const se = $('migSrc');
    const gid = se && se.value;
    if (gid !== renderMigPick._last) {
      renderMigPick._last = gid;
      renderMigPick();
    }
  }
  // 沙盒群：多一个"不启用"的选项，所以单独拼
  const sg = $('cSandboxGroup');
  if (sg && !sg._missing) {
    const cur = sg.value || ((STATE.config || {}).sandbox_group || '');
    sg.innerHTML = '<option value="">不启用（测试时手动选群）</option>' +
      (STATE.groups || []).map(g => {
        const label = g.name ? g.name : ('未命名群 ' + g.gid.slice(0, 8));
        return '<option value="' + esc(g.gid) + '">' + esc(label) + '</option>';
      }).join('');
    sg.value = cur;
  }
}

/* ---------- 订阅迁移：全体 / 选择 的公共部分 ----------
   全体 = 整群照搬；选择 = 只搬勾中的「UP 主 × 提醒类型」。
   ⚠️ 选择状态下源群只移除搬走的那几项，绝不清空整群。 */
var MIG_SCOPE = 'all';
var MIG_SEASONS = true;
function migSrcGroup() {
  var el = $('migSrc');
  var gid = el && el.value;
  if (!gid) return null;
  return (STATE.groups || []).find(function (g) { return g.gid === gid; }) || null;
}
function migKindName(k) {
  return ({ live: '直播', video: '投稿', dynamic: '动态',
            top_comment: '置顶评论' })[k] || k;
}
/* 迁移清单当前停在哪一栏（up / season）——「全选 / 清空」只作用于当前这一栏，
   不然在 UP 主那栏点「清空」会把合集那栏的勾选也抹掉。 */
function migActiveTab() {
  var t = document.querySelector('#migPick .mini-tab.on');
  return (t && t.getAttribute('data-mv') === 'mv-pick-season') ? 'season' : 'up';
}
/* 渲染可勾选的源群订阅清单。
   ⚠️ 勾的是「UP 主 × 提醒类型」，不是整位 UP 主 ——
      只想搬某个 UP 的直播、不想搬投稿，是常见需求。 */
/* 迁移清单分两栏：👤 UP 主 / 📦 合集（跟「群与订阅」里群卡片的 mini-tab 一样）。
   ⚠️ 层级必须清楚：
     外层**大卡片** = 选中（点整块切换，未选中整块变灰）
     内层**小卡片** = 这位 UP 要开哪些提醒
     大卡片没选中时，小卡片**点不动** —— 否则会出现"看着没选却悄悄改了提醒"。 */
function renderMigPick() {
  var box = $('migPickList');
  if (!box || box._missing) return;
  var se = $('migSrc');
  renderMigPick._last = se && se.value;   // 换源群才重画，别清掉勾选
  var g = migSrcGroup();
  var subs = (g && g.subs) || [];
  var seas = (g && g.seasons) || [];
  if (!subs.length) {
    box.innerHTML = '<div class="hint">这个群还没有订阅任何 UP 主</div>';
    renderMigSea();
    updateMigStat();
    return;
  }
  box.innerHTML = subs.map(function (s) {
    var nSea = seas.filter(function (x) {
      return String(x.uid) === String(s.uid);
    }).length;
    var any = ['live', 'video', 'dynamic', 'top_comment'].some(function (k) {
      return !!(s.kinds && s.kinds[k] && s.kinds[k].on);
    });
    var chips = ['live', 'video', 'dynamic', 'top_comment'].map(function (k) {
      var on = !!(s.kinds && s.kinds[k] && s.kinds[k].on);
      return '<button class="chip-tgl' + (on ? ' on' : '') +
        '" data-act="mig-chip" data-uid="' + esc(s.uid) +
        '" data-kind="' + k + '">' + esc(migKindName(k)) + '</button>';
    }).join('');
    // 默认选中：源群里这位 UP 至少开着一类提醒，才预先选上
    return '<div class="mig-card' + (any ? ' on' : '') + '"' +
      ' data-act="mig-card" data-uid="' + esc(s.uid) + '">' +
      '<div class="li">' +
      '<div class="av" data-name="' + esc(s.uname || '?') + '"' +
        (s.face ? ' data-face="' + esc(s.face) + '"' : '') + '></div>' +
      '<div class="bd"><div class="t1">' + esc(s.uname || ('UID ' + s.uid)) +
        '</div><div class="t2">UID ' + esc(String(s.uid)) +
        (nSea ? ' · 该 UP 有 ' + nSea + ' 个合集' : '') + '</div></div>' +
      '</div>' +
      '<div class="mig-kinds">' + chips + '</div>' +
      '</div>';
  }).join('');
  renderMigSea();
  try { initAvatars(); } catch (e) { /* 头像画不出来不影响勾选 */ }
  updateMigStat();
}

/* 合集那一栏：每个合集一张大卡，勾了才会搬。
   ⚠️ 合集只有"订/没订"一种状态，没有提醒类型可分，
   所以里面没有小卡片 —— 点整块就是选中。 */
function renderMigSea() {
  var box = $('migSeaList');
  if (!box || box._missing) return;
  var g = migSrcGroup();
  var seas = (g && g.seasons) || [];
  if (!seas.length) {
    box.innerHTML = '<div class="hint">这个群还没有订阅任何合集</div>';
    return;
  }
  if (!MIG_SEASONS) {
    box.innerHTML = '<div class="hint">上面「一并带走合集订阅」是关的，' +
      '打开之后才能在这里挑要搬哪几个合集。</div>';
    return;
  }
  box.innerHTML = seas.map(function (s) {
    var nm = s.uname || ('UID ' + s.uid);
    return '<div class="mig-card on" data-act="mig-sea-card"' +
      ' data-uid="' + esc(s.uid) + '" data-sid="' + esc(s.season_id) + '">' +
      '<div class="li">' +
      '<div class="av season-av" data-name="📦"' +
      (seasonFace(s)
        ? ' data-face="' + esc(proxyImg(seasonFace(s))) + '"' : '') + '></div>' +
      '<div class="bd"><div class="t1">📦 ' +
        esc(s.title || ('合集 ' + s.season_id)) + '</div>' +
      '<div class="t2">' + esc(nm) + ' · 合集 ID ' + esc(String(s.season_id)) +
      '</div></div></div>' +
      '</div>';
  }).join('');
}
function updateMigStat() {
  var el = $('migPickStat');
  if (!el || el._missing) return;
  var picks = collectMigPicks();
  var sp = collectMigSeasons();
  if (!picks.length && !sp.length) { el.textContent = ''; return; }
  var m = picks.reduce(function (a, p) { return a + p.kinds.length; }, 0);
  var t = '　已选';
  if (picks.length) t += ' ' + picks.length + ' 位 UP 主 / ' + m + ' 项提醒';
  if (sp.length) t += (picks.length ? '，' : ' ') + sp.length + ' 个合集';
  el.textContent = t;
}
function collectMigPicks() {
  var map = {};
  var cards = document.querySelectorAll('#migPickList .mig-card.on');
  for (var i = 0; i < cards.length; i++) {
    var uid = cards[i].getAttribute('data-uid');
    if (!uid) continue;
    var ks = [];
    var cs = cards[i].querySelectorAll('.chip-tgl.on');
    for (var j = 0; j < cs.length; j++) {
      var k = cs[j].getAttribute('data-kind');
      if (k) ks.push(k);
    }
    // 卡片选中了但小卡片一个都没开 = 这位 UP 实际上没东西可搬
    if (ks.length) map[uid] = ks;
  }
  return Object.keys(map).map(function (u) {
    return { uid: u, kinds: map[u] };
  });
}
function collectMigSeasons() {
  if (!MIG_SEASONS) return [];
  var out = [];
  var cards = document.querySelectorAll('#migSeaList .mig-card.on');
  for (var i = 0; i < cards.length; i++) {
    var sid = cards[i].getAttribute('data-sid');
    if (sid) out.push(sid);
  }
  return out;
}
/* 组装请求体；返回 null 表示前端就该拦下（已经提示过用户了） */
function migExtra() {
  if (MIG_SCOPE !== 'part') return { scope: 'all', seasons: MIG_SEASONS };
  var picks = collectMigPicks();
  var sp = collectMigSeasons();
  // 允许"只搬合集"：以前 picks 为空就直接拒绝，于是想只搬几个合集做不到
  if (!picks.length && !sp.length) {
    showMsg('migMsg', '选择迁移要先勾点东西（至少一位 UP 主的一类提醒，或一个合集）',
            'err');
    return null;
  }
  return { scope: 'part', picks: picks, season_picks: sp, seasons: MIG_SEASONS };
}

/* ---------- 群与订阅渲染 ---------- */
const NOTE_LABEL = {
  live: '直播', video: '投稿', dyn: '动态',
  dyn_top: '置顶动态', top_cmt: '置顶评论'
};
function lastNote(s) {
  // 机器人每轮检测的结果。失败时会显示原因，
  // 以前这些都只写在日志里，用户只能看到"没提示"却不知道为什么。
  const last = s.last || {};
  const parts = [];
  for (const k of ['live', 'video', 'dyn']) {
    const t = last[k];
    if (!t) continue;
    const bad = /失败|报错|没查到|没取到|风控|-352/.test(t);
    parts.push('<div class="uid ' + (bad ? 'note-bad' : '') + '">'
      + NOTE_LABEL[k] + '：' + esc(String(t).slice(0, 46)) + '</div>');
  }
  return parts.join('');
}

/* 「➕ 添加订阅 → 开启提醒」四个按钮的读写。
   兼容旧的 checkbox 写法（万一页面还是旧版，也不至于读成 false）。 */
function defKindOn(id, def) {
  const el = $(id);
  if (!el || el._missing) return !!def;
  if (el.classList && el.classList.contains('on')) return true;
  if (typeof el.checked === 'boolean') return el.checked;
  return !!def;
}

function setDefKind(id, on) {
  const el = $(id);
  if (!el || el._missing) return;
  if (el.classList) el.classList.toggle('on', !!on);
  if (typeof el.checked === 'boolean') el.checked = !!on;
}

/* 「💬 登记群」里某个群该显示哪张头像。
   ⚠️ 必须**跟着「群与订阅」里选的那张走**（🖼 选的 UP 主，记住的是 uid），
      否则两边不一致：那边换了头像，这边还是"第一个 UP 主"的，
      看着像两个群。选过就用选的，没选过才退回第一个有头像的订阅。 */
function grpFaceOf(g) {
  let uid = '';
  try { uid = localStorage.getItem('gbg:' + g.gid) || ''; } catch (e) { uid = ''; }
  if (uid) {
    const s = (g.subs || []).find(x => String(x.uid) === String(uid));
    if (s && s.face) return s.face;
  }
  const first = (g.subs || []).find(s => s && s.face);
  return first ? first.face : '';
}

/* 「💬 登记群」里的已登记群列表。
   和「群与订阅」用同一套头像：群里选的那个 UP 主的头像，
   没有订阅就画一个带问号的群图标 —— 一眼能认出是哪个群。 */
function renderGrpList() {
  const box = $('grpListBox');
  if (!box || box._missing) return;
  const gs = STATE.groups || [];
  if (!gs.length) {
    box.innerHTML = '<div class="hint">还没有登记任何群。</div>';
    return;
  }
  box.innerHTML = gs.map(g => {
    const label = g.name || ('未命名群 ' + String(g.gid).slice(0, 8));
    const _f = grpFaceOf(g);
    const face = _f ? proxyImg(_f) : '';
    const nUp = (g.subs || []).length;
    const nSea = (g.seasons || []).length;
    return '<div class="li gl-li">' +
      '<div class="av grp-av" data-name="' + esc(label) + '"' +
      (face ? ' data-face="' + esc(face) + '"' : '') + '></div>' +
      '<div class="bd"><div class="t1">' + esc(label) + '</div>' +
      '<div class="t2">' + esc(String(g.gid).slice(0, 16)) + '…</div></div>' +
      '<span class="gl-tag">' + nUp + ' UP · ' + nSea + ' 合集</span>' +
      '<span class="gl-ok">正常</span>' +
      '<button class="btn dg" data-act="remove-group" data-gid="'
      + esc(g.gid) + '">🗑️</button>' +
      '</div>';
  }).join('');
  initAvatars();
}

/* ---------- 订阅筛选：按「已订阅的 UP 主 / 合集」反查是哪些群订了它 ----------
   群多了以后，"这个 UP 主到底订在哪几个群里"只能一个个群点开翻。
   这里把所有已订阅条目去重列成一张表（顺带标出订了它的群有几个），
   挑一个就筛出对应的群。 */
function subFilterKindVal() {
  const el = $('subFilterKind');
  return (el && !el._missing) ? String(el.value || 'all') : 'all';
}

function subFilterPickVal() {
  const el = $('subFilterPick');
  return (el && !el._missing) ? String(el.value || '') : '';
}

/* 筛选是否生效（生效时即使搜索框为空也要过滤） */
function subFilterActive() {
  const k = subFilterKindVal();
  return k === 'up' || k === 'season' || (k === 'name' && !!subSearchKw());
}

/* 已订阅条目清单：同一条目被多个群订阅只列一次，并数出订了它的群有几个 */
function subFilterItems(kind) {
  const m = new Map();
  (STATE.groups || []).forEach(g => {
    if (kind === 'up') {
      (g.subs || []).forEach(s => {
        if (!s) return;
        const k = String(s.uid || '');
        // 非数字 uid / 0 是坏数据（跟后端 all_ups 一个口径），不列进下拉
        if (!k || !/^\d+$/.test(k) || Number(k) <= 0) return;
        const it = m.get(k) || { key: k, name: String(s.uname || k),
                                 sub: 'UID ' + k, n: 0 };
        it.n += 1;
        m.set(k, it);
      });
    } else if (kind === 'season') {
      (g.seasons || []).forEach(sv => {
        if (!sv) return;
        /* ⚠️ 键只用 season_id：同一个合集在某个群里没存 uid 时，
           "uid:season_id" 会把它拆成两条（见 subFilterMatch）。 */
        const sid = String(sv.season_id || '');
        if (!sid) return;
        const k = sid;
        const it = m.get(k) || {
          key: k,
          name: String(sv.title || ('合集 ' + sid)),
          sub: String(sv.uname || ('UID ' + String(sv.uid || '')))
               + ' · ID ' + sid,
          n: 0 };
        it.n += 1;
        m.set(k, it);
      });
    }
  });
  return Array.from(m.values()).sort((a, b) =>
    String(a.name).localeCompare(String(b.name), 'zh'));
}

/* 填「选择条目」下拉。切了筛选方式或数据变了都要重填。 */
function renderSubFilterPick() {
  const row = $('subFilterPickRow');
  const sel = $('subFilterPick');
  const lbl = $('subFilterPickLabel');
  if (!row || row._missing || !sel || sel._missing) return;
  const kind = subFilterKindVal();
  if (kind !== 'up' && kind !== 'season') { row.hidden = true; return; }
  row.hidden = false;
  if (lbl && !lbl._missing) {
    lbl.textContent = (kind === 'up') ? '选择 UP 主' : '选择合集';
  }
  const items = subFilterItems(kind);
  const old = subFilterPickVal();
  if (!items.length) {
    sel.innerHTML = '<option value="">（还没有订阅）</option>';
    return;
  }
  sel.innerHTML = '<option value="">（不限 · 所有订了'
      + (kind === 'up' ? 'UP 主' : '合集') + '的群）</option>'
    + items.map(it => '<option value="' + esc(it.key) + '">'
        + esc(it.name) + '（' + esc(it.sub) + '）· ' + it.n + ' 个群'
        + '</option>').join('');
  // 之前选的那个还在就保留，否则落到第一个具体条目上（切过来就能用）
  if (old && items.some(it => it.key === old)) sel.value = old;
  else sel.value = items[0].key;
}

/* ---------- 设置页的折叠卡片：记住哪些块是展开的 ----------
   「🖼️ 图片」「💾 数据与备份」参数多，默认收起让设置页不至于拖很长。
   ⚠️ 但"默认收起"不能每次进页面都收一次：用户展开看完，切个选项卡回来
   又合上了，会以为点不动。所以把每块的开合记在本机，下次进来照旧。 */
const FOLD_KEY = 'biliFoldCard';

function foldReadMap() {
  try {
    const m = JSON.parse(localStorage.getItem(FOLD_KEY) || '{}');
    return (m && typeof m === 'object') ? m : {};
  } catch (e) { return {}; }
}

/* 折叠卡片：展开 / 收起给一声很轻的气声。
   ⚠️ 恢复上次展开状态（m[d.id] 为真时 d.open = true）也会触发 toggle，
      页面一打开就"哗啦"响一片。用这个标志把初始化那次静音掉。 */
let FOLD_SFX_READY = false;

function initFoldCards() {
  const m = foldReadMap();
  const ds = document.querySelectorAll('details.card.fold');
  Array.prototype.forEach.call(ds, d => {
    if (!d.id) return;
    if (m[d.id]) d.open = true;
    if (d._foldBound) return;
    d._foldBound = true;
    d.addEventListener('toggle', () => {
      const mm = foldReadMap();
      mm[d.id] = !!d.open;
      try { localStorage.setItem(FOLD_KEY, JSON.stringify(mm)); } catch (e) { /* 存不了就只在本次有效 */ }
      if (FOLD_SFX_READY) sfx(d.open ? 'unfold' : 'fold');
    });
  });
  /* toggle 是异步派发的，等它排完队再放开音效 */
  setTimeout(() => { FOLD_SFX_READY = true; }, 0);
}

/* 群是否符合当前筛选 */
function subFilterMatch(g) {
  const kind = subFilterKindVal();
  const pick = subFilterPickVal();
  if (kind === 'up') {
    const subs = g.subs || [];
    if (!subs.length) return false;
    return !pick || subs.some(s => String(s.uid || '') === pick);
  }
  if (kind === 'season') {
    const sv = g.seasons || [];
    if (!sv.length) return false;
    /* ⚠️ 键只用 season_id（B 站合集 ID 全局唯一）。以前拼 "uid:season_id"，
       某个群没存 uid 就成了 ':3175959'，跟 '123:3175959' 不是一个键 ——
       同一个合集在下拉里列两遍，选哪个都只能筛出一半的群。 */
    return !pick || sv.some(x => String(x.season_id || '') === pick);
  }
  return true;
}

/* 群列表搜索：按群名 / UP 主名 / UID / 合集名过滤。
   命中 UP 主时自动展开所在群，不用再一个个点开找。 */
function subMatch(g, kw) {
  if (!subFilterMatch(g)) return false;
  const kind = subFilterKindVal();
  if (!kw) return true;
  const k = kw.toLowerCase();
  // 「只按群名或群号」时不再拿 UP 主名/合集名去比，免得关键词蹭中
  // 订阅项就把整群都捞出来
  if (kind === 'name') {
    return String(g.name || '').toLowerCase().indexOf(k) >= 0
      || String(g.gid || '').toLowerCase().indexOf(k) >= 0;
  }
  if (String(g.name || '').toLowerCase().indexOf(k) >= 0) return true;
  if (String(g.gid || '').toLowerCase().indexOf(k) >= 0) return true;
  const inSubs = (g.subs || []).some(s =>
    String(s.uname || '').toLowerCase().indexOf(k) >= 0
    || String(s.uid || '').indexOf(k) >= 0);
  if (inSubs) return true;
  return (g.seasons || []).some(sv =>
    String(sv.title || '').toLowerCase().indexOf(k) >= 0
    || String(sv.uname || '').toLowerCase().indexOf(k) >= 0
    || String(sv.uid || '').indexOf(k) >= 0);
}

function subSearchKw() {
  const el = $('subSearch');
  return (el && !el._missing) ? String(el.value || '').trim() : '';
}

/* ⚠️ 每 15 秒 loadState() 都会调 renderGroups() 整块重画，
   于是：正在输入搜索框 → 被打断；展开的群被收起；滚动位置跳回顶部。
   这就是反馈里的「页面老是会自己刷新」。
   先算一个指纹，内容没变就**根本不重画**；确实要重画时也保住滚动位置。 */
function groupsSig() {
  try {
    /* ⚠️ 指纹里**必须带上展开状态**。
       只比较 groups 数据时，点群卡片改的是 EXPANDED，数据没变
       → 指纹相同 → 直接 return，卡片**永远打不开**（反馈「卡片打不开了」）。
       搜索关键词同理：只改 kw 不改数据时也要重画。 */
    return JSON.stringify(STATE.groups || [])
      + '#open:' + Array.from(EXPANDED || []).sort().join(',')
      /* ⚠️ 筛选方式/选中条目也要进指纹：它们不改变 groups 数据，
         只改 EXPANDED 之外的显示结果。不带上就会出现
         「换了筛选但列表纹丝不动」。 */
      + '#flt:' + subFilterKindVal() + '/' + subFilterPickVal();
  } catch (e) { return String(Date.now()); }
}

/* 「📊 当前数据」的详细内容：鼠标移到那个小标签上才浮出来。
   短直播间号单独列一行并说明取没取到 —— 它是直播提醒能不能
   正常反查房间号的关键，以前藏在 "原：x　短：y" 里根本看不出来。 */
function curDataTip(s) {
  const L = [];
  L.push('UID：' + String(s.uid || ''));
  const a = parseInt(s.room_id || 0, 10) || 0;
  const b = parseInt(s.short_room_id || 0, 10) || 0;
  const bOk = b && b !== a;
  L.push('直播间长号：' + (a || '未取到'));
  L.push('短号：' + (bOk ? b : '未取到')
    + (bOk ? '' : '（开播时会自动反查）'));
  const last = s.last || {};
  const NAME = { live: '直播', video: '投稿', dyn: '动态' };
  let any = false;
  for (const k of ['live', 'video', 'dyn']) {
    const t = last[k];
    if (!t) continue;
    any = true;
    /* ⚠️ 直播那一行历史上存过 "live_status=2" 这种原始值，
       直接铺出来就是一串谁也看不懂的等号 —— 翻成中文再显示。 */
    const v = (k === 'live') ? liveStatusCn(String(t)) : String(t);
    L.push(NAME[k] + '：' + v.slice(0, 60));
  }
  if (!any) L.push('（还没跑过检测）');
  return L.join('\n');
}

/* 合集的「📊 当前数据」详情。
   名字没取到时明确说明怎么补，别让人对着一串 uid 猜。 */
function seasonTip(sv, owner) {
  const L = [];
  if (owner) {
    L.push('UP 主：' + owner);
  } else {
    L.push('UP 主：没取到（名字是空的）');
    L.push('到「💬 登记群 → 🔄 刷新群列表」会自动补回来');
  }
  L.push('UID：' + String(sv.uid));
  L.push('合集 ID：' + String(sv.season_id));
  L.push('已记基线：' + String(sv.known_count || 0) + ' 个');
  return L.join('\n');
}

function renderGroups(force) {
  const box = $('groupsBox');
  if (!box || box._missing) return;
  const kw = subSearchKw();
  // 搜索框正在输入时不要重画 —— 一重画光标就掉了
  const ae = document.activeElement;
  const typing = ae && ae.id === 'subSearch';
  const sig = groupsSig() + '|' + kw;
  if (!force && !typing && box._sig === sig) return;
  if (!force && typing && box._sig === sig) return;
  box._sig = sig;
  const _sy = window.scrollY || 0;
  const gs0 = STATE.groups || [];
  const on = kw || subFilterActive();
  const gs = on ? gs0.filter(g => subMatch(g, kw)) : gs0;
  if (on) gs.forEach(g => EXPANDED.add(g.gid));
  const tip = $('subSearchTip');
  if (tip && !tip._missing) {
    tip.textContent = on
      ? (gs.length ? '筛出 ' + gs.length + ' 个群（共 ' + gs0.length + '）'
                   : '没有符合条件的群')
      : '';
  }
  if (!gs.length) {
    /* 筛选生效时，空结果也要说清是"被筛掉了"而不是"一个群都没有" ——
       两种情况的下一步操作完全不同。 */
    box.innerHTML = '<div class="empty">' + (on
      ? '没有符合条件的群。<br><br>换个条目，或点「✖️ 清空」看全部。'
      : '还没有登记任何群。<br><br>' +
        '<b>最快的办法</b>：在群里 @一下机器人 发任意内容（比如「你好」），<br>' +
        '它会自动登记，群名也会自动向官方查询。<br><br>' +
        '也可以点「💬 登记群」手动粘贴群 ID。') +
      '</div>';
    return;
  }
  // ⚠️ 输出结构对齐确认过的布局：群是折叠卡片(.grp)，
  //    群内用 mini-tab 分「UP 主 / 合集」，订阅项是 .li。
  //    以前输出的是另一套 class，布局样式对不上，群列表看着很乱。
  const _idsKey = gs.map(g => g.gid).join(',');
  const _fxOn = FX_ON && box._idsKey !== _idsKey;
  box._idsKey = _idsKey;
  box.innerHTML = gs.map(g => {
    const gid = esc(g.gid);
    const label = g.name || ('未命名群 ' + g.gid.slice(0, 8));
    const nUp = (g.subs || []).length;
    const nSea = (g.seasons || []).length;
    const open = EXPANDED.has(g.gid);
    // 这一栏上次停在哪（默认 UP 主）。重画时按它还原，
    // 不然在合集栏改完东西一保存就被打回 UP 栏。
    const mvUp = 'mv-up-' + gid, mvSea = 'mv-season-' + gid;
    const mvOn = MINI_TAB['grp:' + g.gid] || mvUp;

    const upCards = (g.subs || []).map(s => {
      const chips = (STATE.kinds || []).map(k => {
        const c = (s.kinds || {})[k.key] || {};
        return '<button class="chip-tgl ' + (c.on ? 'on' : '') +
          '" data-act="toggle-sub" data-gid="' + gid +
          '" data-uid="' + esc(s.uid) + '" data-kind="' + esc(k.key) + '">' +
          esc(k.label) + '</button>';
      }).join('');
      return '<div class="li">' +
        '<div class="av" data-name="' + esc(s.uname) + '"' +
        (s.face ? ' data-face="' + esc(proxyImg(s.face)) + '"' : '') + '></div>' +
        '<div class="bd"><div class="t1">' + esc(s.uname) + '</div>' +
        '<div class="btns" style="margin-top:7px">' + chips + '</div></div>' +
        // ⚠️ UID / 直播间号 / 每轮检测结果以前直接铺在名字下面，长短不一、
        //    还把卡片撑得很高。现在收成一个「当前数据」，放到专属文案左边，
        //    鼠标移上去才展开看详情。
        '<span class="curdata" data-tip="' + escAttr(curDataTip(s)) + '">' +
        '📊 当前数据</span>' +
        '<button class="btn" data-act="open-utpl" data-gid="' + gid +
        '" data-uid="' + esc(s.uid) + '">✍️ 专属文案' +
        utplCount(g.gid, s.uid) + '</button>' +
        '<button class="btn dg" data-act="remove-sub" data-gid="' + gid +
        '" data-uid="' + esc(s.uid) + '">🗑️</button>' +
        '</div>';
    }).join('') || '<div class="hint">这个群还没有订阅，到「➕ 添加订阅」加一个</div>';

    const seaCards = (g.seasons || []).map(sv => {
      const nm = sv.title || ('合集 ' + sv.season_id);
      // ⚠️ 以前这里显示的是 uid：名字只在 ups 缓存里查，而只订了合集、
      //    没订 UP 主的，缓存里根本没有 → 整行显示成一串数字。
      //    后端已经三级兜底（订阅时记的名字 → 缓存 → 同群订阅项），
      //    这里再判一次：拿到的名字如果就是 uid 本身，别当成名字显示。
      const owner = (sv.uname && String(sv.uname) !== String(sv.uid))
        ? String(sv.uname) : '';
      return '<div class="li">' +
        '<div class="av season-av" data-name="' + esc(owner || '合集') + '"' +
        (seasonFace(sv)
          ? ' data-face="' + esc(proxyImg(seasonFace(sv))) + '"' : '')
        + '></div>' +
        '<div class="bd"><div class="t1">📦 ' + esc(nm) + '</div>' +
        '<div class="t2">' + esc(owner || ('UID ' + sv.uid)) + ' · 已记 ' +
        esc(sv.known_count || 0) + ' 个</div>' +
        '<div class="uid"><a href="' + esc(sv.url) +
        '" target="_blank" rel="noopener">打开合集</a></div></div>' +
        '<span class="curdata" data-tip="' + escAttr(seasonTip(sv, owner)) + '">'
        + '📊 当前数据</span>' +
        '<button class="chip-tgl ' + (sv.on ? 'on' : '') +
        '" data-act="toggle-season" data-gid="' + gid +
        '" data-uid="' + esc(sv.uid) + '" data-sid="' + esc(sv.season_id) + '">' +
        (sv.on ? '已开启' : '已关闭') + '</button>' +
        '<button class="btn" data-act="open-stpl" data-gid="' + gid +
        '" data-uid="' + esc(sv.uid) + '" data-sid="' + esc(sv.season_id) + '">' +
        '✍️ 专属文案' + (sv.tpl_custom ? '（已改 ' + esc(sv.tpl_custom) + ' 条）' : '') +
        '</button>' +
        '<button class="btn dg" data-act="remove-season" data-gid="' + gid +
        '" data-uid="' + esc(sv.uid) + '" data-sid="' + esc(sv.season_id) + '">🗑️</button>' +
        '</div>';
    }).join('') || '<div class="hint">这个群还没有订阅合集</div>';

    /* 错峰入场只在该"群集合真的变了"时加：新增/删除群、搜索过滤会重画，
       这时浮上来是对的；但**展开/收起某个群也会触发重画**，
       若不加区分，每点一下整列都要重新浮一遍，看着就是"闪一下"。
       所以拿 gid 列表当键，只有它变了才加 fx-in。 */
    const fxCls = _fxOn ? ' fx-in' : '';
    return '<div class="card grp' + (open ? ' open' : '') + fxCls +
      '" data-grp="' + gid +
      '" data-gid="' + gid + '">' +
      /* ⚠️ 群背景挂在**卡片层**（不是头部层）：这样它撑满整张卡的外框，
         卡片展开变高时跟着**等比放大**（正方形：高度跟着卡片走）。
         挂在 .grp-head 里的话高度被头部锁死，永远只有头部那条窄带，
         点开也看不出变化。 */
      '<div class="grp-bg"></div>' +
      '<div class="grp-head" data-act="toggle-group" data-gid="' + gid + '">' +
      '<span class="chev"></span>' +
      '<div class="grp-meta"><h3>💖 ' + esc(label) + '</h3>' +
      '<div class="grp-sub">' + esc(g.gid.slice(0, 8)) + '… · ' + nUp +
      ' 位 UP 主 · ' + nSea + ' 个合集</div></div>' +
      '<span class="tag">' + nUp + ' UP</span>' +
      '<span class="tag">' + nSea + ' 合集</span>' +
      '<span class="grp-hint">点击展开</span>' +
      // ⚠️ 群背景选择按钮：布局样式里有 .gbg-btn / .gbg-pick / .gbg-chip，
      //    但之前 JS 从没渲染过它们 —— 样式空转，功能等于没有。
      //    按钮自带 data-act，事件委托用 closest 取最近的，
      //    所以点它不会顺带触发外层的 toggle-group。
      '<button class="gbg-btn" data-act="gbg-pick" data-gid="' + gid +
      '" title="选一个已订阅的 UP 主当背景">🖼</button></div>' +
      (nUp ? '<div class="gbg-pick" id="gbgpick-' + gid + '">' +
        '<span class="gbg-lab">背景：</span>' +
        '<button class="gbg-chip" data-act="gbg-set" data-gid="' + gid +
        '" data-face="">无</button>' +
        (g.subs || []).filter(s => s.face).map(s =>
          '<button class="gbg-chip" data-act="gbg-set" data-gid="' + gid +
          '" data-uid="' + esc(s.uid) + '" data-face="' + esc(s.face) + '">' +
          esc(s.uname) + '</button>').join('') +
        '</div>' : '') +
      '<div class="grp-body"><div class="grp-inner">' +
      '<div class="mini-tabs">' +
      '<button class="mini-tab' + (mvOn === mvUp ? ' on' : '') +
      '" data-mv="' + mvUp + '">👤 UP 主</button>' +
      '<button class="mini-tab' + (mvOn === mvSea ? ' on' : '') +
      '" data-mv="' + mvSea + '">📦 合集</button>' +
      '</div>' +
      '<div class="mini-view' + (mvOn === mvUp ? ' on' : '') +
      '" id="' + mvUp + '">' + upCards + '</div>' +
      '<div class="mini-view' + (mvOn === mvSea ? ' on' : '') +
      '" id="' + mvSea + '">' + seaCards + '</div>' +
      '</div></div></div>';
  }).join('');
  // 头像：UP 主用 B站 face，合集用箱子图标（都走 --av，布局样式负责画）
  initAvatars();
  // 群背景：把上次选过的 UP 主头像重新贴回去（存的是 uid，刷新后不丢）
  applyGbg();
  // 重画后把滚动位置放回去，不然每 15 秒自动刷新一次就跳回顶部
  try { window.scrollTo(0, _sy); } catch (e) { /* 恢复不了也无所谓 */ }
}

/* ---------- 群背景：选一个已订阅 UP 主的头像，从右往左淡出 ---------- */

function qGid(gid) {
  const esc2 = (window.CSS && CSS.escape) ? CSS.escape(gid) : gid;
  return document.querySelector('.grp[data-gid="' + esc2 + '"]');
}

function setGroupBg(gid, face, uid) {
  const card = qGid(gid);
  const bg = card && card.querySelector('.grp-bg');
  /* ⚠️ 群背景也必须走 /api/img 代理，跟 UP 主头像同一条路。
     之前只有 initAvatars() 走了代理，这里却直接用 B站 原地址：
     浏览器以 127.0.0.1 为 Referer 去取 i0.hdslb.com，防盗链直接挡回来
     —— 表现就是「选了 QZQ_Studio，界面上却什么都没有」。 */
  if (bg) bg.style.setProperty('--gbg', face ? 'url("' + proxyImg(face) + '")' : '');
  // 记住选择（存 uid 而不是图片地址：头像会变，uid 不会）
  try {
    if (face && uid) localStorage.setItem('gbg:' + gid, String(uid));
    else localStorage.removeItem('gbg:' + gid);
  } catch (e) { /* 隐私模式下写不了，不影响本次显示 */ }
}

function applyGbg() {
  (STATE.groups || []).forEach(g => {
    let uid = '';
    try { uid = localStorage.getItem('gbg:' + g.gid) || ''; } catch (e) { return; }
    if (!uid) return;
    const s = (g.subs || []).find(x => String(x.uid) === String(uid));
    if (!s || !s.face) return;
    setGroupBg(g.gid, s.face, s.uid);
    const p = document.getElementById('gbgpick-' + g.gid);
    if (p) p.querySelectorAll('.gbg-chip').forEach(c =>
      c.classList.toggle('on', String(c.getAttribute('data-uid')) === String(uid)));
  });
}

// 合集专属文案编辑弹层
async function openSeasonTpl(gid, uid, sid) {
  const d = await api('/api/season/templates?gid=' + encodeURIComponent(gid) +
    '&uid=' + encodeURIComponent(uid) + '&season_id=' + encodeURIComponent(sid));
  if (!d.ok) { toast(d.msg || '读取失败'); return; }
  const own = d.custom || {}, defs = d.defaults || {}, titles = d.titles || {};
  const grp = d.group || {};
  // ⚠️ 合集编辑页**只给「合集更新」这一类**。
  // 以前是 Object.keys(defs) 全部铺出来 —— 于是合集的弹层里会冒出
  // 开播提醒、新动态、置顶评论这些跟合集毫无关系的输入框。
  const SEASON_KEYS = ['season'];
  const body = SEASON_KEYS.filter(k => k in defs).map(k => {
    // 占位优先显示：合集自己的 → 群级的 → 全局的 → 内置默认
    const ph = own[k] != null ? own[k] : (grp[k] != null ? grp[k] : defs[k]);
    const val = own[k] != null ? own[k] : '';
    return '<label class="fld"><span>' + esc(titles[k] || k) + '</span>' +
      '<input type="text" class="stpl-in" data-stpl="' + esc(k) + '"' +
      ' value="' + esc(val) + '" placeholder="' + esc(ph || '') + '"></label>';
  }).join('');
  const g0 = (d.groups || [])[0] || {};
  const html = '<div class="note">留空 = 用上一层（全局设置 → 内置默认）。' +
    '<br>填了 = 这个合集用自己的，不受群和全局影响。</div>' +
    varButtons((d.vars || {})[g0.key] || ['{name}', '{title}',
      '{season}', '{section}'], d.var_label || {}, TPL_PHRASES.season) +
    body +
    '<div class="row wrap"><button class="btn-primary" data-act="save-stpl" ' +
    'data-gid="' + esc(gid) + '" data-uid="' + esc(uid) + '" data-sid="' + esc(sid) +
    '">保存</button>' +
    '<button class="btn dg" data-act="clear-stpl" data-gid="' + esc(gid) +
    '" data-uid="' + esc(uid) +
    '" data-sid="' + esc(sid) + '">全部清空（回到上一层）</button></div>' +
    '<div id="stplMsg" class="msg"></div>';
  openSheet('✍️ 合集 ' + esc(sid) + ' 的专属文案', html);
}

async function saveSeasonTpl(gid, uid, sid, clear) {
  const out = {};
  document.querySelectorAll('.stpl-in').forEach(el => {
    out[el.dataset.stpl] = clear ? '' : el.value;
  });
  const d = await post('/api/season/templates',
    { gid: gid, uid: Number(uid), season_id: Number(sid), templates: out });
  const m = $('stplMsg');
  if (m) { m.textContent = d.msg || (d.ok ? '已保存' : '保存失败'); m.className = 'msg ' + (d.ok ? 'ok' : 'err'); }
  if (d.ok) {
    toast(d.msg || '已保存');
    /* ⚠️ 保存成功后会 loadState() 重画整块群列表，而重画出来的 HTML
       默认把「👤 UP 主」那栏标成选中 —— 于是改完合集的文案一保存，
       页面跳回 UP 主栏，还得再点一次「📦 合集」才看得到刚改的那条。
       这里先把该群停在合集栏记下来，并保证群是展开的。 */
    MINI_TAB['grp:' + gid] = 'mv-season-' + esc(gid);
    EXPANDED.add(gid);
    closeSheet();
    loadState();
  }
}

// 这个群对这个 UP 主改了几条专属文案
function utplCount(gid, uid) {
  const m = STATE.up_tpl_counts || {};
  const per = m[gid] || {};
  const n = per[String(uid)] || 0;
  return n ? '（已改 ' + n + ' 条）' : '';
}

// UP 主专属文案编辑弹层
//
// ⚠️ 只显示**这个群对该 UP 开了哪些类型**对应的类目。
// 开了动态+直播就只有动态和直播两块，不会出现别的。
async function openUpTpl(gid, uid) {
  const d = await api('/api/up/templates?gid=' + encodeURIComponent(gid) +
    '&uid=' + encodeURIComponent(uid));
  if (!d.ok) { toast(d.msg || '读取失败'); return; }
  const own = d.custom || {}, defs = d.defaults || {}, titles = d.titles || {};
  const groups = d.groups || [];
  const varsMap = d.vars || {}, labelMap = d.var_label || {};
  const blocks = groups.map(g => {
    const fields = (g.keys || []).map(k => {
      const val = own[k] != null ? own[k] : '';
      const ph = own[k] != null ? own[k]
        : ((d.group && d.group[k] != null) ? d.group[k] : (defs[k] || ''));
      return '<label class="fld"><span>' + esc(titles[k] || k) +
        (val ? ' <span class="mini-label">已改</span>' : '') + '</span>' +
        '<input type="text" class="utpl-in" data-utpl="' + esc(k) + '"' +
        ' value="' + esc(val) + '" placeholder="' + esc(ph) + '"></label>';
    }).join('');
    return '<div class="blk"><div class="blk-t">' + esc(g.label) + '</div>' +
      varButtons(varsMap[g.key], labelMap, TPL_PHRASES[g.key]) +
      fields + '</div>';
  }).join('');
  const nm = upName(gid, uid);
  const html = '<div class="note">留空 = 用上一层（群专属 → 全局 → 内置默认）。' +
    '<br>只列出这个群对 TA <b>已开启</b>的类型：' +
    esc((d.opened || []).join('、') || '（还没开任何类型）') + '</div>' +
    (blocks || '<div class="empty">先在上面把类型开关打开，再来改文案</div>') +
    '<div class="row wrap"><button class="btn-primary" data-act="save-utpl" data-gid="' +
    esc(gid) + '" data-uid="' + esc(uid) + '">保存</button>' +
    /* ⚠️ 这个按钮以前是**连一个 class 都没挂的裸按钮标签**（没有任何样式类）。
       样式表里没有针对裸标签的基础规则（.btn 才有），
       于是它吃到浏览器默认样式 —— 深色界面里冒出一块浅灰白底黑字，
       紧挨着紫色渐变的「保存」，看着就像"样式丢了"。
       清空类按钮统一用 .btn.dg（跟「🧹 清空」「🧹 清空缓存」一致）。 */
    '<button class="btn dg" data-act="clear-utpl" data-gid="' + esc(gid) +
    '" data-uid="' + esc(uid) + '">全部清空（回到上一层）</button></div>' +
    '<div id="utplMsg" class="msg"></div>';
  openSheet('✍️ ' + esc(nm) + ' 的专属文案', html);
}

// 找 UP 主昵称（按钮上显示用）
function upName(gid, uid) {
  const g = (STATE.groups || []).find(x => x.gid === gid);
  const s = ((g && g.subs) || []).find(x => String(x.uid) === String(uid));
  return (s && s.uname) || ('UP ' + uid);
}

async function saveUpTpl(gid, uid, clear) {
  const out = {};
  document.querySelectorAll('.utpl-in').forEach(el => {
    out[el.dataset.utpl] = clear ? '' : el.value;
  });
  const d = await post('/api/up/templates',
    { gid: gid, uid: Number(uid), templates: out });
  const m = $('utplMsg');
  if (m) { m.textContent = d.msg || (d.ok ? '已保存' : '保存失败'); m.className = 'msg ' + (d.ok ? 'ok' : 'err'); }
  if (d.ok) {
    toast(d.msg || '已保存');
    // 同合集文案：重画后停在 UP 主栏（这条就是从 UP 主栏点进来的）
    MINI_TAB['grp:' + gid] = 'mv-up-' + esc(gid);
    EXPANDED.add(gid);
    closeSheet();
    loadState();
  }
}

/* ---------- 设置 ---------- */
/* 给 select 赋值：只在**选项里真有这个值**时才写。
   ⚠️ 直接 el.value = x 而 x 不在 options 里，浏览器不报错，
   而是把 value 悄悄变成空串 —— 后果有两层：
     ① 界面显示成"没有选中项"（用户看到的下拉框空白）
     ② 下次保存提交空值，把已经存好的配置抹掉
   「图片发送方式」缺 inline 选项、「沙盒群」在群列表还没拼好时赋值，都踩过这个坑。 */
function setSelectValue(el, val, fallback) {
  if (!el || el._missing) return;
  var v = String(val == null ? '' : val);
  var has = Array.prototype.some.call(el.options,
    function (o) { return o.value === v; });
  if (has) { el.value = v; return; }
  // 值不在选项里：给不出就别动，避免把已保存的值抹成空
  var fb = String(fallback == null ? '' : fallback);
  var hasFb = Array.prototype.some.call(el.options,
    function (o) { return o.value === fb; });
  if (hasFb) el.value = fb;
}

/* 「🔔 提醒开关」折起来时，摘要直接报"开了几项"，不用展开一个个看。 */
function syncSwCardSum() {
  const el = $('swCardSum');
  if (!el || el._missing) return;
  const ids = [['cOffline', '下播'], ['cOnlyUpCmt', '只推本人置顶'],
               ['cPlainMsg', '纯文本'], ['cLogTrans', '中文翻译']];
  const on = ids.filter(x => {
    const e = $(x[0]);
    return e && !e._missing && e.checked;
  }).map(x => x[1]);
  let s = on.length
    ? '已开 ' + on.length + '/' + ids.length + ' · ' + on.join('、')
    : '全关（' + ids.length + ' 项）';
  /* 音效不是开关而是档位，单独接在后面，不混进"已开几项"的计数 */
  const lv = soundLevel();
  if (lv && lv !== 'off') {
    const SN = { '1': '很轻', '2': '适中', '3': '稍响' };
    s += ' · 音效' + (SN[lv] || '');
  }
  el.textContent = s;
}

/* 「🖼️ 图片」卡片可以折起来，折着的时候从摘要就能看出当前设置。 */
function syncImgCardSum() {
  const el = $('imgCardSum');
  if (!el || el._missing) return;
  const onEl = $('cImage');
  if (onEl && !onEl._missing && !onEl.checked) {
    el.textContent = '已关闭（纯文字提醒）';
    return;
  }
  const mode = ($('cImgMode') && !$('cImgMode')._missing)
    ? String($('cImgMode').value || '') : '';
  const NAME = { inline: '内嵌', local: '本地直传', url: '平台拉取',
                 auto: '自动', off: '不发图片' };
  if (mode === 'off') { el.textContent = '不发图片'; return; }
  const w = ($('cHintW') && !$('cHintW')._missing)
    ? Number($('cHintW').value || 0) : 0;
  const h = ($('cHintH') && !$('cHintH')._missing)
    ? Number($('cHintH').value || 0) : 0;
  el.textContent = (NAME[mode] || '内嵌') + ' · '
    + (w || 672) + '×' + (h || '自动');
}

function fillCfg() {
  const c = STATE.config || {};
  [['cAppid', c.appid], ['cPoll', c.poll_interval], ['cSlow', c.slow_check_every],
   ['cTimeout', c.request_timeout], ['cDedupe', c.dedupe_window],
   // ⚠️ 这两项真实生效但以前面板没有入口，只能手改 yaml
   ['cMinReqInt', c.min_request_interval], ['cPollMax', c.poll_interval_max]]
    .forEach(([id, v]) => {
      if (FORM_DIRTY[id]) return;
      const el = $(id);
      if (el.value !== String(v == null ? '' : v)) el.value = v == null ? '' : v;
    });
  const sgEl = $('cSandboxGroup');
  if (sgEl && !sgEl._missing && !FORM_DIRTY['cSandboxGroup']) {
    /* fallback 用**当前值**：群列表还没拼好时 c.sandbox_group 不在 options 里，
       此时若回落成 ''（不启用），下次保存就会把已设好的沙盒群清掉。 */
    setSelectValue(sgEl, c.sandbox_group || '', sgEl.value);
  }
  // 两个「沙箱环境」开关互相同步（凭据页 + 设置→高级 是同一个配置）
  ['cSandbox', 'cSandbox2'].forEach(id => {
    const el = $(id);
    if (!el || el._synced) return;
    el._synced = true;
    el.addEventListener('change', () => {
      syncSandboxPair(id);
      paintSwitches();
    });
  });
  ['cSandbox', 'cOffline', 'cImage'].forEach((id, i) => {
    if (FORM_DIRTY[id]) return;
    const vals = [c.is_sandbox, c.notify_offline, c.send_image];
    $(id).checked = !!vals[i];
  });
  // 「沙箱环境」在凭据页和设置→高级各有一个（同一个配置）。
  // 两个必须同步：否则改了一个、另一个还显示旧值，看着像没保存。
  if ($('cSandbox2') && !FORM_DIRTY['cSandbox2']) {
    $('cSandbox2').checked = !!c.is_sandbox;
  }
  // 纯文本开关：Markdown 没申请权限时勾上
  if ($('cPlainMsg') && !FORM_DIRTY.cPlainMsg) {
    $('cPlainMsg').checked = String(c.message_style || 'markdown') === 'text';
  }
  // 权限：只推 UP 主本人的置顶评论（群主/管理员限制不可配，固定生效）
  if ($('cOnlyUpCmt') && !FORM_DIRTY.cOnlyUpCmt) {
    $('cOnlyUpCmt').checked = c.top_comment_only_up !== false;
  }
  if (!FORM_DIRTY.cImgMode) {
    /* ⚠️ 值必须真的存在于 option 里：给 select 赋一个不存在的 value，
       浏览器不会报错，而是把 value 变成空串 —— 表现就是下拉框显示空白，
       看着像"没有选中项"，而且之后保存提交的是空值（后端白名单直接丢弃），
       于是"改了不保存"。以前后端默认 inline 而选项里没有 inline，正好踩中。 */
    var imSel = $('cImgMode');
    if (imSel && !imSel._missing) {
      setSelectValue(imSel, c.image_send_mode || 'inline', 'inline');
    }
  }
  /* 卡片折起来时也要能看出当前是什么设置，不然每次都得展开确认 */
  syncImgCardSum();
  syncSoundSelect();
  syncSwCardSum();
  /* 图片尺寸提示：QQ markdown 私有语法 ![#宽px #高px](url)。
     ⚠️ 默认**必须开** —— 关掉会退回标准 markdown 的 ![alt](url)，
     手机端 QQ 不渲染图片（电脑端却正常），极易被误判成防盗链。 */
  if ($('cSizeHint') && !FORM_DIRTY.cSizeHint) {
    $('cSizeHint').checked = c.image_size_hint !== false;
  }
  if ($('cHintW') && !FORM_DIRTY.cHintW) {
    $('cHintW').value = Number(c.image_size_hint_w || 672);
  }
  if ($('cHintH') && !FORM_DIRTY.cHintH) {
    $('cHintH').value = Number(c.image_size_hint_h || 0);
  }
  // ⚠️ 最大高度：0 是合法值（不限制），不能用 || 兜默认值，
  //    否则「不限制」一刷新就被显示成 1200、再保存又被写死成 1200。
  // ⚠️ 每消息图片数：0 是合法值（不限），不能用 || 兜默认值
  if ($('cImgMax') && !FORM_DIRTY.cImgMax) {
    $('cImgMax').value = Number(c.image_max_per_msg || 0);
  }
  if ($('cHintMaxH') && !FORM_DIRTY.cHintMaxH) {
    $('cHintMaxH').value = Number(
      c.image_size_hint_max_h != null ? c.image_size_hint_max_h : 1200);
  }
  // 多图排版方式：值不在选项里就别动，避免把已保存的值抹成空
  setSelectValue($('cGridMode'), c.image_grid_mode, 'box');
  // 多图格子尺寸（九宫格）：0 = 跟单图一样，也是合法值，不能用 || 兜默认
  if ($('cHintCell') && !FORM_DIRTY.cHintCell) {
    $('cHintCell').value = Number(
      c.image_size_hint_cell != null ? c.image_size_hint_cell : 320);
  }
  // 中文翻译 / 输出调试日志：真实配置项，以前面板没有入口
  if ($('cLogTrans') && !FORM_DIRTY.cLogTrans) {
    $('cLogTrans').checked = c.log_translate !== false;
  }
  if ($('cDebugLog') && !FORM_DIRTY.cDebugLog) {
    $('cDebugLog').checked = !!c.log_debug;
  }
  /* 检查间隔的范围按**后台真实上下限**渲染，不写死 8~120。
     改了「轮询上限」之后这里的数字会跟着变 —— 用户报的就是
     "数值应该是多少到多少，并且会变化"。 */
  var _pmin = Number(c.poll_interval_min || 8);
  var _pmax = Number(c.poll_interval_max || 120);
  var _pe = $('cPoll');
  if (_pe && !_pe._missing) { _pe.min = _pmin; _pe.max = _pmax; }
  var _rh = $('pollRangeHint');
  if (_rh && !_rh._missing) {
    _rh.textContent = '可填 ' + _pmin + '~' + _pmax +
      ' 秒；撞风控自动放慢（最慢 ' + _pmax + 's），平稳后自动加快（最快 ' +
      _pmin + 's）；顶部心跳波形的周期跟着这个值走';
  }
  // 投稿/动态：显示后台真实值，让用户一眼知道现在到底几轮查一次
  var _sn = $('slowNow');
  if (_sn && !_sn._missing) {
    _sn.textContent = String(c.slow_check_every == null ? 1 : c.slow_check_every);
  }
  // ⚠️ 回填完必须把开关外观刷一遍，否则 .on 和 checked 对不上
  paintSwitches();
  TPL_FIELDS.forEach(([id, key]) => {
    const el = $(id);
    if (!el || el._missing) return;
    // 灰字显示默认文案，用户一眼知道留空会得到什么
    el.placeholder = (STATE.tpl_defaults || {})[key] || '';
    if (FORM_DIRTY[id]) return;
    // 只回填"用户改过的值"，没改过就保持空（用默认）
    el.value = (STATE.tpl_custom || {})[key] || '';
  });
  /* ⚠️ v1.62.0：「当前状态」以前永远是灰字，填没填全看不出来。
     两个都齐了才说"已填写"并显示**绿色**（这是唯一能一眼确认凭据没丢的信号）；
     只填了一半就用橙色提醒还差哪个 —— 只填 AppID 是跑不起来的，
     这种情况以前看着跟"填好了"没区别。 */
  const _cs = $('credSaved');
  if (_cs && !_cs._missing) {
    const hasId = !!c.appid, hasSec = !!c.has_secret;
    _cs.textContent = hasId && hasSec
      ? ('✅ 已填写（AppID：' + c.appid + '，Secret 已加密保存）')
      : (hasId ? '⚠️ 只填了 AppID（' + c.appid + '），还差 AppSecret'
               : '（还没填）');
    _cs.style.color = (hasId && hasSec) ? 'var(--ok)'
      : (hasId ? 'var(--warn)' : 'var(--tx3)');
  }
}

function initFormDirty() {
  /* 群列表搜索框：输入即过滤。
     ⚠️ renderGroups() 内部会跳过"正在这个框里打字"的情况，
        所以光标不会掉、也不会被打断。 */
  const sb = $('subSearch');
  if (sb && !sb._missing) {
    sb.addEventListener('input', () => { renderGroups(); });
  }
  const fk = $('subFilterKind');
  if (fk && !fk._missing) {
    fk.addEventListener('change', () => {
      renderSubFilterPick();
      renderGroups(true);
    });
  }
  const fp = $('subFilterPick');
  if (fp && !fp._missing) {
    fp.addEventListener('change', () => { renderGroups(true); });
  }
  renderSubFilterPick();
  initFoldCards();
  CFG_FIELDS.concat(TPL_FIELDS.map(f => f[0])).forEach(id => {
    const el = $(id);
    if (!el || el._missing) return;
    el.addEventListener('input', () => { FORM_DIRTY[id] = true; });
    el.addEventListener('change', () => {
      FORM_DIRTY[id] = true;
      /* 沙箱是一对开关：保存前必须先同步，否则读到的是另一个的旧值。 */
      if (id === 'cSandbox' || id === 'cSandbox2') syncSandboxPair(id);
      // 开关类改动立即生效
      // ⚠️ cSandbox2 / cLogTrans / cDebugLog 以前不在列表里：
      //    改了不保存，刷新又变回去 —— 就是「沙箱环境按钮无法交互」。
      // ⚠️ 下拉框（cImgMode / cSandboxGroup）以前也不在列表里：
      //    选完得靠别的字段改动才顺带提交，看着就是"选了不保存"。
      if (['cSandbox', 'cSandbox2', 'cOffline', 'cImage', 'cPlainMsg',
           'cOnlyUpCmt', 'cLogTrans', 'cDebugLog',
           'cImgMode', 'cSandboxGroup', 'cSizeHint', 'cHintW',
           'cHintH', 'cHintMaxH', 'cHintCell', 'cGridMode', 'cImgMax'].includes(id)) saveCfg(true);
      // 折叠卡片的摘要跟着变（「提醒开关」折着时也能看出开了几项）
      if (['cOffline', 'cPlainMsg', 'cOnlyUpCmt', 'cLogTrans'].includes(id)) {
        syncSwCardSum();
      }
    });
    if (id === 'cSecret') {
      el.addEventListener('blur', () => {
        if (el.value) { FORM_DIRTY[id] = true; saveCfg(true); }
      });
    }
  });
  // 文本类自动保存（防抖）
  let timer = null;
  ['cAppid', 'cPoll', 'cSlow', 'cTimeout', 'cDedupe'].forEach(id => {
    const el = $(id);
    if (!el || el._missing) return;
    el.addEventListener('input', () => {
      clearTimeout(timer);
      timer = setTimeout(() => { if (el.value !== '') saveCfg(true); }, 1200);
    });
  });
}

/* ---------- 开关外观同步 ----------
   ⚠️ 页面里一批开关写成
       <label class="sw-btn"><input type="checkbox" style="display:none"></label>
   开/关的**外观**靠 CSS 的 .on 类。但以前**没有任何代码把 checked 同步成 .on**：

     · 点一下其实 checked 变了（部分还会自动保存），外观却纹丝不动
     · 刷新后 .on 永远停在 HTML 里写死的那样，和后台真实值完全对不上

   这就是用户报的「图片板块失效」「提醒开关板块失效」「沙箱环境无法交互」的根因：
   不是后端坏了，是**开关从来没有把真实状态画出来**。 */
// ⚠️ logTrans / logErrOnly 已从日志页移除（翻译开关挪到设置，
//    「只看问题」改成按钮），留在这里只会让遍历空跑。
/* ⚠️ 凡是 `<label class="sw-btn"><input type="checkbox" id="xxx">` 这种开关，
   id **必须**登记在这里，否则：
     1. paintSwitches() 不给 label 加 .on → 外观永远显示"关"，
        哪怕后台值其实是开的（值对、显示错，最难排查的一种）
     2. initSwitches() 不绑 change → 点了不重画 → "点了没动画没反应"
   v1.41.0 新增了 cSizeHint（图片尺寸提示）却忘了同步这个数组，
   导致整个「图片」区块看着像失灵。tests/test_sw_ids.py 会自动扫描
   HTML 里所有 sw-btn 开关来兜住这件事，防止以后再漏。 */
const SW_IDS = ['cSandbox', 'cSandbox2', 'cImage', 'cOffline', 'cOnlyUpCmt',
                'cPlainMsg', 'cLogTrans', 'cDebugLog', 'cSizeHint'];

/* ⚠️「沙箱环境」一对开关（凭据页 cSandbox + 设置→高级 cSandbox2）事故复盘：
   initFormDirty / initSwitches 的 change 监听在 DOMContentLoaded 里注册，
   比 loadState()→fillCfg() 里那对"事后同步"监听更早。于是——
     1. 关掉 cSandbox2（checked=false），先触发 initFormDirty 里的保存
     2. 此时 cSandbox 还是 true，saveCfg 里写成
        `cSandbox.checked || cSandbox2.checked` → 恒为 true → 提交"开"
     3. 后面的同步把两个都设成 false、外观显示"关"（就是看到的那下动画）
     4. POST 回来 loadState()→fillCfg() 按后台的 true 又刷回"开"
   表现：点了有动画，但回到开启状态。
   修法两条：① 读取/保存前先 syncSandboxPair ② 布尔值不再用 || 合成。 */
function syncSandboxPair(srcId) {
  var a = $('cSandbox'), b = $('cSandbox2'), src = $(srcId);
  if (!a || !b || !src) return;
  a.checked = b.checked = !!src.checked;
  FORM_DIRTY['cSandbox'] = true;
  FORM_DIRTY['cSandbox2'] = true;
}

function sandboxOn() {
  // 两个已同步、值必然相同，取存在的那个即可（绝不能把两个值用「或」合起来）
  var b = $('cSandbox2'), a = $('cSandbox');
  if (b && !b._missing) return !!b.checked;
  if (a && !a._missing) return !!a.checked;
  return false;
}

function paintSwitches() {
  SW_IDS.forEach(function (id) {
    var el = $(id);
    if (!el || el._missing) return;
    var lb = el.closest ? el.closest('.sw-btn') : null;
    if (!lb) return;
    lb.classList.toggle('on', !!el.checked);
  });
}

function initSwitches() {
  SW_IDS.forEach(function (id) {
    var el = $(id);
    if (!el || el._missing) return;
    paintSwitches();
    el.addEventListener('change', function () {
      if (id === 'cSandbox' || id === 'cSandbox2') syncSandboxPair(id);
      FORM_DIRTY[id] = true;
      paintSwitches();
      /* 滑块拨过去的轻响。change 只在人为改动时触发，
         程序给 .checked 赋值不会响 —— 页面加载时不会莫名出声。 */
      sfx('toggle');
      safe('swCard', syncSwCardSum);
    });
    // 点 label 时 checked 已由浏览器切换；这里兜一次，保证"点了立刻变"
    var lb = el.closest ? el.closest('.sw-btn') : null;
    if (lb) lb.addEventListener('click', function () { setTimeout(paintSwitches, 0); });
  });
}

async function saveCfg(silent) {
  const body = {
    appid: $('cAppid').value.trim(),
    poll_interval: $('cPoll').value,
    slow_check_every: $('cSlow').value,
    request_timeout: $('cTimeout') ? $('cTimeout').value : 10,
    dedupe_window: $('cDedupe') ? $('cDedupe').value : 900,
    min_request_interval: $('cMinReqInt') ? $('cMinReqInt').value : 2.0,
    poll_interval_max: $('cPollMax') ? $('cPollMax').value : 120,
    // 两个「沙箱环境」开关是同一个配置，取任意一个（已互相同步）
    is_sandbox: sandboxOn(),
    message_style: $('cPlainMsg') && $('cPlainMsg').checked ? 'text' : 'markdown',
    // 中文翻译 / 输出调试日志（真实生效：/api/log 与 bot.py 日志级别）
    log_translate: !$('cLogTrans') || !!$('cLogTrans').checked,
    log_debug: !!($('cDebugLog') && $('cDebugLog').checked),

    top_comment_only_up: !$('cOnlyUpCmt') || $('cOnlyUpCmt').checked,

    notify_offline: $('cOffline').checked,
    send_image: $('cImage').checked,
    // ⚠️ image_send_mode 以前**根本没进 body**：
    //    下拉框改了不提交，后端收不到 → "选了不保存"的直接原因。
    image_send_mode: ($('cImgMode') && $('cImgMode').value) || 'inline',
    image_size_hint: !!($('cSizeHint') && $('cSizeHint').checked),
    image_size_hint_w: Number(($('cHintW') && $('cHintW').value) || 672),
    image_size_hint_h: Number(($('cHintH') && $('cHintH').value) || 0),
    image_size_hint_max_h: Number(
      ($('cHintMaxH') && $('cHintMaxH').value) || 0),
    // ⚠️ 格子尺寸 0 是合法值（关闭九宫格），不能用 || 兜成 320
    image_size_hint_cell: (function () {
      const _e = $('cHintCell');
      if (!_e || _e.value === '' || _e.value == null) return 320;
      const _v = Number(_e.value);
      return isNaN(_v) ? 320 : _v;
    })(),
    image_max_per_msg: Number(($('cImgMax') && $('cImgMax').value) || 0),
    // 多图排版方式：box（默认，裁剪填充）/ ratio / square，写错后端按 box 处理
    image_grid_mode: ($('cGridMode') && $('cGridMode').value) || 'box',
    // 沙盒群：固定一个群专门用来测试，留空 = 不启用
    sandbox_group: ($('cSandboxGroup') && $('cSandboxGroup').value) || '',
  };
  const sec = $('cSecret').value;
  if (sec) body.secret = sec;
  const d = await post('/api/config', body);
  if (!silent) showMsg('cfgMsg', d.ok ? '已保存 ✅' : (d.msg || '保存失败'),
                       d.ok ? 'ok' : 'err');
  if (d.ok) {
    Object.keys(FORM_DIRTY).forEach(k => { FORM_DIRTY[k] = false; });
    if (!silent) toast('设置已保存', 'ok');
    /* ⚠️ 先把刚提交的值并进 STATE，再同步显示：
       loadState() 是异步的、这里**没有 await**，不先合并的话
       syncSandbox() 读到的还是 STATE 里那个旧 gid ——
       表现就是"改完沙盒群，顶栏还显示旧的，等几十秒才变"。 */
    STATE.config = Object.assign({}, STATE.config || {}, body);
    if (body.sandbox_group && !GROUP_NAME[body.sandbox_group]) {
      var sel = $('cSandboxGroup');
      var opt = sel && sel.selectedOptions && sel.selectedOptions[0];
      if (opt && opt.textContent) GROUP_NAME[body.sandbox_group] = opt.textContent.trim();
    }
    /* 沙盒群换过之后，顶栏/测试页那几处要**立刻**跟着变，
       不能等下一轮自动刷新（那要等几十秒，看着像"改了没生效"）。 */
    safe('sandbox', syncSandbox);
    loadState();
  }
  return d;
}

async function saveTpl() {
  const body = {};
  TPL_FIELDS.forEach(([id, key]) => {
    const el = $(id);
    if (el && !el._missing) body[key] = el.value;
  });
  const d = await post('/api/config', { templates: body });
  showMsg('tplMsg', d.ok ? '文案已保存 ✅' : (d.msg || '保存失败'),
          d.ok ? 'ok' : 'err');
  if (d.ok) toast('文案已保存', 'ok');
}

function renderTplPreview() {
  const p = $('tplPreview');
  const demo = { name: '波萝Buono', title: '深夜歌回' };
  const t = $('tLive').value || '';
  p.innerHTML = t
    ? '<div class="hint-block">预览：' +
      esc(t.replace(/\{name\}/g, demo.name).replace(/\{title\}/g, demo.title)) +
      '</div>'
    : '';
}

/* ---------- B站登录 ---------- */
async function loadBili() {
  const d = await api('/api/bili/cookie');
  const box = $('biliState');
  /* ⚠️ v1.88.0：登录状态以 **B站 自己的回答** 为准，不能只看本地有没有 cookie。
     以前 has_login 只是「config 里有 SESSDATA=」这个本地判断 —— cookie 过期、
     被风控踢下线、粘了半串，它照样是 true，于是面板显示「已登录」
     而接口一直 -352/412，前后端说法完全相反。 */
  let _st = d.state || (d.has_login ? 'logged' : 'none');
  if (_st === 'logged' && d.suspicious) _st = 'invalid';
  // v1.31.0：把 B站状态存一份，顶部状态条 / 侧边栏账户卡 / 健康度都要用
  BILI_STATE = {
    has_login: !!d.has_login, expired: !!d.expired,
    soon: !!d.soon, account: (d && d.account) || {},
    state: _st, reason: d.reason || '',
  };
  safe('acccard', renderAccCard);
  safe('topbar', renderTopBar);
  safe('health', renderHealth);
  safe('todo', renderTodo);

  /* ⚠️ v1.62.0：底下那行「当前登录状态：<b id="sessState">」以前是**死的** ——
     初始化写死「读取中…」，而全项目没有任何一处给它赋过值，
     于是它永远停在"读取中"，看着就像"登录状态读不出来"。
     这里统一按接口结果填，失败也要有个明确说法，不能留"读取中"。 */
  const setSess = (txt, cls) => {
    const ss = $('sessState');
    if (!ss || ss._missing) return;
    ss.textContent = txt;
    ss.style.color = cls === 'ok' ? 'var(--ok)'
      : (cls === 'bad' ? 'var(--danger)' : 'var(--tx3)');
  };
  if (!d.ok) {
    box.innerHTML = '<div class="empty">读取失败</div>';
    setSess('读取失败，请刷新重试', 'bad');
    return;
  }
  if (!d.has_login) setSess('未登录', 'gray');
  else if (d.expired) setSess('已过期，请重新登录', 'bad');
  else if (_st === 'invalid') {
    // 存了 cookie，但 B站 说它无效 —— 这才是"前端显示登录、后端显示未登录"的真身
    setSess('未生效：B站 说不认这段 cookie' + (d.reason ? '（' + d.reason + '）' : ''), 'bad');
  } else if (_st === 'unknown') {
    setSess('已保存，但没能向 B站 求证' + (d.reason ? '（' + d.reason + '）' : ''), 'gray');
  } else {
    let t = '已登录';
    if (d.expires_str) t += '（有效期至 ' + d.expires_str + '）';
    if (d.days_left != null) t += '，还剩 ' + d.days_left + ' 天';
    if (d.soon) t += ' · 快到期了';
    setSess(t, d.soon ? 'gray' : 'ok');
  }

  const A = d.account || {};

  if (d.has_login && A.ok) {
    // 头像：B 站官方返回的 face；头像框：pendant（有装扮才有）
    const face = A.face
      ? '<img class="face" src="' + esc(A.face) + '" alt="" referrerpolicy="no-referrer">'
      : '<div class="face" style="display:grid;place-items:center;font-size:26px">🍮</div>';
    const pendant = A.pendant
      ? '<img class="pendant" src="' + esc(A.pendant) +
        '" alt="" referrerpolicy="no-referrer" onerror="this.remove()">'
      : '';
    const lv = Math.max(0, Math.min(6, parseInt(A.level, 10) || 0));
    const vipCls = (A.vip_type === 2) ? 'vip-badge annual' : 'vip-badge';
    const expCls = d.expired ? 'bad' : (d.soon ? 'soon' : '');
    const expTxt = d.expired ? 'cookie 已过期，请重新登录'
                             : ('cookie 有效期至 ' + (d.expires_str || '') +
                                (d.days_left != null ? '（还剩 ' + d.days_left + ' 天）' : ''));
    const due = A.vip_due
      ? ' · 会员到期 ' + new Date(Number(A.vip_due)).toLocaleDateString('zh-CN')
      : '';

    box.innerHTML =
      '<div class="acc-card">' +
        '<div class="acc-avatar">' + pendant + face + '</div>' +
        '<div class="acc-main">' +
          '<div class="acc-name">' +
            '<span class="' + (A.vip ? 'vip-name' : '') + '">' + esc(A.uname) + '</span>' +
            '<span class="lv-badge lv-' + lv + '">LV' + lv + '</span>' +
            (A.vip ? '<span class="' + vipCls + '">' +
              esc(A.vip_label || '大会员') + '</span>' : '') +
          '</div>' +
          '<div class="acc-meta">' +
            '<span class="acc-uid">UID ' + esc(A.mid) + '</span>' +
            '<span>·</span><span>硬币 ' + esc(A.coins || 0) + '</span>' +
          '</div>' +
          '<div class="acc-exp ' + expCls + '">⏳ ' + esc(expTxt) + due + '</div>' +
        '</div>' +
      '</div>';
  } else if (d.has_login) {
    /* ⚠️ 以前这一格无条件写「已登录」，而它进来的前提恰恰是
       **B站 没认**（A.ok 为 false）—— 于是面板写"已登录"、
       接口一直 -352，两边打架。现在照实说。 */
    const bad = (_st === 'invalid');
    const expCls = d.expired || bad ? 'bad' : (d.soon ? 'soon' : '');
    box.innerHTML =
      '<div class="acc-card">' +
        '<div class="acc-avatar"><div class="face" style="display:grid;' +
          'place-items:center;font-size:26px">' + (bad ? '🚫' : '🍮') + '</div></div>' +
        '<div class="acc-main">' +
          '<div class="acc-name">' + (bad ? '未生效' : '已保存，未验证') + '</div>' +
          '<div class="acc-meta"><span>' +
            esc(A.msg || (bad ? 'B站 说这段 cookie 无效，接口会返回 -352/412'
                              : '没能向 B站 求证，登录状态未知')) + '</span></div>' +
          '<div class="acc-exp ' + expCls + '">⏳ ' +
            esc(d.expired ? '已过期' : (bad ? '请重新扫码或粘贴完整 Cookie'
                                            : (d.expires_text || ''))) + '</div>' +
        '</div>' +
      '</div>';
  } else {
    box.innerHTML =
      '<div class="acc-card">' +
        '<div class="acc-avatar"><div class="face" style="display:grid;' +
          'place-items:center;font-size:26px;background:var(--glass)">🔒</div></div>' +
        '<div class="acc-main">' +
          '<div class="acc-name">未登录</div>' +
          '<div class="acc-meta"><span>投稿 / 动态接口会报 -352 风控，建议登录</span></div>' +
        '</div>' +
      '</div>';
  }
}

/* 扫码登录（startQr / bili-qr / bili-qr-force）已整条移除：
   走的 B站扫码接口经常拿不到 SESSDATA（被风控），
   留着反而让人以为「扫码失败 = 我操作错了」。
   浏览器登录是唯一推荐路径，续期也复用它。 */


let BROWSER_TIMER = null;
async function pollBrowser() {
  const d = await api('/api/bili/browser-status');
  const box = $('browserBox');
  if (!d.ok) return;

  if (d.running) {
    const total = d.timeout || 180;
    const waited = d.waited || 0;
    const pct = Math.min(100, Math.round(waited / total * 100));
    box.innerHTML =
      '<div class="progress-wrap">' +
      '<div class="progress-text"><span class="spinner"></span> ' +
      esc(d.stage || '等待登录') + '</div>' +
      '<div class="progress-bar"><div class="progress-fill" style="width:' +
      pct + '%"></div></div>' +
      '<div class="progress-meta">已等 ' + waited + 's ／ 剩余 ' +
      (d.remaining || 0) + 's</div>' +
      (waited > 25 ? '<div class="progress-meta" style="margin-top:6px">' +
        '首次使用要装 playwright / 启动浏览器，可能需要 1-2 分钟，' +
        '请查看是否弹出了浏览器窗口</div>' : '') +
      '</div>';
    return;
  }
  if (d.done) {
    clearInterval(BROWSER_TIMER); BROWSER_TIMER = null;
    if (d.success) {
      const a = d.account || {};
      box.innerHTML = '<div class="up-card">' +
        '<div class="bili-avatar">✅</div><div>' +
        '<div class="n">' + esc(a.uname || '登录成功') + '</div>' +
        '<div class="u">UID ' + esc(a.mid || '') +
        (a.level ? ' · LV' + esc(a.level) : '') +
        (d.expires_str ? ' · 有效期至 ' + esc(d.expires_str) : '') +
        '</div></div></div>';
      showMsg('biliMsg', '登录成功，cookie 已保存 ✅', 'ok');
      toast('B 站登录成功', 'ok');
      loadBili();
    } else {
      box.innerHTML = '';
      let html = '<div>❌ ' + esc(d.msg || '登录失败') + '</div>';
      /* v1.62.0：手动填写框默认收起了，光说"下方"会找不到 —— 直接帮着点开。
         ⚠️ 只在**收着**的时候才点，已经展开了再点就又给关上了。 */
      const _mb = $('cookieManual');
      if (_mb && !_mb._missing && _mb.hasAttribute('hidden')) {
        try { ACTIONS['toggle-cookie']($('cookieToggle')); } catch (_) { }
      }
      html += '<div style="margin-top:4px">👉 已帮你展开「✍️ 手动填写」</div>';
      const m = $('biliMsg2');
      m.className = 'msg show err';
      m.innerHTML = html;
    }
  }
}

/* ---------- 安全 ----------
   v1.62.0：安全体检卡片改成和「概况 → 健康度」一样的行式布局 ——
   左边名称 + 说明，右边状态标签；**在本面板里能改的项**整行可点，
   点了直接跳过去并聚焦到该填的输入框；改不了的（文件权限、密钥文件等）
   就老老实实显示状态，不做成可点的样子。
   ⚠️ 以前是一堆 .tile 方块：大小不齐、看不出哪些能处理，
      而且「没问题」只能复用蓝色 .tag.on（蓝色在本项目里是"可点/进行中"，
      语义对不上），所以新增了绿色 .tag.ok。
   ⚠️ 状态词要统一说「安全」而不是「正常」：
      安全体检的结论只有"有风险/没风险"两态。 */
const SEC_FIX = {
  '访问口令': { sec: 'cred', tab: 'sec', el: 'newPwd', go: '去设置 ›' },
  '机器人凭据': { sec: 'cred', tab: 'qq', el: 'cAppid', go: '去填写 ›' },
};
function secTagCls(lv) { return lv === 'ok' ? 'ok' : 'warn'; }
function secTagTxt(lv) { return lv === 'ok' ? '安全' : (lv === 'fatal' ? '危险' : '需注意'); }
async function loadSecurity() {
  const d = await api('/api/security');
  if (!d.ok) return;
  const row = (nm, ds, lv, fix) => {
    const f = fix || null;
    return '<div class="sw' + (f ? ' jump' : '') + '"' +
      (f ? ' data-act="focus-field" data-sec="' + f.sec +
           '" data-tab="' + f.tab + '" data-el="' + f.el + '"' : '') + '>' +
      '<div><div class="nm">' + esc(nm) + '</div><div class="ds">' + esc(ds) +
      (f ? '　<span class="jump-hint">' + esc(f.go) + '</span>' : '') +
      '</div></div>' +
      '<span class="tag ' + secTagCls(lv) + '">' + esc(secTagTxt(lv)) + '</span></div>';
  };
  let html = row('安全等级', d.msg || '', d.level);
  (d.checks || []).forEach(c => {
    html += row(c.name, c.detail || '', c.level, SEC_FIX[c.name]);
  });
  const fails = Number(d.login_fails || 0);
  if (fails > 0) {
    html += row('登录失败', '累计 ' + fails + ' 次，改个强口令能挡掉大部分试探',
                'warn', { sec: 'cred', tab: 'sec', el: 'newPwd', go: '去设置 ›' });
  }
  $('safeBox').innerHTML = html;

  /* 登录状态三张卡：我的 IP / 失败次数 / 锁定状态 */
  const setTxt = (id, v) => { const e = document.getElementById(id); if (e) e.textContent = v; };
  const setCls = (id, ok) => {
    const e = document.getElementById(id); if (!e) return;
    const k = e.closest('.kpi'); if (k) k.classList.toggle('ok', !!ok);
  };
  setTxt('secIp', d.my_ip || d.host || '–');
  setTxt('secIpNote', (d.my_ip === '127.0.0.1' || d.my_ip === '::1') ? '本机访问' : '远程访问');
  setTxt('secFails', String(d.login_fails != null ? d.login_fails : 0));
  const locked = (d.login_locked || 0) > 0;
  setTxt('secLock', locked ? '已锁定' : '未锁定');
  setCls('secLock', !locked);

  /* 口令当前状态：设置页里那行「当前状态：xxx」 */
  const ps = document.getElementById('pwdState');
  if (ps) {
    ps.textContent = d.has_password ? '已设置' : '未设置';
    ps.style.color = d.has_password ? 'var(--ok)' : 'var(--tx3)';
  }
}

/* ---------- 最近安全事件 ---------- */
async function loadAudit() {
  const d = await api('/api/audit');
  const box = document.getElementById('auditBox');
  if (!box) return;
  if (!d || !d.ok) { box.innerHTML = '<div class="empty" style="padding:14px">读取失败</div>'; return; }
  const items = d.items || [];
  if (!items.length) {
    box.innerHTML = '<div class="empty" style="padding:14px">还没有记录</div>';
    return;
  }
  const ICON = { '登录成功': '🔑', '登录失败': '🚫', '修改配置': '⚙️',
                 '删除订阅': '🗑️', '添加订阅': '➕', '订阅合集': '📦',
                 '订阅迁移': '🔀', '订阅复制': '🔀', '登记新群': '💬',
                 '测试推送': '📤', 'CSRF拦截': '🛡️' };
  box.innerHTML = items.map(it =>
    '<div class="li"><div class="av">' + (ICON[it.event] || '📌') + '</div>' +
    '<div class="bd"><div class="t1">' + esc(it.event || '') + '</div>' +
    '<div class="t2">' + esc([it.ip, it.detail, it.time].filter(Boolean).join(' · ')) +
    '</div></div><span class="tag ' + (it.level === 'warn' ? 'warn' : 'on') + '">' +
    (it.level === 'warn' ? '注意' : '正常') + '</span></div>').join('');
}

/* 日志概览每一部分该去哪处理。
   ⚠️ 以前概览只是"展示"，看到「B站接口 12 条」却不知道点哪，
      等于只是把问题又念了一遍。现在每行都能点，跳过去还会闪一下。
   key 必须和 logtranslate.py 里的 part 名字完全一致，
   对不上就不加跳转（仍然显示，只是不可点）。 */
var PART_GO = {
  '机器人':      ['cred',  'qq',   'qqCard',   '查凭据'],
  '机器人连接':  ['cred',  'qq',   'qqCard',   '查凭据'],
  'B站接口':     ['cred',  'bili', 'biliCard', '查 B站登录'],
  '管理面板':    ['setup', 'gen',  'paceCard', '查检查节奏'],
  '运行状态':    ['diag',  'run',  'runCard',  '看运行状态'],
  '安全加固':    ['cred',  'sec',  'secCard',  '查安全'],
  '网络请求':    ['setup', 'adv',  'advCard',  '查高级设置'],
  '消息推送':    ['setup', 'tpl',  'tplCard',  '查文案模板'],
  '数据存储':    ['setup', 'adv',  'advCard',  '查高级设置'],
  '配置文件':    ['setup', 'gen',  'paceCard', '查检查节奏'],
  '启动器':      ['setup', 'adv',  'advCard',  '查高级设置'],
  '自检':        ['diag',  'chk',  'chkCard',  '去自检'],
  '错误详情':    ['diag',  'log',  'logCard',  '看日志'],
};

/* ---------- 媒体缓存 ---------- */
async function loadMedia() {
  const d = await api('/api/media');
  if (!d || !d.ok) return;
  const set = (id, v) => { const e = document.getElementById(id); if (e) e.textContent = v; };
  set('mdCount', String(d.count != null ? d.count : 0));
  set('mdSize', d.size_text || '–');
  set('mdFail', String(d.fail_24h != null ? d.fail_24h : 0));
  // 缓存到底存在哪：不给路径的话，用户没法自己确认到底有没有在缓存
  set('mdDir', d.dir || '–');
  set('mdLong', String(d.long_count != null ? d.long_count : 0));
  set('mdLongSize', d.long_size || '0 KB');
  set('mdTmp', String(d.tmp_count != null ? d.tmp_count : 0));
  set('mdTmpSize', d.tmp_size || '0 KB');
  // 短期图现在保留 5 分钟（连发多群可复用），要写清楚，别让人以为没清
  set('mdTmpTtl', d.tmp_ttl_text || '300 秒');
  set('mdTtl', d.ttl_text || '–');
  const modeTxt = { inline: '内嵌在卡片里', local: '本地下载直传', url: '平台拉图', auto: '本地优先', off: '不发图片' };
  set('mdMode', '・发送方式：' + (modeTxt[d.mode] || d.mode || '–')
    + (d.send_image === false ? '（图片已关闭）' : '')
    + (d.exists === false ? ' ・目录还没建（推送一次就生成）' : ''));

  // 最近用到过的图片：内嵌模式**不落盘**，光数文件永远是 0，
  // 只有这份清单能反映"动态/视频/直播到底用了哪些图"。
  const rc = document.getElementById('mdRecent');
  if (rc) {
    const list = Array.isArray(d.recent) ? d.recent : [];
    if (!list.length) {
      rc.innerHTML = '<div class="hint">还没推送过带图的消息 —— 推送一次后，'
        + '这里会列出用到的直播封面 / 视频封面 / 动态配图</div>';
    } else {
      rc.innerHTML =
        '<div class="hint" style="margin-bottom:6px">最近用到的图片'
        + '（内嵌模式不落盘，这里列的是「用到过哪些图」，不是缓存了几张）</div>'
        + '<div style="display:flex;flex-direction:column;gap:5px">'
        + list.map(function (it) {
          const t = it.ts ? new Date(it.ts * 1000).toLocaleString('zh-CN', { hour12: false }) : '';
          return '<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;'
            + 'padding:6px 9px;border:1px solid var(--line,#ffffff1a);border-radius:9px">'
            + '<span style="font-size:12px;padding:1px 7px;border-radius:999px;'
            + 'background:var(--brand-soft,#2b6cb033);color:var(--brand,#7ab8ff)">'
            + esc(it.kind_text || '图片') + '</span>'
            + '<span style="font-size:13px">' + esc(it.name || '') + '</span>'
            + '<span style="font-size:12px;opacity:.55">' + esc(t) + '</span>'
            + '<a style="font-size:12px;margin-left:auto" href="' + esc(it.url)
            + '" target="_blank" rel="noreferrer">查看</a></div>';
        }).join('') + '</div>';
    }
  }
  // 手机端看不到图的解决办法：把图床域名报备给开放平台
  const hs = document.getElementById('mdHosts');
  if (hs) {
    const hosts = Array.isArray(d.hosts) ? d.hosts : [];
    hs.innerHTML = hosts.length
      ? '手机端看不到图？QQ 手机端会校验外链图片的来源，去「开放平台 → 机器人 → 开发 → 开发管理 → 消息URL配置」把这些域名加进去：'
        + hosts.map(function (h) { return '<b>' + esc(h) + '</b>'; }).join('、')
      : '';
  }
}

/* ---------- 运行状态（真实参数，不再是一片空白） ---------- */
let RUN_DATA = null;
async function loadRun() {
  const d = await api('/api/runstate');
  if (!d || !d.ok) return;
  RUN_DATA = d;
  renderRun();
}

function renderRun() {
  const box = $('runBox');
  if (!box) return;
  const d = RUN_DATA;
  if (!d) { box.innerHTML = '<div class="empty">读取中…</div>'; return; }

  // ⚠️ .kpi 的配色类在标签上（ok / wn），不是 .vl 上 —— 写错位置不上色
  const kpi = (lb, vl, sb, cls) =>
    '<div class="kpi' + (cls ? ' ' + cls : '') + '"><div class="lb">' + esc(lb) + '</div>' +
    '<div class="vl" style="font-size:20px">' + vl + '</div>' +
    '<div class="sb">' + esc(sb) + '</div></div>';

  const pr = d.proc || {};
  const pl = d.poll || {};
  const procVl = pr.unknown ? '未知' : (pr.total || 0);
  const procSb = pr.unknown ? '读不到进程列表' :
    (pr.leftover > 0 ? '⚠️ ' + pr.leftover + ' 个残留，建议清理' : '无残留');
  const pollSb = '最快 ' + (pl.min || 8) + 's / 上限 ' + (pl.max || 120) + 's'
    + (pl.auth_hold ? ' ・ 已暂停加速（B站 登录态没生效）'
       : (pl.backing_off ? ' ・ 已放慢（撞风控）' : ''));
  const pollLv = pl.auth_hold ? 'wn' : '';
  const hbAlive = !!(d.heartbeat || {}).alive;
  const hbAge = (d.heartbeat || {}).age;
  const hbSb = hbAlive ? (hbAge != null ? '最后心跳 ' + hbAge + ' 秒前' : '正常')
                       : esc((d.heartbeat || {}).detail || '没有心跳');

  box.innerHTML =
    '<div class="grid g3">' +
      kpi('进程', procVl, procSb, pr.leftover > 0 ? 'wn' : '') +
      kpi('轮询间隔', (pl.current || 30) + 's', pollSb, pollLv) +
      kpi('已推送今日', String(d.pushes_today || 0),
          '累计 ' + (d.pushes || 0) + ' 条 ・ 跑了 ' + (d.polls || 0) + ' 轮') +
    '</div>' +
    '<div style="margin-top:12px">' +
      '<div class="runrow"><span class="rk">心跳</span><span class="rv">' +
        esc(hbSb) + '</span><span class="tag ' + (hbAlive ? 'on' : 'warn') + '">' +
        (hbAlive ? '正常' : '停止') + '</span></div>' +
      '<div class="runrow"><span class="rk">面板</span><span class="rv">' +
        esc((d.panel || {}).addr || '127.0.0.1:8088') + '</span>' +
        '<span class="tag on">运行中</span></div>' +
      '<div class="runrow"><span class="rk">机器人</span><span class="rv">' +
        esc(d.bot_name || (d.bot_running ? '已上线' : '未运行')) +
        (d.uptime_text ? ' ・ 运行 ' + esc(d.uptime_text) : '') + '</span>' +
        '<span class="tag ' + (d.bot_running ? 'on' : 'warn') + '">' +
        (d.bot_running ? '在线' : '离线') + '</span></div>' +
      '<div class="runrow"><span class="rk">今日错误</span><span class="rv">' +
        String(d.errors_today || 0) + ' 条（累计 ' + String(d.errors || 0) + '）' +
        (d.last_error ? ' ・ 最近：' + esc(String(d.last_error).slice(0, 60)) : '') +
        '</span><span class="tag ' + ((d.errors_today || 0) > 0 ? 'warn' : 'on') + '">' +
        ((d.errors_today || 0) > 0 ? '有错' : '正常') + '</span></div>' +
    '</div>';
}

async function loadHarden() {
  const d = await api('/api/harden');
  if (!d.ok) return;
  const ig = d.integrity || {};
  $('hardBox').innerHTML =
    '<div class="tile"><span class="tile-k">文件完整性</span><span class="tile-v ' +
    (ig.ok ? 'ok' : 'bad') + '">' + esc(ig.ok ? '正常' : (ig.msg || '异常')) + '</span></div>' +
    '<div class="tile"><span class="tile-k">被封 IP</span><span class="tile-v">' +
    esc((d.banned || []).length) + '</span></div>' +
    '<div class="tile"><span class="tile-k">写限流</span><span class="tile-v">' +
    esc((d.limits || {}).write || '') + '</span></div>' +
    '<div class="tile"><span class="tile-k">请求体上限</span><span class="tile-v">' +
    esc(d.max_content_length || '') + '</span></div>';
}

/* ---------- 日志 ---------- */
let LOG_ORDER = 'desc';
// 日志概览里每个"部分"是干什么的。
// 光显示「机器人 84」没人知道那是什么，配一句解释才看得懂。
const PART_HINT = {
  '机器人': '程序的主体运行状态（启动、检测、推送）',
  '机器人连接': '和 QQ 服务器之间的连线情况（登录、心跳、重连）',
  'B站接口': '向 B 站查询直播/投稿/动态的结果',
  '管理面板': '网页管理面板自身的运行记录',
  '消息推送': '提醒消息的组装和发送',
  '安全加固': '防黑、限流、封禁相关',
  '运行状态': '心跳与存活状态',
  '网络请求': '对外发请求的情况（超时、连不上）',
  '依赖安装': '缺少 Python 组件',
  '配置文件': '读写 config.yaml',
  '数据存储': '读写数据库',
  '自检': '运行 python check.py 的结果',
  '启动器': '启动脚本本身',
  '错误详情': '报错的详细信息（堆栈），配合上面的错误一起看',
  '其他': '没归到具体类别的记录（一般是普通说明）',
};

// 展开的群（先按群分组，点群才展示该群订阅）
const EXPANDED = new Set();

/* 每块「UP 主 / 合集」细分当前停在哪一栏。
   键：'grp:'+gid（群卡片）、'lk'（查询结果）、'mig'（迁移清单）
   值：那一栏 mini-view 的 id（如 'mv-season-xxxx' / 'lk-season'）
   ⚠️ 没有它，切到「合集」栏再改任何东西（比如合集专属文案保存成功后
      会 loadState 重画整块列表），重画出来的 HTML 里「UP 主」那栏写死
      带 on —— 于是用户刚在合集页改完，保存一跳就回到 UP 页。
      只认 class 是不够的：class 在 DOM 里，重画就没了。 */
const MINI_TAB = Object.create(null);
try {
  const saved = localStorage.getItem('biliLogOrder');
  if (saved === 'asc' || saved === 'desc') LOG_ORDER = saved;
} catch (e) { /* 隐私模式下不可用，用默认新→旧即可 */ }

async function loadLog() {
  const d = await api('/api/log?limit=200');
  if (!d.ok) return;
  LOG_DATA = d;
  // 最近 1 小时的错误条数 → 概况页待办用
  const s = d.summary || {};
  ERR_RECENT = parseInt(s.err_1h != null ? s.err_1h
                                         : (s.err || s.errors || 0), 10) || 0;
  safe('todo', renderTodo);
  renderLog();
}

/* 「只看问题」是按钮不是开关：按下亮起、再按熄灭。
   以前是个 checkbox，但只有 label 没有 input 的联动逻辑，
   点了压根不切换，看着就是"坏了"。 */
let LOG_ERR_ONLY = false;

function renderLog() {
  const d = LOG_DATA;
  if (!d) return;
  const s = d.summary || {};
  const onlyErr = LOG_ERR_ONLY;
  // 「中文翻译」开关已挪到 设置→通用→提醒开关，日志页不再放开关，
  // 这里直接读配置，改设置即时生效（关掉≠不显示，只是不翻）。
  const showTrans = !((STATE.config || {}).log_translate === false);
  const errBtn = document.getElementById('logErrBtn');
  if (errBtn && !errBtn._missing) errBtn.classList.toggle('on', onlyErr);
  const note = document.getElementById('logTransNote');
  if (note && !note._missing) note.style.display = (d.translated === false || !showTrans) ? 'none' : '';

  const parts = s.parts || [];
  const totalErr = s.total_error != null ? s.total_error : 0;
  const totalWarn = s.total_warn != null ? s.total_warn : 0;
  const total = (d.items || []).length;

  // ⚠️ 以前这里只丢一串「🤖机器人 84 ｜ 🤖机器人连接 63 ｜ 📝其他 53」，
  // 既没说这是什么，也没说该不该管 —— 看不懂等于没显示。
  // 现在先给一句结论，再给每个部分配一句解释。
  let sum = '<div class="log-sum-lead">' +
    (totalErr > 0
      ? '<b style="color:var(--danger)">发现 ' + totalErr + ' 条错误，需要处理</b>'
      : (totalWarn > 0
          ? '<b style="color:var(--warn)">运行正常，有 ' + totalWarn + ' 条提醒</b>'
          : '<b style="color:var(--ok)">运行正常，没有错误</b>')) +
    '<span class="log-sum-sub">最近共 ' + total + ' 条记录</span></div>';

  parts.forEach(p => {
    const name = p.part || p.name || '其他';
    const n = p.total != null ? p.total : (p.count || 0);
    // 每一行都能点：跳到对应设置项并闪一下，跟「跳沙盒群」同款效果。
    // 以前只是展示，看到「B站接口 12 条」却不知道去哪处理。
    const go = PART_GO[name];
    const attrs = go
      ? ' data-act="goto-card" data-sec="' + esc(go[0]) + '" data-tab="' + esc(go[1]) +
        '" data-id="' + esc(go[2]) + '"'
      : '';
    sum += '<div class="log-sum-row"' + attrs + '>' +
      '<span class="log-part">' + esc(p.icon || '') + ' ' + esc(name) + '</span>' +
      '<span class="log-sum-n">' + esc(n) + ' 条</span>' +
      '<span class="log-sum-why">' + esc(PART_HINT[name] || '该部分的记录') + '</span>' +
      (p.error ? '<span class="log-sum-bad">❌ ' + esc(p.error) + ' 条错误</span>' : '') +
      (go ? '<span class="log-sum-go">' + esc(go[3] || '去处理') + ' ›</span>' : '') +
      '</div>';
  });
  $('logSummary').innerHTML = sum;

  let items = d.items || [];
  if (onlyErr) items = items.filter(i => i.level === 'error' || i.level === 'warn');

  // 排序：asc=旧→新，desc=新→旧（默认）。
  // 用原始索引兜底，保证同一秒 / 无时间戳的条目顺序稳定，不会来回跳。
  const idx = new Map(items.map((it, n) => [it, n]));
  items = items.slice().sort((a, b) => {
    const ka = (a.date || '') + ' ' + (a.time || '');
    const kb = (b.date || '') + ' ' + (b.time || '');
    if (ka === kb) return idx.get(a) - idx.get(b);
    return LOG_ORDER === 'asc' ? (ka < kb ? -1 : 1) : (ka < kb ? 1 : -1);
  });

  // 按「年月日」分组 → 时间轴
  const groups = [];
  const byDate = new Map();
  items.forEach(it => {
    const dk = it.date || '';
    if (!byDate.has(dk)) { byDate.set(dk, []); groups.push(dk); }
    byDate.get(dk).push(it);
  });

  const btn = $('logOrderBtn');
  if (btn && !btn._missing) {
    btn.textContent = LOG_ORDER === 'asc' ? '🔀 排序：旧→新' : '🔀 排序：新→旧';
  }

  $('logBox').innerHTML = items.length
    ? groups.map(dk => {
        const list = byDate.get(dk);
        const head = dk
          ? '<div class="tl-date"><span class="tl-date-t">' + esc(fmtLogDate(dk)) + '</span>' +
            '<span class="tl-count">' + list.length + ' 条</span></div>'
          : '<div class="tl-date dim"><span class="tl-date-t">未标注时间</span>' +
            '<span class="tl-count">' + list.length + ' 条</span></div>';
        return '<section class="tl-group">' + head +
          '<div class="tl-items">' + list.map((i, n) =>
            '<div class="tl-item lv-' + esc(i.level || 'info') + '" style="animation-delay:' +
              Math.min(n * 8, 200) + 'ms">' +
            '<span class="tl-dot"></span>' +
            '<span class="tl-clock">' + esc(i.time || '--:--:--') + '</span>' +
            '<div class="tl-body">' +
              '<div class="tl-meta">' +
                '<span class="log-part">' + esc(i.icon || '') + ' ' + esc(i.part || '') + '</span>' +
                // 级别显示中文（以前直接显示 info / warn，看不懂）
                '<span class="log-lv ' + esc(i.level || 'info') + '">' +
                esc(i.level_cn || i.level || '') + '</span>' +
                // 连着刷了好几次的同一句：标出次数，不再占好几行
                (i.repeat && i.repeat > 1
                  ? '<span class="log-rep" title="这一秒内连续出现了 ' + i.repeat +
                    ' 次">×' + i.repeat + '</span>' : '') +
              '</div>' +
              (showTrans && i.meaning ? '<div class="log-trans">' + esc(i.meaning) + '</div>' : '') +
              (i.howto ? '<div class="log-trans" style="color:var(--accent)">👉 ' + esc(i.howto) + '</div>' : '') +
              // ⚠️ 有白话解释时原文默认收起：否则同一件事在一条里出现两遍
              //    （上面一句人话、下面一行「机器人主程序 调整检测间隔 …」），
              //    看着就像同一条消息被重复推送。想看具体数值点「原文」展开。
              (showTrans && i.cn && i.meaning
                ? '<details class="log-detail"><summary>原文</summary>' +
                  '<div class="log-raw">' + esc(i.cn) + '</div></details>'
                : (showTrans && i.cn
                    ? '<div class="log-raw">' + esc(i.cn) + '</div>'
                    : '<div class="log-raw">' + esc(i.raw || '') + '</div>')) +
            '</div></div>').join('') +
          '</div></section>';
      }).join('')
    : '<div class="empty">暂时没有日志</div>';
}

function fmtLogDate(dk) {
  // 2026-09-19 → 2026 年 09 月 19 日（今天 / 昨天加标注）
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dk || '');
  if (!m) return dk || '';
  const y = m[1], mo = m[2], d = m[3];
  let txt = y + ' 年 ' + mo + ' 月 ' + d + ' 日';
  try {
    const pad = n => String(n).padStart(2, '0');
    const now = new Date();
    const t = now.getFullYear() + '-' + pad(now.getMonth() + 1) + '-' + pad(now.getDate());
    const yd = new Date(now.getTime() - 86400000);
    const ydt = yd.getFullYear() + '-' + pad(yd.getMonth() + 1) + '-' + pad(yd.getDate());
    if (dk === t) txt += ' · 今天';
    else if (dk === ydt) txt += ' · 昨天';
  } catch (e) { /* 算不出来就算了，不影响显示 */ }
  return txt;
}

/* ---------- 更新日志 ---------- */
// 显示条数：'3' / '5' / 'all'，默认 5（和页面上 .cl-tab.on 的初始态一致）
let CL_LIMIT = '5';
async function loadVersion() {
  // 三档：'3' / '5' / 'all'。all 传一个大数让后端一次给完
  // （后端上限已放到 999，当前日志 94 条）。
  const lim = (typeof CL_LIMIT === 'undefined') ? '5' : CL_LIMIT;
  const n = (lim === 'all') ? 999 : (parseInt(lim, 10) || 5);
  const d = await api('/api/version?limit=' + n);
  const list = d.changelog || d.log || d.entries || [];
  /* 最新一条更新日志的版本号 = 后端当前版本，拿来和本份脚本比一比 */
  try { checkJsVersion((list[0] || {}).version); } catch (_) { /* 忽略 */ }
  const LV = { major: '大更新', minor: '中等更新', patch: '小更新' };
  /* 更新日志里常写 `innerHTML=''` 这类代码。先转义再把反引号里的
     内容渲染成 <code>，读起来比一坨等宽黑字清楚得多。
     ⚠️ 顺序不能反：必须先 esc 再替换，否则 <code> 会被转义掉。 */
  const clText = s => esc(s).replace(/`([^`]+)`/g, '<code>$1</code>');

  $('changelogBox').innerHTML = list.slice(0, n).map((e, idx) => {
    const groups = {};
    (e.items || []).forEach(it => {
      const g = it.group || '';
      (groups[g] = groups[g] || []).push(it.text);
    });
    const body = Object.keys(groups).map(g =>
      '<div class="cl-group">' + esc(g || '更新内容') + '</div><ul>' +
      groups[g].map(t => '<li>' + clText(t) + '</li>').join('') + '</ul>').join('');
    /* 摘要（标题冒号后那句）单独一行。
       以前只有「v1.98.6 小更新」，不展开根本不知道改了什么。 */
    const sum = String(e.summary || '').trim();
    const sumHtml = sum
      ? '<div class="cl-sum">' + esc(sum) + '</div>' : '';
    return '<div class="cl-item' + (idx === 0 ? ' open' : '') + '">' +
      '<div class="cl-head" data-act="cl-toggle">' +
      '<div class="cl-row">' +
      '<span class="cl-ver">v' + esc(e.version) + '</span>' +
      '<span class="cl-lv">' + esc(LV[e.level] || e.level || '') + '</span>' +
      /* 日期带时分秒：后端 version.py 把「2026-09-21 20:30:15」拆成
         date + time 两个字段，这里拼回去显示。
         ⚠️ 不能用 slice(0,10) 截 —— 那会把时分秒砍掉，正是用户要的。
         早期条目只写了年月日（time 为空），就只显示日期，不倒填假时间。 */
      '<span class="cl-date">' +
        esc([String(e.date || '').trim(), String(e.time || '').trim()]
          .filter(Boolean).join(' ')) + '</span>' +
      '<span style="flex:1"></span><span class="cl-arrow">▸</span>' +
      '</div>' + sumHtml +
      '</div><div class="cl-body">' + body + '</div></div>';
  }).join('') || '<div class="empty">没有更新日志</div>';
}

/* ---------- 查询结果里的「UP 主 / 合集」二选一清单 ----------

   ⚠️ 以前订阅合集只能手动粘 space.bilibili.com/UID/lists/ID?type=season，
      UID 和合集 ID 都得自己去翻；而 UP 主订阅又在同一张卡片里靠一个
      「💖 添加」按钮完成 —— 两套东西、两种做法，很容易加错。
   现在查询结果直接分两栏（跟「群与订阅」里群卡片的 mini-tab 一个样式）：
      点 UP 主 → 加 UP 主订阅（按上面勾的提醒类型）
      点合集   → 加这个合集订阅
   已经订过的会标「已订阅」，不会重复加。 */
function lookupGroup() {
  const gid = ($('addGroup') || {}).value || '';
  return (STATE.groups || []).find(g => g.gid === gid) || null;
}

function renderLookupPick() {
  const box = $('lookupInfo');
  const u = LOOKUP;
  if (!u || !box || box._missing) return;
  const g = lookupGroup();
  const uid = String(u.uid || '');
  const subUids = new Set(((g && g.subs) || []).map(s => String(s.uid)));
  const seaKeys = new Set(((g && g.seasons) || [])
    .map(s => String(s.uid) + ':' + String(s.season_id)));
  const upOn = subUids.has(uid);
  // 后端已按"最近一次加视频"倒序，这里再排一次兜底：
  // 万一哪天接口换了顺序，列表里第一个也应该是最新更新的那个合集。
  const seasons = (LOOKUP_SEASONS || []).slice().sort(
    (a, b) => (Number(b.recent) || 0) - (Number(a.recent) || 0));

  const upRow =
    '<div class="li">' +
    '<div class="av" data-name="' + esc(u.uname || '') + '"' +
    (u.face ? ' data-face="' + esc(u.face) + '"' : '') + '></div>' +
    '<div class="bd"><div class="t1">' + esc(u.uname || '未知') + '</div>' +
    '<div class="t2">UID ' + esc(uid) +
    (roomText(u.room_id, u.short_room_id)
      ? ' · 直播间 ' + esc(roomText(u.room_id, u.short_room_id)) : '') +
    '</div></div>' +
    (upOn
      ? '<span class="tag">已订阅</span>'
      : '<button class="btn pri" data-act="pick-add-sub">💖 订阅这个 UP 主</button>') +
    '</div>';

  let seaBody;
  if (LOOKUP_SEA_ERR) {
    seaBody = '<div class="hint">合集没取到：' + esc(LOOKUP_SEA_ERR) +
      '<br>可以直接在下面「📦 订阅视频合集」里粘链接添加。</div>';
  } else if (!seasons.length) {
    seaBody = '<div class="hint">这位 UP 主没有公开的合集' +
      (LOOKUP_SERIES_N ? '（有 ' + LOOKUP_SERIES_N +
        ' 个「视频列表」，那是另一个东西，接口不同，订阅不了）' : '') +
      '。</div>';
  } else {
    seaBody = seasons.map(s => {
      const key = uid + ':' + String(s.id);
      const on = seaKeys.has(key);
      const when = s.recent ? ' · 最近更新 ' + esc(fmtAgo(s.recent)) : '';
      return '<div class="li">' +
        '<div class="av season-av" data-name="📦"' +
        (seasonFace(s)
          ? ' data-face="' + esc(proxyImg(seasonFace(s))) + '"' : '') + '></div>' +
        '<div class="bd"><div class="t1">📦 ' +
        esc(s.title || ('合集 ' + s.id)) + '</div>' +
        '<div class="t2">' + esc(s.total || 0) + ' 个视频' + when + '</div></div>' +
        (on
          ? '<span class="tag">已订阅</span>'
          : '<button class="btn pri" data-act="pick-add-season" data-sid="' +
            esc(s.id) + '" data-title="' + escAttr(s.title || '') +
            '">📦 订阅</button>') +
        '</div>';
    }).join('');
  }

  // 换群 / 订阅成功后会重画：必须先把上一块删掉，否则叠着长
  const old = box.querySelector('.lk-pick');
  if (old) old.remove();
  // 订阅成功后会重画：记住上次停在哪一栏（默认 UP 主）
  const lkOn = MINI_TAB['lk'] || 'lk-up';
  box.insertAdjacentHTML('beforeend',
    '<div class="lk-pick">' +
    '<div class="mini-tabs">' +
    '<button class="mini-tab' + (lkOn === 'lk-up' ? ' on' : '') +
    '" data-mv="lk-up">👤 UP 主</button>' +
    '<button class="mini-tab' + (lkOn === 'lk-season' ? ' on' : '') +
    '" data-mv="lk-season">📦 合集' +
    (seasons.length ? '（' + seasons.length + '）' : '') + '</button>' +
    '</div>' +
    '<div class="mini-view' + (lkOn === 'lk-up' ? ' on' : '') +
    '" id="lk-up">' + upRow + '</div>' +
    '<div class="mini-view' + (lkOn === 'lk-season' ? ' on' : '') +
    '" id="lk-season">' + seaBody + '</div>' +
    '<div class="hint" style="margin-top:8px">订阅到：<b>' +
    esc(g ? (g.name || ('未命名群 ' + g.gid.slice(0, 8))) : '（先选一个群）') +
    '</b> · 换群就重画一次，已订过的会标出来</div>' +
    '</div>');
  initAvatars();
}

/* 相对时间：合集列表里显示"3 天前更新"比一长串时间戳好认 */
function fmtAgo(ts) {
  const t = Number(ts) || 0;
  if (!t) return '';
  const s = Math.floor(Date.now() / 1000) - t;
  if (s < 60) return '刚刚';
  if (s < 3600) return Math.floor(s / 60) + ' 分钟前';
  if (s < 86400) return Math.floor(s / 3600) + ' 小时前';
  if (s < 86400 * 30) return Math.floor(s / 86400) + ' 天前';
  if (s < 86400 * 365) return Math.floor(s / (86400 * 30)) + ' 个月前';
  return Math.floor(s / (86400 * 365)) + ' 年前';
}

/* ---------- 操作分发 ---------- */
const ACTIONS = {
  // 关闭全屏面板（返回主页）
  async 'close-sheet'() { closeSheet(); },
  // 主面板的返回/✕。
  // ⚠️ 这两个按钮以前也是 close-sheet，而 close-sheet 现在只关文案弹层
  // —— 于是主面板点返回、点 ✕ 都没反应，只能刷新页面。必须分开。
  // 名字刻意叫 go-home 而不是「关闭主区」：主区是常驻的，这里只是跳回概况，
  // 叫"关闭"会让后来的人（和我）误以为要隐藏主区，又改回白屏。
  async 'go-home'() { goHome(); },
  // 群背景：展开/收起选择行（同一时刻只开一个群的）
  'gbg-pick'(el) {
    const gid = el.getAttribute('data-gid');
    const p = document.getElementById('gbgpick-' + gid);
    if (!p) { toast('这个群还没有已订阅的 UP 主'); return; }
    document.querySelectorAll('.gbg-pick.show').forEach(x => {
      if (x !== p) x.classList.remove('show');
    });
    p.classList.toggle('show');
  },
  // 群背景：选中某个 UP 主（或"无"）
  'gbg-set'(el) {
    const gid = el.getAttribute('data-gid');
    const face = el.getAttribute('data-face') || '';
    setGroupBg(gid, face, el.getAttribute('data-uid') || '');
    const p = document.getElementById('gbgpick-' + gid);
    if (p) p.querySelectorAll('.gbg-chip').forEach(c => c.classList.toggle('on', c === el));
  },
  // 快捷插入变量：插到光标处（不是末尾追加）
  async 'ins-var'(el) {
    insertVar(el.getAttribute('data-var') || '');
  },


  'goto-sec': (el) => {
    const sec = el && el.getAttribute('data-sec');
    if (!sec) return;
    goto(sec);
    setTimeout(() => {
      const card = document.querySelector('.acc-item[data-sec="' + sec + '"]');
      if (!card) return;
      try { card.scrollIntoView({ behavior: 'smooth', block: 'start' }); } catch (e) {}
      if (sec === 'setup') {
        const f = $('cAppid');
        if (f && !f._missing) { try { f.focus(); } catch (e) {} }
      }
    }, 220);
  },

  /* 「👋 第一次使用？」里的「现在去填写」。
     ⚠️ 以前固定跳 setup（⚙️ 设置），而 AppID / AppSecret 输入框其实在
        cred（🔐 凭据 → QQ 凭据）里 —— 点一下跳到一个根本没有 AppID
        输入框的页面，等于把人指错地方。改成直达并聚焦到 AppID。 */
  'goto-setup': () => ACTIONS['focus-field']({
    getAttribute: (k) => (k === 'data-sec' ? 'cred'
                          : k === 'data-tab' ? 'qq' : 'cAppid'),
  }),
  'goto-home': () => goto('status'),

  // 测试推送优先发到沙盒群：固定一个群专门试东西，不用每次去选
  async 'test-push'() {
    const sg = ($('cSandboxGroup') && $('cSandboxGroup').value) || '';
    const gid = sg || $('addGroup').value;
    if (!gid) {
      showMsg('quickMsg', '先在「⚙️ 设置」里指定沙盒群，或在「💖 订阅」里登记一个群',
              'err');
      goto('setup');
      return;
    }
    showMsg('quickMsg', sg ? '正在发到沙盒群…' : '正在发送…');
    const d = await post('/api/test', { gid: gid, kind: 'live' });
    showMsg('quickMsg', d.ok ? (d.msg || '已发送，去看群里') : (d.msg || '发送失败'),
            d.ok ? 'ok' : 'err');
  },
  // 自检用后端固定的测试 UID，和「添加订阅」输入框无关
  async 'diagnose'() {
    renderDiag(null);   // 先把六行刷成「未检测」，跑完再填真实参数
    showMsg('quickMsg', '正在自检…');
    const d = await api('/api/diagnose');
    const b = d.bili || {}, q = d.qq || {};
    // ⚠️ 以前这里往另一个 span 里写「测试 UID：xxxx」，而它正好排在
    //    「已记住：用户 | uid：xxxx」后面 —— 于是跑完自检就多出一截
    //    「测试 UID：401315430」，看着像重复。现在改成刷新同一处显示。
    if (d.selfcheck_uid) {
      const _cb = $('chkUid');
      const _v = String(d.selfcheck_uid);
      if (_cb && document.activeElement !== _cb) _cb.value = _v;
      showChkUid(_v, d.selfcheck_uname || '');
    }
    showMsg('quickMsg',
      'B站：' + (b.ok ? '✅ ' + (b.uname || '通') : '❌ ' + (b.msg || '')) +
      ' ｜ QQ：' + (q.ok ? '✅ ' + q.msg : '❌ ' + q.msg),
      (b.ok && q.ok) ? 'ok' : 'err');
    /* 「重新自检」只负责把上面七行刷成最新结果。 */
    renderDiag(b);
  },
  /* ⚠️ 全面自检里原本还有一个「⚡ 立即检测」按钮，它会把结果单独开一块
     卡片显示 —— 而卡片里的七行跟上面那七行怎么调都对不齐（复制 DOM 也
     一样），来回改了好几版都不是要的效果。现已按用户要求**整个移除**，
     全面自检只留「重新自检」一个按钮：点了就把上面七行刷成最新结果。
     概况页 / 运行状态页的「立即检测」（巡查所有订阅）不受影响。 */
  async 'check-now'(btn) {
    /* ⚠️ 结果要显示在**点按钮的那一页**。
       以前固定写进 #checkNowBox，而那个容器在「自检」面板里 ——
       在「运行状态」页点「立即检测」，结果跑进一个隐藏面板，
       表现就是"点了没反应"。现在按按钮就近找 .check-now-out。 */
    const box = pickCheckNowBox(btn);
    if (!box) return;
    box.innerHTML = '<div class="progress-text">'
      + '<span class="spinner"></span> 正在检测所有订阅…</div>';
    const d = await post('/api/check-now', {});
    if (!d.ok) {
      showMsg('quickMsg', d.msg || '检测失败', 'err');
      /* ⚠️ 只清掉「正在检测…」那行：以前整块 innerHTML=''，
         把上一次的检测结果一起抹了 —— 试一次失败就把历史全丢。 */
      var pr2 = box.querySelectorAll('.progress-text');
      for (var qi = 0; qi < pr2.length; qi++) pr2[qi].remove();
      toast(d.msg || '检测失败', 'err');
      return;
    }
    /* 每次检测单独成块（跟推送测试「实际图片行」一个样式），
       块头带时间/耗时/结论，点一次开一块、不覆盖上一次，方便对比。 */
    const rows = d.rows || [];
    let body;
    if (!rows.length) {
      body = '<div class="empty">还没有任何订阅</div>';
    } else {
      body = rows.map(r => {
        let h = '<div class="tile" style="min-height:auto;margin-bottom:8px">';
        h += '<div class="tile-k">' + esc(r.name) + ' · UID '
          + esc(r.uid) + ' · '
          + (roomText(r.room_id, r.short_room_id)
              ? '直播间 ' + esc(roomText(r.room_id, r.short_room_id))
              : '无直播间号')
          + '</div>';
        const map = [['live', '直播'], ['video', '投稿'], ['dynamic', '动态']];
        map.forEach(pair => {
          const k = pair[0], label = pair[1];
          const v = (r.results || {})[k];
          if (!v) return;
          if (v.ok) {
            /* ⚠️ 直播状态必须显示"未开播/已开播/轮播中"，不能是 0/1/2。
               后端已经带中文 text，以前前端偏取数字 status，
               于是结果区里永远是"当前 1"。比较基线仍用数字。 */
            const raw = (k === 'live') ? v.status : (k === 'video' ? v.bvid : v.id);
            const curShow = (k === 'live')
              ? (v.text || liveStatusCn(v.status)) : raw;
            // 基线是按群存的：每个群进度可能不同，多群时逐个显示
            const bls = Array.isArray(v.baselines) ? v.baselines : [];
            const base = v.baseline;
            const same = String(base) === String(raw);
            let blTxt;
            if (bls.length > 1) {
              blTxt = bls.map(b => (b.name ? esc(b.name) : esc((b.gid || '').slice(0, 8)))
                + ' ' + esc(b.value === '' ? '（空）' : b.value)).join('、');
            } else {
              /* 已记的直播基线也可能存着 0/1/2，同样翻成中文再显示 */
              blTxt = esc(base === undefined || base === null
                || base === '' ? '（空）'
                : (k === 'live' ? liveStatusCn(base) : String(base)));
            }
            h += '<div class="tile-v ' + (same ? '' : 'ok') + '">'
              + label + '：当前 ' + esc(curShow === undefined || curShow === null
                  ? '（空）' : String(curShow))
              + ' ｜ 已记 ' + blTxt
              + (same ? '' : '　← 不一样，会推送')
              + '</div>';
          } else {
            h += '<div class="tile-v bad">' + label + '：'
              + esc(v.text || '失败') + '</div>';
          }
        });
        return h + '</div>';
      }).join('');
    }
    pushCheckNowCard(box, d, body);
    toast('检测完成：' + (d.summary || ''), 'ok');
  },
  async 'refresh-names'() {
    toast('正在向官方查询群名…');
    const d = await post('/api/group/refresh-names', {});
    const id = $('addGroupMsg')._missing ? 'quickMsg' : 'addGroupMsg';
    showMsg(id, d.ok ? (d.msg || '') : (d.msg || '查询失败'), d.ok ? 'ok' : 'err');
    // 失败的群要说明是哪个、为什么，不然"1 个未能获取"等于没说
    const box = $('groupNameDetail');
    if (box && !box._missing) {
      const bad = (d.detail || []).filter(x => x && x.reason);
      box.innerHTML = bad.length
        ? '<div class="diag-row">' + bad.map(x =>
            '群 ' + esc(String(x.gid).slice(0, 8)) + '… → ' + esc(x.reason)
          ).join('</div><div class="diag-row">') + '</div>'
        : '';
    }
    if (d.ok) { toast(d.msg || '已更新'); loadState(); }
  },
  /* ---------- 订阅迁移：全体 / 选择 ----------
     全体：整群照搬（源群的订阅、专属文案、合集一起走）。
     选择：只搬勾中的「UP 主 × 提醒类型」。

     ⚠️ 选择模式下「迁移」**不是清空源群** —— 只移除真正搬走的那几项。
     否则没勾选的订阅会跟着一起消失，那是不可逆的数据丢失，
     而且从界面上完全看不出自己丢了什么。 */
  'mig-scope'(el) {
    const sc = el.getAttribute('data-scope') || 'all';
    MIG_SCOPE = sc;
    document.querySelectorAll('[data-act="mig-scope"]').forEach(b => {
      b.classList.toggle('on', b.getAttribute('data-scope') === sc);
    });
    const box = $('migPick');
    if (box && !box._missing) box.style.display = (sc === 'part') ? '' : 'none';
    const hint = $('migScopeHint');
    if (hint && !hint._missing) {
      hint.textContent = (sc === 'part')
        ? '只搬勾中的 UP 主和提醒类型，没勾的留在源群'
        : '把源群的订阅、专属文案、合集整份搬到目标群';
    }
    if (sc === 'part') renderMigPick();
  },
  // 勾/取消某个「UP 主 × 提醒类型」
  // 外层大卡片：点整块切换"要不要搬"（未选中 → 整块变灰）
  'mig-card'(el) {
    el.classList.toggle('on', !el.classList.contains('on'));
    updateMigStat();
  },
  // 合集的大卡片：按住 data-sid，选中 = 搬这个合集
  'mig-sea-card'(el) {
    el.classList.toggle('on', !el.classList.contains('on'));
    updateMigStat();
  },
  'mig-chip'(el) {
    /* ⚠️ 外层大卡片没选中时，小卡片**必须点不动**。
       以前只凭 .chip-tgl.on 收集，卡片选没选中跟小卡片是两回事 ——
       于是会出现"整块看着是灰的，里面的提醒却已经勾上了"。 */
    const card = el.closest('.mig-card');
    if (card && !card.classList.contains('on')) {
      toast('先在卡片上点一下选中它，再挑要搬哪些提醒', 'err');
      return;
    }
    el.classList.toggle('on', !el.classList.contains('on'));
    updateMigStat();
  },
  'mig-pick'(el) {
    const mode = el.getAttribute('data-all');
    const g = migSrcGroup();
    const seaMode = (migActiveTab() === 'season');
    if (seaMode) {
      // 合集那一栏：只有"选中/不选中"，没有提醒类型可分
      document.querySelectorAll('#migSeaList .mig-card').forEach(c => {
        c.classList.toggle('on', mode === '1');
      });
      updateMigStat();
      return;
    }
    document.querySelectorAll('#migPickList .mig-card').forEach(card => {
      const uid = card.getAttribute('data-uid');
      const s = ((g && g.subs) || [])
        .find(x => String(x.uid) === String(uid));
      // 「只选已开启的」：源群里这位 UP 一类都没开 → 整块不选
      const any = ['live', 'video', 'dynamic', 'top_comment']
        .some(k => !!(s && s.kinds && s.kinds[k] && s.kinds[k].on));
      let want;
      if (mode === '1') want = true;
      else if (mode === '0') want = false;
      else want = any;
      card.classList.toggle('on', !!want);
      card.querySelectorAll('.chip-tgl').forEach(c => {
        let kon;
        if (mode === '1') kon = true;
        else if (mode === '0') kon = false;
        else {
          // 「只选已开启的」：按源群**实际的开关状态**来勾
          const k = c.getAttribute('data-kind');
          kon = !!(s && s.kinds && s.kinds[k] && s.kinds[k].on);
        }
        c.classList.toggle('on', !!kon);
      });
    });
    updateMigStat();
  },
  'mig-seasons'(el) {
    MIG_SEASONS = !el.classList.contains('on');
    el.classList.toggle('on', MIG_SEASONS);
    // 关掉之后再打开要重画，否则合集那一栏还停在"开关是关的"提示上
    renderMigSea();
    updateMigStat();
  },
  // 订阅迁移：把源群的订阅复制/迁移到目标群
  async 'mig-copy'() {
    const src = $('migSrc') && $('migSrc').value;
    const dst = $('migDst') && $('migDst').value;
    if (!src || !dst) { showMsg('migMsg', '先选好源群和目标群', 'err'); return; }
    if (src === dst) { showMsg('migMsg', '源群和目标群不能是同一个', 'err'); return; }
    const extra = migExtra();
    if (extra === null) return;
    showMsg('migMsg', '正在复制…');
    const d = await post('/api/sub/copy',
      Object.assign({ src: src, dst: dst, move: false }, extra));
    showMsg('migMsg', d.ok ? (d.msg || '已复制') : (d.msg || '复制失败'),
            d.ok ? 'ok' : 'err');
    if (d.ok) { toast(d.msg || '已复制'); loadState(); }
  },
  async 'mig-move'() {
    const src = $('migSrc') && $('migSrc').value;
    const dst = $('migDst') && $('migDst').value;
    if (!src || !dst) { showMsg('migMsg', '先选好源群和目标群', 'err'); return; }
    if (src === dst) { showMsg('migMsg', '源群和目标群不能是同一个', 'err'); return; }
    const gs = STATE.groups || [];
    const sn = (gs.find(g => g.gid === src) || {}).name || ('群 ' + src.slice(0, 8));
    const dn = (gs.find(g => g.gid === dst) || {}).name || ('群 ' + dst.slice(0, 8));
    const extra = migExtra();
    if (extra === null) return;
    let tip;
    if (MIG_SCOPE === 'part') {
      const n = (extra.picks || []).length;
      const m = (extra.picks || []).reduce((a, p) =>
        a + ((p.kinds || []).length), 0);
      const ns = (extra.season_picks || []).length;
      tip = '确定要把「' + sn + '」里勾选的 ' + n + ' 位 UP 主（' + m +
            ' 个提醒）' + (ns ? '、' + ns + ' 个合集' : '') +
            '迁移到「' + dn + '」吗？\n\n' +
            '源群只会移除这几项，没勾选的原样保留。';
    } else {
      // 全体迁移会清空源群且不可逆 —— 必须二次确认
      tip = '确定要把「' + sn + '」的订阅全部迁移到「' + dn + '」吗？\n\n' +
            '迁移后源群会被清空，此操作不可撤销。';
    }
    if (!confirm(tip)) return;
    showMsg('migMsg', '正在迁移…');
    const d = await post('/api/sub/copy',
      Object.assign({ src: src, dst: dst, move: true }, extra));
    showMsg('migMsg', d.ok ? (d.msg || '已迁移') : (d.msg || '迁移失败'),
            d.ok ? 'ok' : 'err');
    if (d.ok) { toast(d.msg || '已迁移'); loadState(); }
  },
  async 'open-utpl'(el) {
    await openUpTpl(el.dataset.gid, el.dataset.uid);
  },
  async 'save-utpl'(el) {
    await saveUpTpl(el.dataset.gid, el.dataset.uid, false);
  },
  async 'clear-utpl'(el) {
    await saveUpTpl(el.dataset.gid, el.dataset.uid, true);
  },
  async 'add-season'() {
    const gid = $('seasonGroup') && $('seasonGroup').value;
    const uid = ($('seasonUid') && $('seasonUid').value || '').trim();
    const ref = ($('seasonRef') && $('seasonRef').value || '').trim();
    if (!gid) { showMsg('seasonMsg', '先选目标群', 'err'); return; }
    if (!ref) { showMsg('seasonMsg', '填合集链接或合集 ID', 'err'); return; }
    showMsg('seasonMsg', '正在订阅…');
    const d = await post('/api/season/add', { gid: gid, uid: uid, ref: ref });
    showMsg('seasonMsg', d.msg || (d.ok ? '已订阅' : '订阅失败'), d.ok ? 'ok' : 'err');
    if (d.ok) { toast(d.msg || '已订阅'); loadState(); }
  },
  async 'toggle-season'(el) {
    const on = !el.classList.contains('on');
    const d = await post('/api/season/set', {
      gid: el.dataset.gid, uid: Number(el.dataset.uid),
      season_id: Number(el.dataset.sid), on: on });
    if (d.ok) { toast(on ? '已开启' : '已关闭'); loadState(); }
    else toast(d.msg || '设置失败');
  },
  async 'remove-season'(el) {
    if (!confirm('取消订阅这个合集？')) return;
    const d = await post('/api/season/remove', {
      gid: el.dataset.gid, uid: Number(el.dataset.uid),
      season_id: Number(el.dataset.sid) });
    if (d.ok) { toast('已删除', 'ok'); loadState(); }
    else toast(d.msg || '删除失败');
  },
  async 'open-stpl'(el) {
    await openSeasonTpl(el.dataset.gid, el.dataset.uid, el.dataset.sid);
  },
  async 'save-stpl'(el) {
    await saveSeasonTpl(el.dataset.gid, el.dataset.uid, el.dataset.sid, false);
  },
  async 'clear-stpl'(el) {
    if (!confirm('清空这个合集的专属文案？之后它会用上一层（群 → 全局 → 默认）。')) return;
    await saveSeasonTpl(el.dataset.gid, el.dataset.uid, el.dataset.sid, true);
  },
  async 'add-group'() {
    const gid = $('newGid').value.trim();
    if (!gid) { showMsg('addGroupMsg', '先填群 ID', 'err'); return; }
    const d = await post('/api/group/add', { gid: gid, name: $('newGname').value.trim() });
    showMsg('addGroupMsg', d.ok ? '已登记 ✅' : (d.msg || '失败'), d.ok ? 'ok' : 'err');
    if (d.ok) { $('newGid').value = ''; $('newGname').value = ''; loadState(); }
  },
  async 'remove-group'(el) {
    const gid = el && el.getAttribute ? el.getAttribute('data-gid') : '';
    if (!gid) { toast('没认出是哪个群，请刷新页面重试', 'err'); return; }
    const gs = STATE.groups || [];
    const g = gs.find(x => x.gid === gid) || {};
    const nm = g.name || (String(gid).slice(0, 8) + '…');
    const n = (g.subs || []).length;
    if (!confirm('确定移除群「' + nm + '」？\n它的 ' + n
      + ' 条订阅和专属文案会一起删掉。')) return;
    const d = await post('/api/group/remove', { gid: gid });
    toast(d.ok ? (d.msg || '已移除') : (d.msg || '移除失败'), d.ok ? 'ok' : 'err');
    if (d.ok) loadState();
  },
  /* 「➕ 添加订阅 → 开启提醒」的四个小按钮。
     ⚠️ 以前是隐藏的 checkbox + label，没有任何代码同步 .on 样式：
        点下去值变了、界面纹丝不动，看着就是「失灵、没反馈」。
        现在样式和值一起改，并弹一句提示。 */
  'toggle-defkind'(el) {
    const KIND_NAME = { live: '直播', video: '投稿', dynamic: '动态',
                        top_comment: '置顶评论' };
    const kind = el.getAttribute('data-kind') || '';
    const on = !el.classList.contains('on');
    el.classList.toggle('on', on);
    try { toast((on ? '已开启「' : '已关闭「')
      + (KIND_NAME[kind] || kind) + '」提醒'); } catch (e) { /* 提示失败无所谓 */ }
  },
  'sub-search-clear'() {
    const el = $('subSearch');
    if (el && !el._missing) el.value = '';
    // 筛选方式一并复位，否则点了清空还是只看到一小撮群
    const fk = $('subFilterKind');
    if (fk && !fk._missing) fk.value = 'all';
    renderSubFilterPick();
    renderGroups(true);
    try { if (el && !el._missing) el.focus(); } catch (e) { /* 聚焦失败无所谓 */ }
  },
  async 'lookup'() {
    const uid = $('addUid').value.trim();
    if (!uid) { showMsg('addSubMsg', '先填 UID 或空间链接', 'err'); return; }
    $('lookupInfo').innerHTML = '<div class="up-card"><span class="spinner"></span> 查询中…</div>';
    const d = await api('/api/up/search?uid=' + encodeURIComponent(uid));
    if (!d.ok) {
      $('lookupInfo').innerHTML = '';
      showMsg('addSubMsg', d.msg || '查询失败', 'err');
      return;
    }
    LOOKUP = d.up || d;
    // 顺带回来的合集清单：点哪个就订哪个，不用再手动粘合集链接
    LOOKUP_SEASONS = d.seasons || [];
    LOOKUP_SERIES_N = d.series_n || 0;
    LOOKUP_SEA_ERR = d.season_err || '';
    const u = LOOKUP;
    $('lookupInfo').innerHTML =
      '<div class="up-card">' +
      (u.face ? '<img src="' + esc(proxyImg(u.face)) + '" alt="">' : '') +
      '<div><div class="n">' + esc(u.uname || '未知') + '</div>' +
      '<div class="u">UID ' + esc(u.uid) + (roomText(u.room_id, u.short_room_id)
        ? ' · 直播间 ' + esc(roomText(u.room_id, u.short_room_id)) : '') +
      '</div></div></div>';
    renderLookupPick();
    // 顺手把下面「订阅视频合集」的 UID 也填上：查都查了，别让用户再填一遍
    const su = $('seasonUid');
    if (su && !su._missing && !String(su.value || '').trim()) {
      su.value = String(u.uid || '');
    }

    // 按查询到的实际情况决定默认勾选，不无脑全开。
    // ⚠️ 之前只信 card 接口的 live_room，UP 没在播时那个字段经常是 null
    // → 明明有直播间却判定成 0 → 自动不勾「直播」→ 直播提醒彻底失效。
    // 后端已改成三级反查，这里也别再一刀切：
    //   查到房间号   → 勾上
    //   没查到       → 也勾上，但提示"没查到，开播时会自动反查"
    //   （勾了最多暂时不触发，不勾就完全没机会）
    // ⚠️ 现在是按钮不是 checkbox：改 .on 类，别再动 .checked
    setDefKind('defLive', true);
    setDefKind('defVideo', true);
    setDefKind('defDyn', true);
    // 置顶评论默认不勾：噪音相对大，要的话用户自己开
    setDefKind('defTopCmt', false);
    const tip = u.room_id
      ? '查询到了（直播间 ' + roomText(u.room_id, u.short_room_id)
        + '），默认开启三类'
      : '没查到直播间号（可能只是当前没在播）。仍默认勾选「直播」，'
        + '开播时会自动反查房间号；确认对方从不直播可以手动取消。';
    showMsg('addSubMsg', tip, u.room_id ? 'ok' : 'warn');
  },
  async 'add-sub'() {
    const gid = $('addGroup').value;
    const uid = $('addUid').value.trim();
    if (!gid) { showMsg('addSubMsg', '先选一个群', 'err'); return; }
    if (!uid) { showMsg('addSubMsg', '先填 UID', 'err'); return; }
    const d = await post('/api/sub/add', {
      gid: gid, uid: uid,
      uname: (LOOKUP && LOOKUP.uname) || '',
      room_id: (LOOKUP && LOOKUP.room_id) || 0,
      short_room_id: (LOOKUP && LOOKUP.short_room_id) || 0,
      face: (LOOKUP && LOOKUP.face) || '',
      kinds: {
        live: defKindOn('defLive', true),
        video: defKindOn('defVideo', true),
        dynamic: defKindOn('defDyn', true),
        top_comment: defKindOn('defTopCmt', false),
      },
    });
    showMsg('addSubMsg', d.ok ? (d.msg || '已添加') : (d.msg || '添加失败'),
            d.ok ? 'ok' : 'err');
    if (d.ok) { toast('订阅已添加', 'ok'); loadState(); }
  },

  /* 查询结果里点「💖 订阅这个 UP 主」：和上面的「💖 添加」走同一个接口，
     只是 uid / uname / 直播间号直接用查出来的那一份，不用手填。 */
  async 'pick-add-sub'() {
    const gid = ($('addGroup') || {}).value;
    const u = LOOKUP;
    if (!gid) { showMsg('addSubMsg', '先选一个群', 'err'); return; }
    if (!u || !u.uid) { showMsg('addSubMsg', '先点「🔍 查询」', 'err'); return; }
    const d = await post('/api/sub/add', {
      gid: gid, uid: String(u.uid),
      uname: u.uname || '', room_id: u.room_id || 0,
      short_room_id: u.short_room_id || 0, face: u.face || '',
      kinds: {
        live: defKindOn('defLive', true),
        video: defKindOn('defVideo', true),
        dynamic: defKindOn('defDyn', true),
        top_comment: defKindOn('defTopCmt', false),
      },
    });
    showMsg('addSubMsg', d.ok ? (d.msg || '已添加') : (d.msg || '添加失败'),
            d.ok ? 'ok' : 'err');
    if (d.ok) { toast('已订阅「' + (u.uname || u.uid) + '」', 'ok'); await loadState(); renderLookupPick(); }
  },

  /* 查询结果里点某个合集的「📦 订阅」：不用再粘合集链接，
     后端拿 uid + 纯数字 season_id 就够了（parse_season_ref 认纯数字）。 */
  async 'pick-add-season'(el) {
    const gid = ($('addGroup') || {}).value;
    const u = LOOKUP;
    const sid = el && el.getAttribute('data-sid');
    const title = (el && el.getAttribute('data-title')) || '';
    if (!gid) { showMsg('addSubMsg', '先选一个群', 'err'); return; }
    if (!u || !u.uid) { showMsg('addSubMsg', '先点「🔍 查询」', 'err'); return; }
    if (!sid) { showMsg('addSubMsg', '没认出合集 ID', 'err'); return; }
    const d = await post('/api/season/add', {
      gid: gid, uid: String(u.uid), ref: String(sid),
      title: title, uname: u.uname || '',
    });
    showMsg('addSubMsg', d.ok ? (d.msg || '已订阅') : (d.msg || '订阅失败'),
            d.ok ? 'ok' : 'err');
    if (d.ok) {
      toast('已订阅合集「' + (title || sid) + '」', 'ok');
      await loadState();
      renderLookupPick();
    }
  },

  'save-cfg': () => saveCfg(false),
  'save-tpl': () => saveTpl(),

  // 恢复默认：把所有输入框清空（留空即用默认）再保存
  async 'reset-tpl'() {
    TPL_FIELDS.forEach(([id]) => {
      const el = $(id);
      if (el && !el._missing) { el.value = ''; FORM_DIRTY[id] = false; }
    });
    await saveTpl();
    showMsg('tplMsg', '已恢复默认文案', 'ok');
  },

  async 'bili-browser-cancel'() {
    const d = await post('/api/bili/browser-cancel', {});
    toast(d.msg || (d.ok ? '已取消' : '取消失败'));
    if (BROWSER_TIMER) { clearInterval(BROWSER_TIMER); BROWSER_TIMER = null; }
    $('browserBox').innerHTML = '';
  },

  async 'bili-browser'() {
    const box = $('browserBox');
    showMsg('biliMsg', '正在启动浏览器，请稍候…');
    box.innerHTML = '<div class="qr-status"><span class="spinner"></span> 正在打开浏览器…</div>';
    const d = await post('/api/bili/browser-login', {});
    if (!d.ok) {
      box.innerHTML = '';
      showMsg('biliMsg', d.msg || '启动失败', 'err');
      return;
    }
    showMsg('biliMsg', '已打开浏览器，请在里面登录 B 站。登录成功后这里会自动更新。');
    if (BROWSER_TIMER) clearInterval(BROWSER_TIMER);
    BROWSER_TIMER = setInterval(pollBrowser, 2000);
  },
  async 'bili-refresh'() {
    const d = await post('/api/bili/refresh', {});
    showMsg('biliMsg', d.ok ? '已续期 ✅' : (d.msg || '续期失败'), d.ok ? 'ok' : 'err');
    if (d.ok) loadBili();
  },
  async 'bili-logout'() {
    const d = await post('/api/bili/logout', {});
    showMsg('biliMsg', d.ok ? '已退出登录' : (d.msg || '失败'), d.ok ? 'ok' : 'err');
    if (d.ok) loadBili();
  },
  async 'save-cookie'() {
    const t = $('cookieText').value.trim();
    if (!t) { showMsg('biliMsg', '先粘贴 cookie', 'err'); return; }
    const d = await post('/api/bili/cookie', { cookie: t });
    showMsg('biliMsg', d.ok ? '已保存 ✅' : (d.msg || '保存失败'), d.ok ? 'ok' : 'err');
    if (d.ok) { $('cookieText').value = ''; loadBili(); }
  },
  async 'set-pwd'() {
    const v = $('newPwd').value;
    const d = await post('/api/security/password', { action: 'set', new: v });
    showMsg('pwdMsg', d.ok ? '口令已保存' : (d.msg || '失败'), d.ok ? 'ok' : 'err');
    if (d.ok) { $('newPwd').value = ''; loadSecurity(); }
  },
  'refresh-log': () => loadLog(),

  'toggle-log-order': () => {
    LOG_ORDER = LOG_ORDER === 'asc' ? 'desc' : 'asc';
    try { localStorage.setItem('biliLogOrder', LOG_ORDER); } catch (e) { /* 存不了也照样能排 */ }
    renderLog();
  },
  async 'clear-log'() {
    if (!confirm('清空所有日志文件？此操作不可恢复。')) return;
    const d = await post('/api/log/clear', {});
    if (d.ok) { toast('已清空', 'ok'); loadLog(); }
    else toast(d.msg || '清空失败');
  },
};

/* 动态元素上的操作（通过事件委托读到 dataset） */
const ROW_ACTIONS = {
  // ---------- 数据与备份 ----------
  /* ⚠️ 置灰/换文字交给统一机制（beginBusy/endBusy）：
     这里以前自己改 textContent，恢复用的还是异步开始前的旧值，
     跟统一机制的恢复叠在一起会互相覆盖，按钮最后卡在"备份中…"。 */
  async 'backup-now'(el) {
    const r = await post('/api/backup/export', {});
    showMsg('bkMsg', r.msg || (r.ok ? '已备份' : '备份失败'),
            r.ok ? 'ok' : 'err');
    if (r.ok) toast('已备份（设定 + 凭据 + 订阅）', 'ok');
    loadBackupInfo();
  },
  'backup-download'() {
    window.location.href = '/api/backup/download';
  },
  async 'datadir-apply'() {
    const inp = document.getElementById('bkHome');
    const p = (inp && inp.value || '').trim();
    if (!p) { showMsg('bkMsg', '先填一个文件夹路径', 'err'); return; }
    if (!confirm('把配置、订阅、密钥、日志、图片和备份一起搬到：\n' + p
                 + '\n\n搬完需要重启机器人才生效。继续？')) return;
    const r = await post('/api/data/home', { path: p });
    showMsg('bkMsg', r.msg || (r.ok ? '已搬好' : '搬迁失败'), r.ok ? 'ok' : 'err');
    if (r.ok) { toast('数据已搬到新位置，重启后生效', 'ok'); loadBackupInfo(); }
  },
  async 'legacy-import'(el) {
    const p = el.dataset.path || '';
    if (!p) return;
    if (!confirm('把这份旧数据合并进来？\n\n已有的群、订阅、文案一律不变，'
                 + '只补缺失的；旧库里的推送进度不带过来。')) return;
    const r = await post('/api/data/legacy/import', { path: p });
    const sm = r.summary || {};
    showMsg('lgMsg', r.msg || (r.ok
      ? `已合并：群 ${sm.groups || 0} 个 · 订阅 ${sm.subs || 0} 条 · `
        + `合集 ${sm.seasons || 0} 个`
      : '导入失败'), r.ok ? 'ok' : 'err');
    if (r.ok) { toast('旧数据已合并', 'ok'); loadState(); loadLegacyInfo(); }
  },
  'backup-restore'() {
    const inp = document.getElementById('bkFile');
    if (!inp) return;
    inp.value = '';
    inp.onchange = async () => {
      if (!inp.files || !inp.files[0]) return;
      if (!confirm('用这个备份覆盖当前的设定、凭据和订阅？\n'
                   + '（恢复前会自动给现在的文件留一份底）')) return;
      const fd = new FormData();
      fd.append('file', inp.files[0]);
      const r = await api('/api/backup/restore',
                          { method: 'POST', body: fd, timeout: 60000 });
      showMsg('bkMsg', r.msg || (r.ok ? '已恢复' : '恢复失败'),
              r.ok ? 'ok' : 'err');
      if (r.ok) { toast('已恢复，重启机器人后生效', 'ok'); loadState(); }
    };
    inp.click();
  },
  async 'toggle-sub'(el) {
    const gid = el.dataset.gid, uid = el.dataset.uid, kind = el.dataset.kind;
    const g = (STATE.groups || []).find(x => x.gid === gid);
    const s = g && (g.subs || []).find(x => String(x.uid) === String(uid));
    const cur = s && s.kinds && s.kinds[kind] ? !!s.kinds[kind].on : false;
    await post('/api/sub/set', { gid: gid, uid: uid, kind: kind, on: !cur });
    loadState();
  },
  async 'remove-sub'(el) {
    if (!confirm('取消这个 UP 主在本群的全部订阅？')) return;
    await post('/api/sub/remove', { gid: el.dataset.gid, uid: el.dataset.uid });
    toast('已取消订阅', 'ok');
    loadState();
  },
  // 点群名展开/收起该群订阅
  'toggle-group'(el) {
    const gid = el.getAttribute('data-gid');
    if (EXPANDED.has(gid)) {
      EXPANDED.delete(gid);
      /* 收起即复位：下次再展开回到「👤 UP 主」栏。
         不复位的话，上次停在「📦 合集」栏会一直被记住，
         收起再展开还是合集栏 —— 反馈「折叠后希望回到 UP 主栏」。 */
      delete MINI_TAB['grp:' + gid];
    } else EXPANDED.add(gid);
    /* ⚠️ 不调 renderGroups()：整块重画会**把展开动画吃掉**。
       重画出来的卡片是带着 .open 一起插入 DOM 的，浏览器把它当
       初始状态，grid-template-rows 的过渡根本没有起点可插值 ——
       所以点开是"啪"一下硬切，看着像没有动画。
       这里只改这一张卡片的 class，让 CSS 自己过渡。
       找不到卡片（理论上不会）才退回整块重画。 */
    const card = el.closest ? el.closest('.grp') : null;
    if (card) {
      card.classList.toggle('open', EXPANDED.has(gid));
      if (!EXPANDED.has(gid)) {
        /* 收起后把内部高度交还给 CSS：动画期间由 JS 撑着，
           收完必须清掉，否则下次展开量到的是旧高度。 */
        const body = card.querySelector('.grp-body');
        if (body) { body.style.height = ''; body.style.transition = ''; }
      }
    } else renderGroups();
  },
  'cl-toggle'(el) {
    const item = el.closest('.cl-item');
    if (!item) return;
    const open = item.classList.toggle('open');
    sfx(open ? 'unfold' : 'fold');
  },

  /* 界面音效档位。存浏览器本地，不进订阅数据。
     选完立刻试听一声，用户好判断"这个音量吵不吵"。 */
  'sound-change'(el) {
    const v = el.value || 'off';
    try {
      if (window.Sound && window.Sound.setLevel) {
        window.Sound.setLevel(v === 'off' ? null : v);
      }
    } catch (_) {}
    syncSoundSelect();
    safe('swCard', syncSwCardSum);
    if (v !== 'off') sfx('done');
    const label = (el.selectedOptions && el.selectedOptions[0] &&
                   el.selectedOptions[0].textContent) || v;
    toast(v === 'off' ? '界面音效已关闭' : ('界面音效：' + label));
  },
};

/* ---------- 点完要等的按钮：统一上锁 + 进度提示 ---------- */
/* ⚠️ 以前只有概况页那几个按钮做了置灰，其余几十个点了就没动静：
   后端其实在跑，界面上一片安静，于是反复点，同一个请求发出去 N 遍。
   这里在**分发处**统一兜住 —— 凡是动作返回 Promise（要等后端的）
   都自动置灰 + 显示进度，不用逐个按钮改，以后新加的按钮也自动生效。

   两个时间点刻意分开：
     · 置灰**立刻** —— 防重复点击，这是关键
     · 进度提示**延迟 160ms** —— 几十毫秒就回来的操作（切页、插变量）
       完全不显示，不会满屏闪"…中…"
*/
const BUSY_DELAY = 160;
/* 逐动作的过程文案。没列到的按按钮自身文字推一个，"处理中…"兜底。
   写"正在…"而不是"…中"：前者明确表示还在办，后者像卡住了。 */
const ACT_WAIT = {
  'check-now': '正在检测所有订阅…',
  'refresh-state': '正在刷新状态…',
  'refresh-names': '正在刷新群名…',
  'refresh-media': '正在刷新图片…',
  'refresh-log': '正在刷新日志…',
  'save-cfg': '正在保存设置…',
  'save-tpl': '正在保存文案…',
  'save-cookie': '正在保存 Cookie…',
  'save-chk-uid': '正在保存…',
  'set-pwd': '正在设置密码…',
  'clear-pwd': '正在清除密码…',
  'backup-now': '正在备份，别关窗口…',
  'backup-restore': '正在恢复备份…',
  'backup-download': '正在准备下载…',
  'datadir-apply': '正在搬家，完成后会自动重启…',
  'diagnose': '正在诊断…',
  'add-sub': '正在添加订阅…',
  'add-season': '正在添加合集…',
  'add-group': '正在登记群…',
  'lookup': '正在查询…',
  'test-push': '正在发送测试…',
  'push-test': '正在发送测试…',
  'mig-pick': '正在读取订阅…',
  'mig-scope': '正在读取…',
  'mig-copy': '正在复制订阅…',
  'mig-move': '正在迁移订阅…',
  'mig-seasons': '正在迁移合集…',
  'media-probe': '正在探测图床…',
  'media-test-upload': '正在上传测试图…',
  'media-clear': '正在清理图片缓存…',
  'clean-procs': '正在清理残留进程…',
  'clear-log': '正在清理日志…',
  'restart-bot': '正在重启机器人…',
  'dump-debug': '正在导出排查包…',
  'unban-ip': '正在解封…',
  'bili-browser': '正在打开浏览器登录…',
  'bili-refresh': '正在刷新登录态…',
  'bili-logout': '正在退出登录…',
  'legacy-import': '正在导入旧版数据…',
  'selfcheck': '正在自检…',
};

function waitLabel(act, el) {
  const m = ACT_WAIT[act];
  if (m) return m;
  let raw = '';
  try {
    raw = (el && (el.getAttribute('data-wait') || el.textContent)) || '';
  } catch (_) {}
  /* 剥掉 emoji 和空白再取前 10 个字：
     按钮原文多是"💾 立即备份"，直接拿来用会带一堆图标和换行。 */
  const t = String(raw)
    .replace(/[\uD800-\uDFFF\u2190-\u27BF\uFE0F\u200D\s]/g, '')
    .slice(0, 10);
  return t ? ('正在' + t + '…') : '正在处理…';
}

/* 开始等待：立刻置灰挡住重复点击，视觉提示延迟出现。
   ⚠️ 置灰用属性而不是 class：a/div 之类也能挡住。
   ⚠️ 原文存的是 innerHTML —— 不少按钮里带图标，只存文字会丢图标。 */
function beginBusy(el, act, snap) {
  if (!el) return false;
  // 已经在处理中：这次点击直接作废，不再发第二次请求
  if (el.getAttribute('data-busy') === '1') return false;
  el.setAttribute('data-busy', '1');
  try {
    /* ⚠️ 用**点击那一刻**的快照（snap）恢复，而不是此刻的 innerHTML：
       动作已经跑起来了，它可能已经改过按钮文字，
       此刻读到的就不是原文，恢复时会把"处理中"之类的字留在按钮上。 */
    el._busyHtml = (snap != null) ? snap : el.innerHTML;
    el.disabled = true;
    el.setAttribute('aria-busy', 'true');
    if (el.classList) el.classList.add('is-busy');
  } catch (_) {}
  const lb = waitLabel(act, el);
  el._busyLabel = lb;                 // 结束时按这条内容收掉进度提示
  el._busyTimer = setTimeout(function () {
    el._busyShown = true;
    try {
      el.textContent = lb;
      if (el.classList) el.classList.add('is-busy-on');
    } catch (_) {}
    topLoad(true);
    toast(lb, '', true);
  }, BUSY_DELAY);
  return true;
}

/* 结束等待。⚠️ 必须无条件恢复：动作抛异常也要解锁，
   否则按钮永久置灰，只能刷新页面（finally 语义）。 */
function endBusy(el) {
  if (!el) return;
  try { clearTimeout(el._busyTimer); } catch (_) {}
  el.removeAttribute('data-busy');
  try {
    el.disabled = false;
    el.removeAttribute('aria-busy');
    if (el.classList) el.classList.remove('is-busy', 'is-busy-on');
  } catch (_) {}
  if (el._busyShown) {
    el._busyShown = false;
    // 动作自己会弹结果提示，这里只收掉"还在办"的那条
    clearStickyToast(el._busyLabel || '');
    topLoad(false);
    try { if (el._busyHtml != null) el.innerHTML = el._busyHtml; } catch (_) {}
  }
}

/* 分发一个动作，并给它套上等待反馈。
   返回 Promise 的（要等后端）才上锁；同步动作原样跑，不打扰。 */
function runAct(act, el, fn) {
  // 点下的瞬间就把原文留住：动作里有同步代码会改按钮
  let snap = null;
  try { if (el) snap = el.innerHTML; } catch (_) {}
  let r;
  try {
    r = fn(el);
  } catch (err) {
    return Promise.reject(err);
  }
  if (!r || typeof r.then !== 'function') return null;   // 同步：不上锁
  beginBusy(el, act, snap);
  return r.then(function (v) { endBusy(el); return v; },
                function (e) { endBusy(el); throw e; });
}

/* 异步动作的异常在外面 try/catch 是抓不到的（它发生在下一个微任务），
   所以在这里用 .catch 兜住 —— 否则失败时按钮只是悄悄恢复原状，
   界面上什么提示都没有。 */
function _guardAct(act, el, fn) {
  let p;
  try {
    p = runAct(act, el, fn);
  } catch (err) {
    _actFail(act, err);
    return;
  }
  if (p && typeof p.catch === 'function') {
    p.catch(function (err) { _actFail(act, err); });
  }
}

function _actFail(act, err) {
  try {
    window.__ACT_FAILS = window.__ACT_FAILS || [];
    const m = (err && (err.message || String(err))) || '未知错误';
    window.__ACT_FAILS.push(act + '：' + m);
    if (typeof toast === 'function') toast('「' + act + '」出错了：' + m, 'err');
    console.error('[act:' + act + ']', err);
  } catch (_) { /* 兜底不能再抛 */ }
}

/* ---------- 事件委托 ---------- */
/* ⚠️ 只装一次：脚本解析时就装，DOMContentLoaded 里再调会跳过。
   以前只在 DOMContentLoaded 里注册 —— 一旦那个回调没跑到这一步
   （或压根没触发），**整页所有按钮全部哑掉**，而页面上毫无提示。
   document 在脚本执行时就已经存在，所以可以直接装。 */
var DELEGATES_ON = false;
function initDelegates() {
  if (DELEGATES_ON) return;
  DELEGATES_ON = true;
  /* 音效用单独一个监听：开关滑块、普通按钮大多没有 data-act，
     走不到下面那个动作分发里。
     ⚠️ 刻意只给两类响 —— 主按钮一声极轻的短音、开关一声拨动声。
        列表项、链接、标签页一律不响，否则点哪儿都出声会非常吵。 */
  document.addEventListener('click', e => {
    const t = e.target;
    if (!t || !t.closest) return;
    if (t.closest('.sw-btn')) { sfx('toggle'); return; }
    if (t.closest('.btn.pri')) sfx('click');
  });

  document.addEventListener('click', e => {
    const el = e.target.closest ? e.target.closest('[data-act]') : null;
    if (!el) return;
    const act = el.getAttribute('data-act');
    /* ⚠️ 动作内部的异常必须看得见。
       以前动作里一抛错就冒泡成"未捕获异常"，按钮看着只是"点了没反应"，
       控制台里那行报错用户根本不会去看。这里兜住并弹提示，
       顺便记进 window.__ACT_FAILS，方便把原文发过来定位。 */
    try {
      if (ROW_ACTIONS[act]) {
        e.preventDefault();
        _guardAct(act, el, ROW_ACTIONS[act]);
        return;
      }
      // ⚠️ 必须把 el 传进去。
      // 以前调用 ACTIONS 里的动作时没带参数，于是所有靠 el.dataset
      // 取 gid / uid 的动作拿到的是 undefined，一访问就 TypeError。
      // 表现就是"按钮点了没反应"，而且报错还看不懂。
      // 不需要 el 的动作多收一个参数也无害（JS 允许多传）。
      if (ACTIONS[act]) {
        e.preventDefault();
        _guardAct(act, el, ACTIONS[act]);
      }
    } catch (err) {
      try {
        window.__ACT_FAILS = window.__ACT_FAILS || [];
        var m = (err && (err.message || String(err))) || '未知错误';
        window.__ACT_FAILS.push(act + '：' + m);
        if (typeof toast === 'function') toast('「' + act + '」出错了：' + m, 'err');
        console.error('[act:' + act + ']', err);
      } catch (_) { /* 兜底不能再抛 */ }
    }
  });

  /* 群内细分（UP 主 / 合集）：切换范围**只限本群卡片内**，
     不能跨群 —— 每个群有自己的 UP 主和合集，混着切会张冠李戴。
     查询结果里那块「UP 主 / 合集」不在 .grp 里，就退回 #lookupInfo，
     同样是只切自己这一块。 */
  document.addEventListener('click', e => {
    const t = e.target.closest ? e.target.closest('.mini-tab') : null;
    if (!t) return;
    const card = t.closest('.grp') || t.closest('#lookupInfo') ||
                 t.closest('#migPick');
    if (!card) return;
    e.preventDefault();
    /* ⚠️ 记住这一栏，供重画时还原（见 MINI_TAB 的说明）。
       群卡片用 gid 当键：列表重画后 DOM 节点全换了新的，
       只靠 class 是留不住的。 */
    const mvNow = t.getAttribute('data-mv');
    if (mvNow) {
      if (card.classList.contains('grp')) {
        MINI_TAB['grp:' + (card.getAttribute('data-gid') || '')] = mvNow;
      } else if (card.id) {
        MINI_TAB[card.id === 'lookupInfo' ? 'lk' : card.id] = mvNow;
      }
    }
    card.querySelectorAll('.mini-tab').forEach(x => x.classList.remove('on'));
    t.classList.add('on');
    card.querySelectorAll('.mini-view').forEach(v => {
      const on = v.id === t.getAttribute('data-mv');
      v.classList.toggle('on', on);
      v.classList.toggle('show', on);
    });
  });

  /* 群名输入框：失焦即保存
     ⚠️ 这里以前写的是 `if (fn) fn(el)`，而 fn 在本作用域**根本没有定义** ——
        一旦页面上出现 .group-name 输入框、失焦就抛 ReferenceError，
        表现就是"点了别处没反应"。群名实际是「🏷️ 刷新群名」从官方拉的，
        没有可编辑输入框，所以整段都是死代码，直接去掉。 */
}

/* ---------- 杂项 ---------- */
function initLogControls() {
  /* 「中文翻译」开关已挪到 设置→通用→提醒开关（#cLogTrans），
     日志页不再放开关 —— 所以这里不再绑定 logTrans。
     改设置那边会走 saveCfg → loadLog，日志页即时跟着变。 */
  const g = $('cLogTrans');
  if (g && !g._missing) g.addEventListener('change', function () {
    safe('log', loadLog);
  });
  // 更新日志条数：三档按钮。
  // ⚠️ 以前 $('clCount') 是 number 输入框，现在换成了按钮组 ——
  //    还去读 .value 会拿到空串，条数一直回退成默认 5，切了没反应。
  const box = $('clCount');
  if (box) {
    box.addEventListener('click', e => {
      const b = e.target.closest ? e.target.closest('.cl-tab') : null;
      if (!b) return;
      box.querySelectorAll('.cl-tab').forEach(x => x.classList.remove('on'));
      b.classList.add('on');
      CL_LIMIT = b.getAttribute('data-cl') || '5';
      loadVersion();
    });
  }
}

function initTplPreview() {
  ['tLive', 'tVideo', 'tDyn', 'tDynNo', 'tDynPin', 'tDynPinNo'].forEach(id => {
    const el = $(id);
    if (!el || el._missing) return;
    el.addEventListener('input', renderTplPreview);
  });
}



/* ---------- 启动 ---------- */
function safe(name, fn) {
  try { fn(); } catch (e) { console.error('[' + name + ']', e); bootFail(name, e); }
}

/* ⚠️ 以前 safe() 只往 console.error 写，页面上**一点提示都没有**。
   结果任何一步初始化失败 → 事件委托没注册 → 所有按钮点了没反应，
   而用户完全看不出原因，只能报"都点不了"。
   现在把失败项显示在页面顶部的红色横幅里，并写进 window.__BOOT_FAILS
   （自检页也会读它），一眼能看出是哪一步挂了。 */
window.__BOOT_FAILS = [];
function bootFail(name, e, refreshable) {
  try {
    var msg = (e && (e.message || String(e))) || '未知错误';
    window.__BOOT_FAILS.push(name + '：' + msg);
    var box = document.getElementById('bootErr');
    if (!box) {
      box = document.createElement('div');
      box.id = 'bootErr';
      box.style.cssText = 'position:fixed;left:0;right:0;top:0;z-index:99999;' +
        'background:#c0392b;color:#fff;padding:10px 14px;font:13px/1.6 system-ui;' +
        'box-shadow:0 2px 12px rgba(0,0,0,.4)';
      document.body.appendChild(box);
    }
    // 版本不一致时给一个真能用的按钮：带时间戳绕开缓存重载，
    // 比让用户自己按 Ctrl+F5 靠谱（有些浏览器/企业环境根本刷不掉）。
    var btn = '';
    if (refreshable) {
      btn = '<button id="bootReload" style="position:absolute;right:12px;' +
        'top:10px;padding:4px 10px;border:0;border-radius:6px;' +
        'background:#fff;color:#c0392b;font-weight:600;cursor:pointer">' +
        '强制重载页面</button>';
    }
    box.innerHTML = btn +
      '<b>⚠️ 面板初始化有步骤失败，部分按钮可能点不动</b><br>' +
      window.__BOOT_FAILS.map(function (s) {
        return '· ' + String(s).replace(/</g, '&lt;');
      }).join('<br>');
    var rb = document.getElementById('bootReload');
    if (rb) {
      rb.onclick = function () {
        try { location.replace('/?_=' + Date.now()); } catch (_) {
          try { location.reload(true); } catch (__) { location.reload(); }
        }
      };
    }
  } catch (_) { /* 显示失败也不能再抛 */ }
}

/* 本份 app.js 的版本，用来和后端版本比对。
   ⚠️ 两者不一致只有两种可能：
     ① 浏览器缓存了旧脚本（Ctrl+F5 强刷即可）
     ② 8088 上跑的是**另一个旧版面板** —— 新面板启动时发现端口被占，
        会自动改用 8089/8090…，而你打开的还是 8088 那个旧面板。
        旧面板里根本没有新版加的按钮和接口，于是"点了没反应"。
   这两种情况以前完全无从区分，现在直接把结论写在页面顶部。 */
/* ⚠️ 以前这里写死版本号，打包时忘了同步就会永远比后端低，
   于是页面顶部一直挂着「版本不一致」的红色横幅 —— 那是我自己
   制造的误报，还把排查方向带偏了。现在由后端注入（见 index.html
   的 window.__BE_VER），两边不可能对不上；取不到才用兜底值。 */
window.__APP_VER = window.__BE_VER || '';

/* 脚本一解析就装上事件委托，不等 DOMContentLoaded ——
   那个回调要是没跑到，整页按钮全哑且毫无提示。 */
try { initDelegates(); } catch (e) { bootFail('delegates', e); }

function checkJsVersion(beVer) {
  try {
    var be = String(beVer || '').replace(/^v/i, '').trim();
    var fe = String(window.__APP_VER || '').replace(/^v/i, '').trim();
    // ⚠️ 前端版本拿不到（旧 HTML / 注入缺失）时**不报警**。
    //    以前兜底成写死的 '1.45.0'，于是每次打包后横幅必弹、还把
    //    排查方向带偏 —— 那是误报，不是真的版本不一致。
    if (!be || !fe || be === fe) return;
    bootFail('版本不一致',
      '前端脚本 v' + fe + ' / 后端 v' + be +
      ' —— 点右边按钮强制重载；重载后仍不一致，说明这个端口上跑的是另一个'
      + '旧版面板（新版因端口被占用改用了其它端口，看启动窗口里写的真实端口）',
      true);
  } catch (_) { /* 兜底不能再抛 */ }
}

/* ============================================================
   动效（v1.22.0）
   全部是"有物理感"的：跟随指针的光、轻微 3D 倾斜、按压反馈。
   刻意不做条纹滚动渐变 —— 那种很生硬。
   系统开了「减少动效」就整套跳过。
   ============================================================ */
const FX_ON = !(window.matchMedia &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches);
// 触屏没有 hover，做了 tilt 反而会粘住，直接跳过
const FX_HOVER = FX_ON && (window.matchMedia &&
  window.matchMedia('(hover: hover)').matches);

/* 品牌 logo 底纹：图不随包带了，由后端 /api/brand/logo 给出地址
   （默认取仓库里那张，可用 ONOBN_LOGO_URL 换，或设 off 彻底不显示）。
   ⚠️ 必须用 JS 探测：CSS 里 --logo 默认是 none，
      只有图片**真的加载成功**才把 URL 设上去。
      直接写死 url() 的话，取不到图时会多发一次失败请求；
      探测则干净，且失败时连请求都不留。
   ⚠️ 远程取不到（离线、或 CDN 被拦）时 onerror 触发，保持 none：
      不显示、不报错、不留空白块 —— 断网也照常能用面板。 */
function initLogo() {
  // v1.93.0：优先数据目录 images/ui/logo.png（换新版不会被删），
  // 没有自定义图时接口自动回落到随包自带的那张。
  const url = '/api/brand/logo';
  const img = new Image();
  img.onload = function () {
    document.documentElement.style.setProperty('--logo', 'url("' + url + '")');
  };
  img.onerror = function () { /* 没有 logo.png：保持 none，不显示任何东西 */ };
  img.src = url;
}

/* 指针柔光：目标值跟真实鼠标走，显示值慢慢追过去（lerp），
   所以光是"飘"过去的，不是硬跟着跳 —— 这是不生硬的关键 */
function initGlow() {
  if (!FX_HOVER) return;
  const el = $('bgGlow');
  if (!el || el._missing) return;
  let tx = (window.innerWidth || 1280) / 2,
      ty = (window.innerHeight || 800) * 0.28,
      cx = tx, cy = ty, raf = 0, idle = 0, on = false;
  // ⚠️ 坐标改用 px：光斑现在是固定大小的圆 + translate3d 位移，
  //    不再用整屏渐变重绘（见 index.html 里 .bg-glow::before/::after）。
  const apply = () => {
    el.style.setProperty('--gx', cx.toFixed(1) + 'px');
    el.style.setProperty('--gy', cy.toFixed(1) + 'px');
    el.style.setProperty('--gx2', (window.innerWidth - cx).toFixed(1) + 'px');
    el.style.setProperty('--gy2', (window.innerHeight - cy).toFixed(1) + 'px');
  };
  window.addEventListener('pointermove', (e) => {
    tx = e.clientX;
    ty = e.clientY;
    if (!on) { on = true; el.classList.add('on'); }
    idle = 0;
    // 收敛后循环会自己停，鼠标再动要重新点起来
    if (!raf) raf = requestAnimationFrame(loop);
  }, { passive: true });
  const loop = () => {
    cx += (tx - cx) * 0.055;
    cy += (ty - cy) * 0.055;
    apply();
    // 长时间不动就淡出，避免一直亮着晃眼
    if (++idle > 240 && on) { el.classList.remove('on'); on = false; }
    /* ⚠️ 关键：追上目标就**停掉 rAF**。
       以前这个循环无条件一直跑，鼠标不动也每帧重绘整屏渐变，
       是"动画卡"的最大来源 —— 现在只有真在飘的时候才占帧。 */
    if (Math.abs(tx - cx) < 0.6 && Math.abs(ty - cy) < 0.6) { raf = 0; return; }
    raf = requestAnimationFrame(loop);
  };
  window.addEventListener('beforeunload', () => cancelAnimationFrame(raf));
}

/* 卡片：轻微 3D 倾斜 + 高光跟着指针跑 */
function initTilt() {
  if (!FX_HOVER) return;
  const MAX = 5;                      // 最大倾斜角度，再大就浮夸了
  document.addEventListener('pointerover', (e) => {
    const card = e.target.closest && e.target.closest('.kpi, .tile, .nav-i');
    if (!card) return;
    if (card._tiltBound) return;
    card._tiltBound = true;
    card.classList.add('fx-tilt');
    card.addEventListener('pointermove', (ev) => {
      const r = card.getBoundingClientRect();
      const px = (ev.clientX - r.left) / r.width;
      const py = (ev.clientY - r.top) / r.height;
      card.style.setProperty('--rx', ((0.5 - py) * MAX * 2).toFixed(2) + 'deg');
      card.style.setProperty('--ry', ((px - 0.5) * MAX * 2).toFixed(2) + 'deg');
      card.style.setProperty('--mx', (px * 100).toFixed(1) + '%');
      card.style.setProperty('--my', (py * 100).toFixed(1) + '%');
    }, { passive: true });
    card.addEventListener('pointerleave', () => {
      card.style.setProperty('--rx', '0deg');
      card.style.setProperty('--ry', '0deg');
    });
  }, { passive: true });
}

/* 点击涟漪：从按下的那一点扩散开 */
function initRipple() {
  if (!FX_ON) return;
  document.addEventListener('pointerdown', (e) => {
    const t = e.target.closest && e.target.closest(
      'button, .chip, .btn-primary, .nav-i');
    if (!t || t._missing) return;
    if (getComputedStyle(t).position === 'static') t.style.position = 'relative';
    const r = t.getBoundingClientRect();
    const d = Math.max(r.width, r.height);
    const sp = document.createElement('span');
    sp.className = 'ripple';
    sp.style.width = sp.style.height = d + 'px';
    sp.style.left = (e.clientX - r.left - d / 2) + 'px';
    sp.style.top = (e.clientY - r.top - d / 2) + 'px';
    t.appendChild(sp);
    setTimeout(() => sp.remove(), 640);
  }, { passive: true });
}

/* 刷新类按钮：点一下转一圈，明确"在动了"。
   ⚠️ .spin-once 只写 CSS 而没有元素用的话又是死样式，
      这里统一给所有 data-act 以 refresh 开头的按钮挂上。 */
function initSpinRefresh() {
  if (!FX_ON) return;
  document.addEventListener('click', (e) => {
    const t = e.target.closest && e.target.closest('button[data-act^="refresh"]');
    if (!t || t._missing) return;
    t.classList.add('spin-once');
    setTimeout(() => { try { t.classList.remove('spin-once'); } catch (err) {} }, 660);
  }, { passive: true });
}

/* 滚动渐入：进入视口才浮上来，长列表不会一次性全糊上来 */
let _io = null;
function initReveal() {
  if (!FX_ON || !('IntersectionObserver' in window)) return;
  if (!_io) {
    _io = new IntersectionObserver((entries) => {
      entries.forEach((en) => {
        if (en.isIntersecting) {
          en.target.classList.add('in');
          _io.unobserve(en.target);
        }
      });
    }, { threshold: 0.06, rootMargin: '0px 0px -8% 0px' });
  }
  document.querySelectorAll('.blk:not(.rv), .tile:not(.rv)').forEach((el) => {
    // 已经在视口里的别再动画一次，直接显示
    const r = el.getBoundingClientRect();
    if (r.top < window.innerHeight && r.bottom > 0) return;
    el.classList.add('rv', 'reveal');
    _io.observe(el);
  });
}

/* 数字变化：从旧值滚到新值，不直接跳 */
function countUp(el, to) {
  if (!el || el._missing) return;
  const from = parseInt(el.dataset.v || '0', 10);
  const target = parseInt(to, 10);
  if (!Number.isFinite(target)) { el.textContent = to; return; }
  el.dataset.v = String(target);
  if (!FX_ON || from === target) { el.textContent = String(target); return; }
  // 数字真的变了才脉冲一下，提示"这个值刚变过"。
  // ⚠️ .fx-pulse 以前定义了却没有任何元素用（死样式），这里才真正接上；
  //    跑完自删，不然每次重画都会再抖一遍。
  if (el.classList && el.classList.add) {
    el.classList.add('fx-pulse');
    setTimeout(() => { try { el.classList.remove('fx-pulse'); } catch (e) {} }, 660);
  }
  const t0 = performance.now(), dur = 520;
  const step = (t) => {
    const k = Math.min(1, (t - t0) / dur);
    const e = 1 - Math.pow(1 - k, 3);          // easeOutCubic
    el.textContent = String(Math.round(from + (target - from) * e));
    if (k < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

window.addEventListener('DOMContentLoaded', () => {
  safe('logo', initLogo);
  safe('glow', initGlow);
  safe('tilt', initTilt);
  safe('ripple', initRipple);
  safe('spinRefresh', initSpinRefresh);
  safe('theme', initTheme);
  safe('sheets', initSheets);
  safe('delegates', initDelegates);
  safe('dirty', initFormDirty);
  safe('sw', initSwitches);
  safe('logCtl', initLogControls);
  safe('tplPrev', initTplPreview);
  safe('reveal', initReveal);
  safe('tips', initTips);
  // 自检六行：打开页面先铺成「未检测」，点「重新自检」才填真实参数
  safe('diag', function () { renderDiag(null); });

  /* ⚠️ 启动时必须显式切到概况：所有 .sec 默认 display:none，
     只靠 HTML 上的 show 兜底不够（刷新后要回到概况、且要触发数据加载）。 */
  safe('boot', function () { openSec('home'); });

  loadState();
  safe('security', loadSecurity);
  safe('audit', loadAudit);
  safe('media', loadMedia);
  safe('harden', loadHarden);
  safe('testopt', restoreTestOpt);
  safe('bili', loadBili);
  safe('log', loadLog);
  safe('run', loadRun);
  safe('version', loadVersion);

  setInterval(loadState, 15000);
  setInterval(() => { safe('log', loadLog); }, 10000);
  // 运行状态里的「实际轮询间隔 / 已推送今日 / 心跳」都会变，
  // 跟着状态一起刷，不然页面上永远是打开那一刻的快照
  setInterval(() => { safe('run', loadRun); }, 15000);
});

/* ══════════════════════════════════════════════════════════════
   v1.31.0 五大板块：顶部状态条 / 心跳 / 概况 / 凭据 / 推送测试
   ══════════════════════════════════════════════════════════════ */

/* ---------- 心跳波形 ----------
   形态：真实 ECG。全站只有这里 + 进度条保留渐变/曲线，其余已单色化。

   语义（⚠️ 用户多轮确认，写死在这里，别再改回去）：
   ① **搏动固定 1 秒一次**，与轮询间隔**无关**：
      轮询点**缩小**、波形框**放大**，两者**同一拍**（同一批百分比）。
      —— 之前把搏动周期做成"= 轮询间隔"，8s 一轮时 8 秒才跳一下，
         看着像卡住；用户要的是恒定 1 秒。
   ② **滚动速度 = 轮询间隔的反比**（间隔越大 → 滚得越慢），浮点、无级：
      speed(px/s) = HB_FLOW_REF / dur，基准 96（dur=8 → 12px/s）。
      ⚠️ 方向别再弄反：早先做成正比（间隔越大滚得越快），被指出不对。
      ⚠️ 基准别再调大：24px/s 时用户就反馈"8 秒档看着好快"。
   ③ **波长（= 波形密度）必须和速度解耦**，各算各的：
      ⚠️ 以前 wl = speed × 1 秒，于是"每秒正好滚过一个波群"，
         而搏动也是 1 秒一次 → 每搏动一次波形整体换一格，
         用户看到的就是"动一下删一个波形"。现在波长按间隔线性插值，
         轮询快 → 波群密（wl 小），轮询慢 → 疏（wl 大），中间不饱和。
      滚动动画时长 = 波长 / 速度（不再是恒定 1 秒），与 ① 的 1 秒搏动脱钩。
   ④ 未运行 → **红色直线**；运行 → **绿色波形**（颜色由 .hb.bad 切换）。 */
var HB_Y = 12;            // 基线 y
var HB_VIEW = 54;         // 可视区宽(px)，与 .hb-frame 一致
/* ⚠️ 钳制范围 = 真实配置：poll_interval_min=8 / max=120。
   之前写 15~600，真实默认 12s 被钳成 15s，和设置里的数字自相矛盾。 */
var HB_MIN = 8, HB_MAX = 120;

var HB_BEAT_SEC = 1;      // 搏动周期：固定 1 秒（与轮询无关）
var HB_W0 = 54;           // 一个波群的标准宽度(px)，实际波长按它缩放
/* 滚动速度(px/s) = 轮询间隔数值的**反比**（间隔越大 → 滚得越慢）：
     speed = HB_FLOW_REF / dur
   ⚠️ 基准取 96：dur=8 → 12px/s。之前 24px/s 用户就反馈"8 秒档看着好快"，
      再快就变成"每秒换一屏"。想整体调快/调慢只改这一个数。
   ⚠️ 更早就错过一次方向（做成正比，间隔越大滚得越快），这里按"间隔越大越慢"。
   ⚠️ 只在这里设速度下限；波长不再跟着速度走 —— 见下面 HB_WAVE_*。 */
var HB_FLOW_REF = 96;           // 速度基准(px·s)
var HB_FLOW_MIN = 2;            // 极慢轮询时的速度下限(px/s)，免得看着完全不动
/* 波长(px) = 一个波群占的宽度 = 波形**密度**，与滚动速度**解耦**：
   ⚠️ 病根就在这里 —— 以前 wl = speed × 1 秒，于是"每秒正好滚过一个波群"，
      而搏动也是 1 秒一次，两者一乘就是"每搏动一次波形整体换一格"，
      用户看到的就是"动一下删一个波形"。现在波长和速度各算各的。
   轮询快(dur 小) → 波群密(wl 小)；轮询慢 → 波群疏(wl 大)，按间隔线性插值，
   **中间不饱和**（以前 clamp 到 6px，dur≥64 全挤在同一档，看着像跳档）。 */
var HB_WAVE_FAST = 18;          // dur=HB_MIN 时的波长（最密）
var HB_WAVE_SLOW = 54;          // dur=HB_MAX 时的波长（最疏，≈ 一个可视区宽）
/* 搏动：极值落在周期的 9% 处（收缩快、回弹慢）。 */
var HB_PEAK_PCT = 9;
/* 幅度：⚠️ 用户反馈"波动太不明显"，所以给足 ——
   点缩到 .65、框放到 1.18。起点终点都是静止态 + 两段缓动，
   所以幅度大也不会"啪"地跳一下（以前突兀是硬起硬落 + 叠光晕，不是幅度问题）。 */
var HB_LED_MIN = 0.65;
var HB_FRM_MAX = 1.18;

/* 无级：直接返回浮点秒。
   ⚠️ 以前"个位数奇数就 ×2"是分档（15→30、19→38），中间值会被硬掰到档上，
      用户看到的就是"跳档"而不是变速。现在 8.5 就是 8.5。 */
function hbCycleSec(sec) {
  sec = parseFloat(sec);
  if (!isFinite(sec) || sec <= 0) sec = HB_MIN;
  return Math.max(HB_MIN, Math.min(HB_MAX, sec));
}

/* 波长(px) = 一个波群占的宽度 = 波形密度。
   轮询快(dur 小) → 密(wl 小)；轮询慢 → 疏(wl 大)，按间隔**线性插值**。
   ⚠️ 与 hbSpeed 解耦：这里不再由速度推出，否则每秒必然滚过一个波群。 */
function hbWaveLen(dur) {
  var t = (dur - HB_MIN) / (HB_MAX - HB_MIN);
  t = t < 0 ? 0 : (t > 1 ? 1 : t);
  return HB_WAVE_FAST + (HB_WAVE_SLOW - HB_WAVE_FAST) * t;
}
/* 滚动速度(px/s)：只跟轮询间隔有关，与波长无关。 */
function hbSpeed(dur) {
  return Math.max(HB_FLOW_MIN, HB_FLOW_REF / (dur > 0 ? dur : HB_MIN));
}
/* 滚过一个波长要几秒 = 滚动动画的时长。
   ⚠️ 以前写死 1 秒（= 搏动周期），这就是"动一下删一个波形"的直接来源；
      现在它 = 波长/速度，随轮询无级变化，和 1 秒的搏动彻底脱钩。 */
function hbRollSec(dur) { return hbWaveLen(dur) / hbSpeed(dur); }

function hbBeatSeg(x0, k) {
  /* 一个完整 P-QRS-T，相对 x0 的偏移按 k 缩放（k = 波长 / HB_W0）。
     ⚠️ 小波的振幅也加大了：以前 P 只有 2.2、T 只有 3.2，
        在 20px 高的框里几乎看不见，"波动不明显"一半是这个原因。 */
  var Y = HB_Y;
  return ' L' + (x0 - 15 * k).toFixed(2) + ',' + Y +
         ' L' + (x0 - 12 * k).toFixed(2) + ',' + (Y - 3.0) +
         ' L' + (x0 - 9 * k).toFixed(2) + ',' + Y +
         ' L' + (x0 - 3 * k).toFixed(2) + ',' + (Y + 3.0) +
         ' L' + x0.toFixed(2) + ',' + 1.5 +               /* R：高尖 */
         ' L' + (x0 + 3 * k).toFixed(2) + ',' + (Y + 5.0) +
         ' L' + (x0 + 6 * k).toFixed(2) + ',' + Y +
         ' L' + (x0 + 10 * k).toFixed(2) + ',' + (Y - 4.2) +
         ' L' + (x0 + 14 * k).toFixed(2) + ',' + Y;
}

function buildHbPath(sec, alive) {
  var dur = hbCycleSec(sec);
  var wl = hbWaveLen(dur);
  /* 机器人没启动 → **一条直线**（红色由 .hb.bad 的 .hb-line 负责）。
     ⚠️ 以前只是把滚动停住，波形仍是一段 ECG 定格在框里，看着像"心跳卡住了"
        而不是"没心跳"；用户要的是平的。 */
  if (alive === false) {
    var flat = Math.max(HB_VIEW * 2, wl * 2);
    return { d: 'M0,' + HB_Y + ' L' + flat + ',' + HB_Y, w: flat, wl: wl, roll: 0 };
  }
  /* 总宽必须 ≥ 可视区 + 一个波长：滚动位移正好是一个波长，
     左移后右侧要有画好的波形顶上，否则右边会露出空白（= "删掉一个波形"的观感）。 */
  var n = Math.ceil(HB_VIEW / wl) + 2;
  var total = n * wl;
  var k = wl / HB_W0;
  /* ⚠️ 每个波群都放在自己那个周期的正中间（i*wl + wl/2），
     这样路径严格满足 p(x) === p(x+wl)：
     波群占 [x0-15k, x0+14k]，而 15k/wl = 0.278、14k/wl = 0.259，
     整个波群都落在 [i*wl, (i+1)*wl] 内，跨周期不会重叠。
     有了这个周期性，左移一个波长后跳回起点才是**无缝**的。
     ⚠️ 以前用 lead 把首个 R 峰挪到可视区中心，那会把周期性打破，
        且 lead 是按速度算的 —— 速度与波长解耦后它已经没有意义，去掉。 */
  var d = 'M0,' + HB_Y, i, x0;
  for (i = 0; i < n; i++) {
    x0 = i * wl + wl / 2;
    d += hbBeatSeg(x0, k);
  }
  d += ' L' + total.toFixed(2) + ',' + HB_Y;
  return { d: d, w: total, wl: wl, roll: hbRollSec(dur) };
}

/* 上一次的波长 / 滚动时长 / 在线态：没变就不重画，
   避免每 15s 的状态刷新把动画掐断重来（看着就是莫名抖一下） */
var _hbWl = 0, _hbRoll = 0, _hbAlive = true;

function setHb(sec, alive) {
  if (alive === undefined || alive === null) alive = true;
  var dur = hbCycleSec(sec);
  var wl = hbWaveLen(dur);
  var roll = hbRollSec(dur);
  var g = buildHbPath(sec, alive);
  if (Math.abs(wl - _hbWl) > 0.01 || Math.abs(roll - _hbRoll) > 0.01 || alive !== _hbAlive) {
    var svg = document.getElementById('hbSvg');
    var path = document.getElementById('hbPath');
    if (svg) { svg.setAttribute('viewBox', '0 0 ' + g.w.toFixed(2) + ' 20'); svg.style.width = g.w.toFixed(2) + 'px'; }
    if (path) path.setAttribute('d', g.d);
    /* 滚动位移 = 一个波长（px，不是 -50%：周期数按可视区算，百分比不固定），
       走完它的时间是 roll 秒 = 波长/速度 —— **不再是恒定 1 秒**。
       ⚠️ 以前写死 1 秒且位移正好一个波长，于是每秒换一个波群，
          和 1 秒的搏动叠在一起就是"动一下删一个波形"。 */
    document.documentElement.style.setProperty('--hb-shift', (-g.wl).toFixed(2) + 'px');
    document.documentElement.style.setProperty('--hb-roll-dur', roll.toFixed(3) + 's');
    _hbWl = wl; _hbRoll = roll; _hbAlive = alive;
  }
  /* 顶部"轮询 Xs"要带单位，只写数字会显示成"轮询 12"；
     无级之后 sec 可能是浮点（8.4），显示取整即可，别把小数露出来。 */
  var t = document.getElementById('tbIv');
  if (t) t.textContent = Math.round(parseFloat(sec) || HB_MIN) + 's';
}

/* ---------- 顶部常驻状态条 ---------- */
function renderTopBar() {
  var hb = STATE.heartbeat || {};
  var alive = !!hb.alive;
  var ready = !!STATE.creds_ready;
  // 机器人 + 心跳灯联动：不在线 → 轮询必然也停，两者一起变红
  var botTx = document.getElementById('botTx');
  if (botTx) botTx.textContent = !ready ? '待填凭据' : (alive ? '在线' : '未运行');
  var botWrap = botTx && botTx.parentElement;
  if (botWrap) botWrap.classList.toggle('bad', !alive);
  var hbEl = document.getElementById('tbHb');
  if (hbEl) hbEl.classList.toggle('bad', !alive);
  var hbLed = document.getElementById('hbLed');
  if (hbLed) hbLed.classList.toggle('bad', !alive);

  // 轮询间隔：优先用**运行时实际值**（撞风控会自动放慢），没有再退回配置值。
  // ⚠️ 以前只读 config.poll_interval —— 那是"设了多少"，不是"现在实际多少"，
  //    风控避让时顶栏一直显示 8s，看着像没生效。
  var hbp = (hb.poll || (hb.notes && hb.notes.poll)) || null;
  var poll = (hbp && hbp.current) || (STATE.config || {}).poll_interval || 12;
  // 机器人没在跑 → 波形画成一条红线（见 buildHbPath 的 alive 分支）
  if (document.getElementById('tbIv')) setHb(poll, alive);

  // B站登录
  var bTx = document.getElementById('tbBiliTx');
  var _be = biliEff();
  if (bTx) bTx.textContent = biliText();
  var bWrap = document.getElementById('tbBili');
  if (bWrap) bWrap.classList.toggle('bad', _be !== 'logged');
  // 概况页的续期入口：只在没登录/没生效时出现（正常时藏起来，不占位置）
  var qBtn = document.getElementById('quickBiliLogin');
  if (qBtn) qBtn.hidden = (_be === 'logged');
}

/* B站 登录的**有效**状态（面板全局用这一个口径）。
   ⚠️ 不能直接用 BILI_STATE.has_login —— 那只是"本地存了 cookie"，
      而机器人实测（心跳里的 bili_login）可能已经发现 B站 不认了。
      以实测为准，避免"面板说已登录、接口一直 -352"。 */
function biliEff() {
  var hb = STATE.heartbeat || {};
  var st = BILI_STATE.state || (BILI_STATE.has_login ? 'logged' : 'none');
  var bl = hb.bili_login || null;
  if (hb.alive && bl && bl.ok === false) st = 'invalid';
  if (st === 'logged' && BILI_STATE.expired) st = 'invalid';
  return st;                       // logged / invalid / unknown / none
}
function biliText() {
  switch (biliEff()) {
    case 'logged': return '已登录';
    case 'invalid': return '未生效';
    case 'unknown': return '未验证';
    default: return '未登录';
  }
}

/* ---------- 沙盒群：唯一入口在 设置/通用，这里只做同步显示 ---------- */
var GROUP_NAME = {};      // gid → 群名（从 STATE.groups 建）
function syncSandbox() {
  var gid = (STATE.config || {}).sandbox_group || '';
  // ⚠️ 群名查不到时**不要拿群号顶上**：用户要的是群名，一串十六进制
  //    看着像乱码。显示「未命名群」至少还能点进去改。
  var name = gid ? (GROUP_NAME[gid] || '未命名群') : '';
  ['tbSbName', 'sbBoxName', 'sbName'].forEach(function (id) {
    var el = document.getElementById(id);
    if (el) el.textContent = name || '未设置';
  });
  /* ⚠️ 群号（gid）**不再显示**：用户要的只有群名，一串十六进制
     看着像乱码。群名两侧的「」由 CSS 伪元素加（在 #sbBoxName 上），
     所以这里只写纯文本，JS 更新时不会把引号冲掉。 */
  var box = document.getElementById('sbBox');
  if (box) box.classList.toggle('bad', !gid);
  var warn = document.getElementById('noSandbox');
  if (warn) warn.style.display = gid ? 'none' : '';
  var tb = document.getElementById('tbSb');
  if (tb) tb.classList.toggle('bad', !gid);
}

/* ---------- 健康度：一项 25% ---------- */

/* 轮询那一项要说**实际**跑多快，不能永远写「按设定检查」。
   机器人每轮会把真实间隔写进心跳（hb.poll.current），撞风控会自己放慢，
   这时设定值和实际值不一样 —— 只显示设定值，看着就像调速没生效。 */
function pollActual(hb) {
  var p = (hb && hb.poll) || null;
  var cur = p && p.current ? parseInt(p.current, 10) : 0;
  return cur > 0 ? cur : 0;
}
function pollBase(hb) {
  var p = (hb && hb.poll) || null;
  var b = p && p.base ? parseInt(p.base, 10) : 0;
  if (b > 0) return b;
  return parseInt((STATE.config || {}).poll_interval || 0, 10) || 0;
}
function pollBackingOff(hb) {
  var p = (hb && hb.poll) || null;
  if (p && typeof p.backing_off !== 'undefined') return !!p.backing_off;
  var c = pollActual(hb), b = pollBase(hb);
  return !!(c && b && c > b);
}
function pollText(hb, alive) {
  if (!alive) return (hb && hb.detail) || '还没收到心跳';
  var c = pollActual(hb), b = pollBase(hb);
  if (c && b && c !== b) {
    return '实际 ' + c + 's/轮（设定 ' + b + 's，' +
      (c > b ? '撞风控已放慢' : '已加快') + '）';
  }
  if (c) return '实际 ' + c + 's/轮，与设定一致';
  return '正在按 ' + (b || '设定') + 's 检查';
}

function renderHealth() {
  var box = document.getElementById('healthBox');
  if (!box || box._missing) return;
  var hb = STATE.heartbeat || {};
  var ready = !!STATE.creds_ready;
  var alive = !!hb.alive;
  /* ⚠️ 以前只看本地有没有 cookie：cookie 其实已经失效、接口一直 -352，
     这一项照样显示"正常 / 已登录"。改成以 B站 实测为准。 */
  var _bes = biliEff();
  var biliOk = (_bes === 'logged');
  var biliDs = biliOk ? '已登录'
    : (_bes === 'invalid' ? 'B站 说不认这段 cookie，接口会返回 -352/412'
      : (_bes === 'unknown' ? '存了 cookie，但没能向 B站 求证' : '未登录，动态类接口可能返回 -352'));
  var biliSt = biliOk ? '正常'
    : (_bes === 'invalid' ? '未生效' : (_bes === 'unknown' ? '未验证' : '未登录'));
  /* ⚠️ go（跳转）只在**不正常**时才给：
     正常项就该老老实实显示"正常"，不该整行可点。
     以前 go 是无条件写死的，于是"QQ 连接""轮询"即使正常也可点，
     点了还跳到别的页 —— 看着像出了问题，实际没事。 */
  var items = [
    { nm: 'QQ 连接', ok: ready,
      ds: ready ? 'AppID / AppSecret 已填写' : '还没填 AppID',
      st: ready ? '正常' : '未填写',
      go: ready ? '' : '去填写 ›', sec: 'cred', tab: 'qq' },
    { nm: 'B站接口', ok: true,
      ds: '接口连通', st: '正常', go: '', sec: '', tab: '' },
    { nm: 'B站登录', ok: biliOk,
      ds: biliDs,
      st: biliSt,
      go: biliOk ? '' : '去登录 ›', sec: 'cred', tab: 'bili' },
    { nm: '轮询', ok: alive,
      ds: pollText(hb, alive),
      st: alive ? (pollBackingOff(hb) ? '已放慢' : '正常') : '未运行',
      go: alive ? '' : '看状态 ›', sec: 'diag', tab: 'run' },
  ];
  var okN = items.filter(function (x) { return x.ok; }).length;
  /* ⚠️ 类名必须用最终版那套 .hp / .sw / .tag：
     内联样式（最终版布局）里定义的正是 .hp-bar / .hp-fill / .hp-txt / .sw / .tag，
     而 .health-bar / .hl-i 只在**旧的 style.css** 里有 —— 用旧类名就等于
     走了旧样式表，看着和最终版不一样（进度条粗细、行高、标签全都不对）。 */
  var html = '<div class="hp"><div class="hp-bar"><div class="hp-fill" style="width:' +
    (okN * 25) + '%"></div></div>' +
    '<div class="hp-txt">' + okN + ' / 4 项正常（' + (okN * 25) + '%）</div></div>';
  items.forEach(function (x) {
    // 正常项：老老实实一行「名称 + 描述 + 正常标签」，不可点
    // 异常项：整行可点（jump），尾部给蓝色「去登录 ›」之类的提示
    html += '<div class="sw' + (x.go ? ' jump' : '') + '"' +
      (x.go ? ' data-act="goto-tab" data-sec="' + x.sec +
              '" data-tab="' + x.tab + '"' : '') + '>' +
      '<div><div class="nm">' + esc(x.nm) + '</div><div class="ds">' + esc(x.ds) +
      (x.go ? '　<span class="jump-hint">' + esc(x.go) + '</span>' : '') +
      '</div></div>' +
      '<span class="tag' + (x.ok ? ' on' : '') + '">' + esc(x.st) + '</span></div>';
  });
  box.innerHTML = html;
}

/* ---------- 待办：把散落各处的异常聚成一处 ---------- */
function renderTodo() {
  var box = document.getElementById('todoBox');
  if (!box || box._missing) return;
  var hb = STATE.heartbeat || {};
  var todo = [];
  if (!STATE.creds_ready) {
    todo.push({ ic: '🔑', tx: '还没填 AppID / AppSecret，机器人跑不起来',
                sec: 'cred', tab: 'qq' });
  }
  var _bt = biliEff();
  if (_bt !== 'logged') {
    todo.push({ ic: '🍪',
                tx: (_bt === 'invalid'
                     ? 'B站 cookie 没生效（接口返回 -352/412），请重新登录'
                     : (_bt === 'unknown'
                        ? 'B站 登录状态没能验证，动态类接口可能失败'
                        : 'B站未登录，动态类接口可能返回 -352')),
                sec: 'cred', tab: 'bili' });
  }
  if (!(STATE.config || {}).sandbox_group) {
    todo.push({ ic: '🧪', tx: '还没设置沙盒群，推送测试不知道发到哪',
                sec: 'setup', tab: 'gen' });
  }
  if (!hb.alive && STATE.creds_ready) {
    todo.push({ ic: '😴', tx: '机器人没在运行（' + (hb.detail || '没收到心跳') + '）',
                sec: 'diag', tab: 'run' });
  }
  if (ERR_RECENT > 0) {
    todo.push({ ic: '📋', tx: '最近 1 小时有 ' + ERR_RECENT + ' 条错误',
                sec: 'diag', tab: 'log' });
  }
  if (!todo.length) { box.innerHTML = ''; return; }
  var html = '<div class="todo-hd"><span>⚠️</span><span>有 ' +
    todo.length + ' 件事要处理</span></div>';
  todo.forEach(function (t) {
    html += '<div class="todo-i" data-act="goto-tab" data-sec="' + t.sec +
      '" data-tab="' + t.tab + '"><span class="ic">' + t.ic + '</span>' +
      '<span class="tx">' + esc(t.tx) + '</span><span class="go">去处理 ›</span></div>';
  });
  box.innerHTML = html;
}

/* ---------- 最近动态 ----------
   每行 = 头像 + 「UP 主名 + 动作」+「多久前推送到几个群」+ 类型标签。

   ⚠️ 数据来自心跳里的 recent（机器人**真的发出去**才写一条），
      不再拿 notes 拼：notes 是"检测结果"，每一轮都写、没推也更新，
      里面还是 dyn_id / BV 号这种原始值 —— 拼出来就是
      「没推送也显示、时间不对、内容是一串号」。 */
var RECENT_KIND = {
  live:           { act: '开播',     tag: '直播' },
  offline:        { act: '已下播',   tag: '直播' },
  video:          { act: '新投稿',   tag: '投稿' },
  dynamic:        { act: '新动态',   tag: '动态' },
  dynamic_pinned: { act: '置顶动态', tag: '动态' },
  top_comment:    { act: '置顶评论', tag: '评论' },
  season:         { act: '合集更新', tag: '合集' },
};

/* 头像：从已登记的订阅里按 uid 找一张。
   找不到就交给 initAvatars() 画一个带首字的占位头像。 */
function recentFaceOf(uid, isSea) {
  var gs = STATE.groups || [];
  var upFallback = { face: '', nm: '' };
  for (var i = 0; i < gs.length; i++) {
    /* 合集那条优先用**合集封面**（1:1），没有再退回 UP 主头像 */
    if (isSea) {
      var svs = (gs[i] || {}).seasons || [];
      for (var k = 0; k < svs.length; k++) {
        if (String(svs[k].uid) === String(uid) && seasonFace(svs[k])) {
          return { face: seasonFace(svs[k]), nm: svs[k].uname || '' };
        }
      }
    }
    var subs = (gs[i] || {}).subs || [];
    for (var j = 0; j < subs.length; j++) {
      if (String(subs[j].uid) === String(uid) && subs[j].face) {
        if (isSea && !upFallback.face) {
          upFallback = { face: subs[j].face, nm: subs[j].uname || '' };
        } else {
          return { face: subs[j].face, nm: subs[j].uname || '' };
        }
      }
    }
    var svs2 = (gs[i] || {}).seasons || [];
    for (var m = 0; m < svs2.length; m++) {
      if (String(svs2[m].uid) === String(uid) && svs2[m].face) {
        if (isSea && !upFallback.face) {
          upFallback = { face: svs2[m].face, nm: svs2[m].uname || '' };
        } else {
          return { face: svs2[m].face, nm: svs2[m].uname || '' };
        }
      }
    }
  }
  return upFallback;
}

function renderRecent() {
  var box = document.getElementById('recentBox');
  if (!box || box._missing) return;
  var evs = (((STATE.heartbeat || {}).recent) || []).filter(function (e) {
    return e && e.ts;
  }).slice().sort(function (a, b) {
    return (Number(b.ts) || 0) - (Number(a.ts) || 0);
  }).slice(0, 6);
  if (!evs.length) {
    // 刚升级还没推送过：说清楚"下一次推送后才有"，
    // 别让人以为数据丢了。
    box.innerHTML = '<div class="empty">还没有推送记录' +
      '<span class="hint" style="margin-top:4px">下一次成功推送后自动出现</span>' +
      '</div>';
    return;
  }
  var html = '';
  evs.forEach(function (e) {
    var k = RECENT_KIND[e.kind] || { act: '更新', tag: '更新' };
    var f = recentFaceOf(e.uid, isSea);
    var nm = String(e.name || '').trim() || f.nm || ('UID ' + e.uid);
    var isSea = String(e.kind) === 'season';
    html += '<div class="li rc-row">' +
      '<div class="av' + (isSea ? ' season-av' : '') + '" data-name="' +
        escAttr(nm) + '"' +
        (f.face ? ' data-face="' + escAttr(proxyImg(f.face)) + '"' : '') +
        '></div>' +
      '<div class="bd"><div class="t1">' + esc(nm) + ' ' + esc(k.act) + '</div>' +
      '<div class="t2">' + esc(fmtAgo(e.ts) || '刚刚') + '推送到 ' +
        (Number(e.groups) || 0) + ' 个群</div></div>' +
      '<span class="tag rc-tag">' + esc(k.tag) + '</span></div>';
  });
  box.innerHTML = html;
  initAvatars();
}

/* ---------- 侧边栏 B站账户卡 ---------- */
var BILI_STATE = { has_login: false, expired: false, account: {},
                   state: 'none', reason: '' };
function renderAccCard() {
  var box = document.getElementById('accCard');
  if (!box || box._missing) return;
  var A = BILI_STATE.account || {};
  if (!BILI_STATE.has_login || !A.ok) {
    box.className = 'acc-off';
    box.innerHTML = '<span>🍪</span><span>未登录 B站 · 去登录</span>';
    return;
  }
  box.className = 'acc';
  var lv = Math.max(0, Math.min(6, parseInt(A.level, 10) || 0));
  var hc = (A.vip_type === 2) ? '' : '';
  var face = A.face
    ? '<div class="acc-av" style="background-image:url(' + esc(A.face) + ')"></div>'
    : '<div class="acc-av">🍮</div>';
  box.innerHTML = face +
    '<div class="acc-bd"><div class="acc-nm">' + esc(A.uname || 'B站用户') + '</div>' +
    '<div class="acc-sub">' +
      '<span class="acc-lv lv' + lv + hc + '">LV' + lv + '</span>' +
      '<span>UID ' + esc(String(A.mid || '')) + '</span>' +
      (A.vip_label ? '<span class="acc-vip">' + esc(A.vip_label) + '</span>' : '') +
    '</div></div>';
}

/* ---------- 推送测试：7 种类型 ----------
   ⚠️ 后端 PUSH_KINDS 已扩展到 7 种；之前只有 4 种，
      于是下播/置顶动态/置顶评论/合集这几个按钮一律被拒，
      表现为"点了没反应"。 */
var PUSH_KINDS = [
  { key: 'live', nm: '开播' }, { key: 'offline', nm: '已下播' },
  { key: 'video', nm: '新投稿' }, { key: 'dynamic', nm: '新动态' },
  { key: 'dynamic_pinned', nm: '置顶动态' },
  { key: 'top_comment', nm: '置顶评论' }, { key: 'season', nm: '合集更新' },
];

async function pushTest(btn, kind) {
  var cfg = STATE.config || {};
  var gid = cfg.sandbox_group || '';
  if (!gid) {
    toast('还没设置沙盒群，先去「设置 → 通用」选一个', 'err');
    goto('setup'); showTab('setup', 'gen');
    return;
  }
  if (kind === 'all') {
    /* 七条都发，详情里汇总：每条一行，图片行各归各的。
       以前只 toast 一句"已发送 7 条"，哪条有图、比例对不对完全看不到。 */
    var rows = [];
    var last = null;
    for (var i = 0; i < PUSH_KINDS.length; i++) {
      var r = await post('/api/test', { gid: gid, kind: PUSH_KINDS[i].key,
        real: testOpt('real', true), mark: testOpt('mark', true) });
      if (r) { last = r; }
      var ls = ((r && r.img_lines) || []);
      rows.push('<div class="hint" style="margin-top:6px">'
        + esc(PUSH_KINDS[i].nm) + '：'
        + ((r && r.src === 'real') ? '真实' : '占位')
        + ' · ' + (r ? (r.imgs || 0) : 0) + ' 张图'
        + (ls.length ? '<br>' + ls.map(esc).join('<br>') : '')
        + '</div>');
    }
    var card = document.getElementById('tpLastCard');
    var body = document.getElementById('tpLastBody');
    if (card && body) {
      body.innerHTML = '<div class="hint">全部跑一遍 · 共 '
        + PUSH_KINDS.length + ' 条</div>' + rows.join('')
        + '<div class="btns" style="margin-top:8px">'
        + '<button class="btn" data-act="copy-tp-last">📋 复制全部</button></div>';
      card.style.display = '';
      card._lines = rows.map(function (s) {
        return s.replace(/<[^>]*>/g, ''); }).join('\n');
    }
    toast('已发送 ' + PUSH_KINDS.length + ' 条测试消息到沙盒群', 'ok');
    return;
  }
  var d = await post('/api/test', { gid: gid, kind: kind,
    real: testOpt('real', true), mark: testOpt('mark', true) });
  if (d && d.ok) {
    if (btn) { btn.classList.add('push-ok');
               setTimeout(function () { btn.classList.remove('push-ok'); }, 1800); }
    /* ⚠️ 详情写进「上次发送详情」卡片，不再只靠 toast ——
       toast 几秒就消失，用户根本来不及看（反馈："toast 是什么，后台看不到"）。 */
    renderTpLast(d, kind);
    var m = (d && d.msg) || ('已发送「' + kind + '」测试消息到沙盒群');
    var nlines = ((d && d.img_lines) || []).length;
    if (nlines) { m += ' ｜ 图片行已显示在下方详情里'; }
    toast(m, 'ok');
  } else {
    toast((d && d.msg) || '发送失败', 'err');
  }
}

/* ---------- 上次发送详情（固定显示，不会消失） ----------
   ⚠️ 「实际发出的图片行」是诊断"比例不对 / 手机看不到图"的唯一依据：
      ![#672px #1195px](url) 里那两个数字就是客户端预留的显示比例。
      以前只塞进 toast，几秒就消失，用户来不及截图就永远看不到了。
      现在固定显示，且可一键复制出来发给我排查。 */
function renderTpLast(d, kind) {
  var card = document.getElementById('tpLastCard');
  var body = document.getElementById('tpLastBody');
  if (!card || !body || !d) return;
  var nm = kind;
  for (var i = 0; i < PUSH_KINDS.length; i++) {
    if (PUSH_KINDS[i].key === kind) { nm = PUSH_KINDS[i].nm; break; }
  }
  var src = (d.src === 'real')
    ? '<b style="color:var(--ok,#3fb950)">真实内容</b>'
    : '<b style="color:var(--warn,#d29922)">占位内容（没图是正常的）</b>';
  var lines = (d && d.img_lines) || [];
  var h = '<div class="hint">' + esc(kind === 'all' ? '全部跑一遍' : nm)
        + ' · ' + src + ' · 卡片里有 <b>' + (d.imgs || 0) + '</b> 张图</div>';
  if (lines.length) {
    h += '<div class="hint" style="margin-top:8px">'
       + '实际发出的图片行 —— <b>那两个数字就是显示比例</b>'
       + '（竖图应该高&gt;宽，如 #672px #1195px）：</div>'
       + '<pre class="tp-lines">' + esc(lines.join('\n')) + '</pre>'
       + '<div class="btns" style="margin-top:8px">'
       + '<button class="btn" data-act="copy-tp-last">📋 复制图片行</button></div>';
  } else {
    h += '<div class="hint" style="margin-top:8px">这条没有图片行'
       + '（占位内容本身没图；勾上「用真实数据」才有真实封面/配图）</div>';
  }
  body.innerHTML = h;
  card.style.display = '';
  card._lines = lines.join('\n');
}

/* ---------- 立即检测：结果就近显示 + 单独成块 ----------
   ⚠️「立即检测」在三个地方都有（概况页快捷操作 / 自检页 / 运行状态页），
      以前三个按钮共用同一个 #checkNowBox，而它在「自检」面板里。
      在运行状态页点了，结果写进另一个（隐藏的）面板 —— 看着就是没反应。 */
function pickCheckNowBox(btn) {
  var own = null;
  try {
    if (btn && btn.closest) {
      var scope = btn.closest('.sub-panel') || btn.closest('.card');
      if (scope) own = scope.querySelector('.check-now-out');
    }
  } catch (e) { own = null; }
  if (own) return own;
  // 兜底：老的 #checkNowBox（没加 class 的历史模板）
  return document.getElementById('checkNowBox');
}

/* 0/1/2 → 未开播/已开播/轮播中。
   后端已带中文 text，但**历史基线里存的仍是数字**，显示前也要翻。 */
function liveStatusCn(v) {
  var m = {'0': '未开播', '1': '已开播', '2': '轮播中'};
  var s = String(v === undefined || v === null ? '' : v).trim();
  if (m[s]) return m[s];
  /* ⚠️ 历史基线里存过 "live_status=2" 这种整串，先把数字抠出来再查表，
     否则正则一条都匹配不上，界面上就一直显示那串等号。 */
  var mm = s.match(/live[_\s-]*status\s*[:=]\s*(-?\d+)/i);
  if (mm && m[mm[1]]) return m[mm[1]];
  if (/未开播|下播/.test(s)) return '未开播';
  if (/轮播/.test(s)) return '轮播中';
  if (/直播中|已开播/.test(s)) return '已开播';
  return s || '（空）';
}

var CHECK_NOW_CARD_MAX = 3;     // 最多留几块，多了页面太长

/* 结果块是**折叠卡片**：折着只看一行结论（时间 + 耗时 + 结论），
   点开才是逐项明细。5 个 UP 主各有三行，全铺开会把下面的内容顶没。
   ⚠️ v2.0.1：默认**折叠**（以前是默认展开）。一次检测 5 个 UP 主 × 3 行，
      默认展开等于把整屏占满、还得手动收回去；而折着那行已经写了结论
      （"状态没变，不推送"），不展开也知道结果 —— 要看明细再点开。 */
var CHECK_NOW_CARD_OPEN = false;
function pushCheckNowCard(box, d, body) {
  /* ⚠️ 先把「正在检测所有订阅…」那行清掉。
     以前只在**失败**时清（成功时不管），于是跑完还留着一行带转圈的
     进度文字 —— 看着就是"一直卡在检测中"。 */
  var pr = box.querySelectorAll('.progress-text');
  for (var pi = 0; pi < pr.length; pi++) pr[pi].remove();

  var el = document.createElement('details');
  el.className = 'diag-card fold flash';
  el.open = CHECK_NOW_CARD_OPEN;
  var el2 = (d.elapsed === undefined || d.elapsed === null)
    ? '' : ('，用了 ' + d.elapsed + 's');
  el.innerHTML =
    '<summary class="fold-hd dcard-head">'
    + '<span class="fold-ic">▸</span>'
    + '<span class="dt">⚡ 立即检测 ' + esc(String(d.at || '')) + el2 + '</span>'
    + '<span class="dsrc">' + esc(String(d.summary || '')) + '</span>'
    + '</summary>'
    + '<div class="fold-bd">'
    + '<div class="dcard-note">' + esc(String(d.note || '')) + '</div>'
    + '<div class="dcard-body-full">' + body + '</div>'
    + '</div>';
  /* 新块进来时把上一块收起：默认就是折叠的，这里兜的是用户手动展开过
     的旧块 —— 一次只让一份是展开的，不然连点几次会层层摊开。 */
  var olds = box.querySelectorAll('details.diag-card');
  for (var oi = 0; oi < olds.length; oi++) {
    olds[oi].open = CHECK_NOW_CARD_OPEN && oi === 0;
  }
  box.insertBefore(el, box.firstChild);   // 最新的在最上面
  while (box.children.length > CHECK_NOW_CARD_MAX) {
    box.removeChild(box.lastChild);
  }
  setTimeout(function () { el.classList.remove('flash'); }, 1600);
}

/* ---------- 全面自检：固定七行，点之前一律「未检测」 ----------
   ⚠️ 以前是 detail.push 只推**有值**的项：某项没取到就整行消失，
      于是行数是浮动的，也看不出到底哪项没测。现在行数固定为七项，
      没跑过就是「未检测」，跑完才有参数（参数在鼠标悬停的浮层里）。 */
var DIAG_ROWS = [
  ['room_id', '直播间'],
  ['live', '直播状态'],
  ['video', '最新投稿'],
  ['dynamic', '最新动态'],
  ['pinned', '置顶动态'],
  ['top_comment', '置顶评论'],
  ['season', '合集检测']
];
/* 一行自检结果的 HTML。
   ⚠️ 上面那七行和「立即检测」新开的卡片**必须长得一模一样**（同样的
      .diag-row / 未检测·通过·异常 / 详细内容在 data-tip 里）——
      以前卡片另写了 .dcard-row 两列排版，而 .dcard-row 根本没有样式，
      于是卡片里七项挤成一坨，跟上面完全不同。现在两处共用这一个函数。 */
function diagRowHtml(b, r) {
  var k = r[0], label = r[1];
  var st = 'idle', badge = '未检测', tip = '点「重新自检」后这里会显示详细内容';
  if (b) {
    var raw = (k === 'room_id')
      ? (roomText(b.room_id, b.short_room_id) || b.room_id) : b[k];
    raw = (raw === undefined || raw === null) ? '' : String(raw).trim();
    /* ⚠️ 直播状态不能出现 0/1/2 这种原始数字 —— 后端已翻好，
       但历史记录/异常兜底里仍可能存着数字，显示前在这里再兜一次。 */
    if (raw && k === 'live') raw = liveStatusCn(raw);
    /* ⚠️ "没有置顶动态""该置顶动态下没有置顶评论""查不到直播间号"
       这类是**接口扫到了、只是没有内容**，属于正常，一律算通过 ——
       以前按"没值"算异常，于是没置顶评论就整行标红，误报成故障。 */
    if (!raw) { st = 'idle'; badge = '无'; tip = '接口通，但没有检测到内容'; }
    else if (raw.indexOf('失败') === 0) { st = 'bad'; badge = '异常'; tip = raw; }
    else { st = 'ok'; badge = '通过'; tip = raw; }
  }
  /* 详细参数不再直接铺在右边（七行长短不一，挤成一坨也对不齐），
     改成鼠标移到这一行上才显示（.dv 的 data-tip 浮层）。 */
  return '<div class="diag-row"><span class="dk">' + esc(label)
    + '</span><span class="dv ' + st + '" data-tip="' + escAttr(tip)
    + '">' + esc(badge) + '</span></div>';
}
function renderDiag(b) {
  var box = document.getElementById('diagBox');
  if (!box || box._missing) return;
  box.innerHTML = DIAG_ROWS.map(function (r) { return diagRowHtml(b, r); }).join('');
}

/* ---------- 测试 UID：可自己填、会记住 ---------- */
var CHK_UID_DEFAULT = '259149243';
function loadChkUid(force) {
  var box = document.getElementById('chkUid');
  // ⚠️ 以前只读 STATE.config.selfcheck_uid，而 /api/state 根本不返回这个键
  //    → 永远 undefined → 永远回退 CHK_UID_DEFAULT → 输入框"一直回弹"。
  //    现在后端 state 会回真实值，这里只把 CHK_UID_DEFAULT 当最后兜底。
  var v = String((STATE.config || {}).selfcheck_uid || CHK_UID_DEFAULT);
  // 名字从 state 带过来（后端本地查的，不联网）；查不到就显示「用户」
  var nm = String((STATE.config || {}).selfcheck_uname || '');
  // ⚠️ 用户正在输入时不要覆盖 —— 自动刷新 loadState() 会再刷一次，
  //    无条件赋值就会把刚敲的内容冲掉。保存后 force=true 才强制回写。
  if (box && (force || document.activeElement !== box)) box.value = v;
  showChkUid(v, nm);
}
function showChkUid(v, name) {
  // ⚠️ HTML 里已经写了「已记住：」这个前缀，这里**不能再拼一遍** ——
  //    以前就是两边都拼，显示成了「已记住：已记住：259149243」。
  //    现在这里只负责「用户 | uid：xxx」这一段。
  var el = document.getElementById('chkUidNow');
  if (!el) return;
  var nm = (name && String(name).trim()) || '用户';
  el.textContent = nm + ' | uid：' + v;
}
function parseUid(s) {
  var m = String(s || '').match(/(\d{3,})/);
  return m ? m[1] : '';
}
async function saveChkUid() {
  var box = document.getElementById('chkUid');
  var v = parseUid(box ? box.value : '');
  if (!v) {
    toast('没填 UID，保持原来的（' + CHK_UID_DEFAULT + '）', 'err');
    return;
  }
  var d = await post('/api/config', { selfcheck_uid: v });
  if (d && d.ok) {
    // ⚠️ 保存后必须强制回写输入框：此时焦点还在框上，
    //    loadState() 里的 activeElement 保护会跳过赋值，
    //    于是框里还是旧值、下面提示已经是新值 —— 用户以为没存上。
    await loadState();
    loadChkUid(true);
    toast('已记住测试 UID：' + v, 'ok');
  } else toast((d && d.msg) || '保存失败', 'err');
}
async function resetChkUid() {
  await post('/api/config', { selfcheck_uid: CHK_UID_DEFAULT });
  await loadState();
  loadChkUid(true);   // 用后端返回的真实值，不猜
  toast('已恢复默认测试 UID', 'ok');
}

/* ---------- 最近错误数（待办用） ---------- */
var ERR_RECENT = 0;

/* ---------- 把 heroBox / statsBox 同步到运行状态页 ---------- */
function syncMirror() {
  ['heroBox', 'statsBox'].forEach(function (id) {
    var src = document.getElementById(id);
    var dst = document.getElementById(id + '2');
    if (src && dst) dst.innerHTML = src.innerHTML;
  });
}

/* ---------- 新增 action 注册 ----------
   ⚠️ 用 Object.assign 追加，不去改原 ACTIONS 对象里的内容，
      避免动到已有 action 的实现。 */
Object.assign(ACTIONS, {
  /* 跳到「某板块的某选项卡」：概况页的健康度、待办、KPI 都用它 */
  'goto-tab': (el) => {
    const sec = el && el.getAttribute('data-sec');
    const tab = el && el.getAttribute('data-tab');
    if (!sec) return;
    // ⚠️ 已经在目标板块时**只切选项卡**，不能再走 goto()：
    // goto → openSec 会先把选项卡重置成默认项，再切回来，
    // 表现就是点选项卡闪一下又回到第一个（或干脆切不动）。
    const cur = document.querySelector('.sec.show');
    if (cur && cur.getAttribute('data-sec') === sec) {
      if (tab) showTab(sec, tab);
      return;
    }
    goto(sec);
    if (tab) setTimeout(() => showTab(sec, tab), 30);
  },
  /* 跳到某个输入框并聚焦（v1.62.0）
     安全体检里「可编辑」的项用它：光切选项卡还不够，
     跳过去以后还得让人一眼看到该填哪个框，所以滚到视野中间 + 聚焦 + 闪一下。 */
  'focus-field': (el) => {
    const id = el && el.getAttribute('data-el');
    if (!id) return;
    try { ACTIONS['goto-tab'](el); } catch (_) { /* 切不动就只聚焦 */ }
    const focusIt = () => {
      const t = document.getElementById(id);
      if (!t) return;
      try { t.scrollIntoView({ block: 'center', behavior: 'smooth' }); }
      catch (_) { /* 老浏览器不支持 options */ }
      try { t.focus({ preventScroll: true }); } catch (_) { t.focus(); }
      // 闪两下边框：纯视觉提示，不改动任何值
      const old = t.style.boxShadow;
      let n = 0;
      const iv = setInterval(() => {
        n += 1;
        t.style.boxShadow = (n % 2) ? '0 0 0 3px rgba(0,174,236,.45)' : (old || '');
        if (n >= 5) { clearInterval(iv); t.style.boxShadow = old || ''; }
      }, 160);
    };
    setTimeout(focusIt, 80);
  },
  /* 手动填写 Cookie：默认收起，点一下才展开（v1.62.0） */
  'toggle-cookie': (el) => {
    const box = $('cookieManual');
    if (!box || box._missing) return;
    const open = box.hasAttribute('hidden');
    if (open) box.removeAttribute('hidden'); else box.setAttribute('hidden', '');
    const btn = $('cookieToggle');
    if (btn && !btn._missing) {
      btn.textContent = open ? '✍️ 收起手动填写' : '✍️ 手动填写';
      btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    }
    if (open) {
      const t = $('cookieText');
      if (t && !t._missing) setTimeout(() => { try { t.focus(); } catch (_) { } }, 60);
    }
  },
  /* 推送测试：7 种类型 + 全部跑一遍 */
  'push-test': async (el) => {
    const kind = (el && el.getAttribute('data-kind')) || 'live';
    await pushTest(el, kind);
  },
  /* 复制「上次发送详情」里的图片行 —— 用户要把它发给我排查比例问题，
     手抄 URL 必错，所以给一键复制。navigator.clipboard 在非 https
     下不可用，所以留了 textarea + execCommand 的兜底。 */
  'copy-tp-last': () => {
    var card = document.getElementById('tpLastCard');
    var txt = (card && card._lines) || '';
    if (!txt) { toast('还没有可复制的内容', 'err'); return; }
    function fallback() {
      try {
        var ta = document.createElement('textarea');
        ta.value = txt;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
        toast('已复制', 'ok');
      } catch (e) { toast('复制失败，请手动选中后 Ctrl+C', 'err'); }
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(txt).then(function () {
        toast('已复制', 'ok');
      }, fallback);
    } else { fallback(); }
  },
  /* 「设置 → 关于」一键复制版本信息：反馈 bug 时直接贴，
     省得手抄版本号、数据目录、依赖情况抄错。 */
  'about-copy': () => {
    const d = _aboutCache;
    const s = (d && d.stats) || {};
    const deps = (d && d.deps) || {};
    const txt = [
      'OnOBN 版本：v' + ((d && d.version) || '?'),
      '项目名：OnOB站通知订阅工具（OnO Bilibili Notifier / OnOBN）',
      '作者：' + ((d && d.author) || '波萝Buono')
        + '（GitHub: ' + ((d && d.github) || 'github.com/XxBoLuoxX') + '）',
      '运行环境：Python ' + ((d && d.python) || '?') + ' / ' + ((d && d.platform) || '?'),
      '数据目录：' + ((d && d.data_dir) || '?'),
      '数据文件：' + ((d && d.state_path) || '?'),
      '订阅规模：群 ' + (s.groups || 0) + ' · 订阅 ' + (s.subs || 0)
        + ' · UP 主 ' + (s.ups || 0) + ' · 合集 ' + (s.seasons || 0),
      '依赖：' + Object.keys(deps).map(k => k + (deps[k] ? '✅' : '❌')).join(' '),
    ].join('\n');
    function fallback() {
      try {
        const ta = document.createElement('textarea');
        ta.value = txt;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
        toast('已复制版本信息', 'ok');
      } catch (e) { toast('复制失败，请手动选中后 Ctrl+C', 'err'); }
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(txt).then(() => toast('已复制版本信息', 'ok'), fallback);
    } else { fallback(); }
  },
  'save-chk-uid': async () => { await saveChkUid(); },
  'reset-chk-uid': () => { resetChkUid(); },
  'refresh-state': async () => {
    await loadState(); await loadLog(); await loadBili();
    await loadRun();
    toast('已刷新', 'ok');
  },
  /* 清理残留进程：多开一个实例就会同时监听同一个群 → 一条动态推好几次。
     以前运行状态页只提示「N 个残留，建议清理」却**没有清理按钮**，
     用户只能关掉重开，于是体感就是"杀了没杀干净"。 */
  'clean-procs': async () => {
    if (!confirm('清理残留进程？会关掉除当前面板和正在运行的机器人以外的所有本项目进程。')) return;
    const d = await post('/api/clean-procs', {});
    if (!d.ok) { toast(d.msg || '清理失败', 'err'); return; }
    toast(d.msg || ('已清理 ' + (d.n || 0) + ' 个'), 'ok');
    await loadRun(); await loadState();
  },
  /* 测试选项：存 localStorage，刷新后还是上次的选择 */
  'toggle-testopt': (el) => {
    const k = el.getAttribute('data-opt');
    const on = !el.classList.contains('on');
    el.classList.toggle('on', on);
    try {
      const o = JSON.parse(localStorage.getItem('biliTestOpt') || '{}');
      o[k] = on; localStorage.setItem('biliTestOpt', JSON.stringify(o));
    } catch (e) { /* 存不了也照样生效到关页面为止 */ }
  },
  /* ⚠️ 「模拟未设置沙盒群」已移除（早期试功能时加的假状态开关）：
     按钮和处理一起删，不留死代码。 */
  /* 跳到沙盒群：以前只是 goto-tab 到「设置→通用」，页面停在板块顶部，
     沙盒群卡片在下面，用户还得自己找 —— 看着像"跳了没反应"。
     现在跳过去后再把 #sbCard 滚进视野并闪一下。 */
  'goto-sandbox': () => {
    ACTIONS['goto-tab']({ getAttribute: (k) => (k === 'data-sec' ? 'setup' : 'gen') });
    setTimeout(() => {
      const card = document.getElementById('sbCard');
      if (!card) return;
      // ⚠️ scrollIntoView 在部分环境（含 jsdom）没有实现，
      //    不判断直接调会抛错、把后面的高亮也一起打断。
      if (typeof card.scrollIntoView === 'function') {
        try { card.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
        catch (e) { try { card.scrollIntoView(); } catch (e2) { /* 滚不动就算了 */ } }
      }
      card.classList.remove('flash');
      void card.offsetWidth;   // 强制重排，动画才会重新播
      card.classList.add('flash');
      setTimeout(() => card.classList.remove('flash'), 1800);
    }, 80);
  },

  /* 通用「跳到某张卡片并高亮」：日志概览行、各处提示都用它。
     跟 goto-sandbox 同款效果（滚进视野 + 闪一下），只是目标可配置。 */
  'goto-card': (el) => {
    const sec = el.getAttribute('data-sec') || 'setup';
    const tab = el.getAttribute('data-tab') || '';
    const id = el.getAttribute('data-id') || '';
    ACTIONS['goto-tab']({
      getAttribute: (k) => (k === 'data-sec' ? sec : (k === 'data-tab' ? tab : ''))
    });
    setTimeout(() => {
      const card = id ? document.getElementById(id) : null;
      if (!card) return;
      if (typeof card.scrollIntoView === 'function') {
        try { card.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
        catch (e) { try { card.scrollIntoView(); } catch (e2) { /* 滚不动就算了 */ } }
      }
      card.classList.remove('flash');
      void card.offsetWidth;
      card.classList.add('flash');
      setTimeout(() => card.classList.remove('flash'), 1800);
    }, 80);
  },

  /* 「只看问题」：按下亮起，再按熄灭（不是开关，是动作按钮） */
  'toggle-log-err': () => {
    LOG_ERR_ONLY = !LOG_ERR_ONLY;
    toast(LOG_ERR_ONLY ? '只看问题：开' : '只看问题：关', 'ok');
    renderLog();
  },

  /* 文案预设：整套一次套用，点完还能单独改 */
  'tpl-preset': (el) => {
    const key = el && el.getAttribute('data-preset');
    const set = TPL_PRESETS[key];
    if (!set) return;
    TPL_FIELDS.forEach(([id, k]) => {
      const t = $(id);
      if (!t || t._missing) return;
      if (!(k in set)) return;
      t.value = set[k];
      FORM_DIRTY[id] = true;
    });
    const box = document.getElementById('tplPresets');
    if (box) box.querySelectorAll('.cl-tab').forEach(b => b.classList.toggle('on', b === el));
    safe('tplPrev', renderTplPreview);
    toast('已套用「' + ((el && el.textContent) || key) + '」预设，记得点保存', 'ok');
  },

  /* 输出调试日志：往 bot.log 打一份诊断快照，方便确认开关真的生效了 */
  async 'dump-debug'() {
    const on = $('cDebugLog') && $('cDebugLog').checked;
    if (!on) { showMsg('advMsg', '先打开上面的「输出调试日志」开关', 'err'); return; }
    const d = await post('/api/debug/dump', {});
    showMsg('advMsg', d.ok ? ('已写入 bot.log ✅ ' + (d.msg || '')) : (d.msg || '失败'),
            d.ok ? 'ok' : 'err');
    if (d.ok) { toast('调试日志已输出', 'ok'); safe('log', loadLog); }
  },

  async 'clear-pwd'() {
    if (!confirm('清除面板口令？之后打开面板不用输密码。')) return;
    const d = await post('/api/security/password', { action: 'clear' });
    showMsg('pwdMsg', d.ok ? '口令已清除' : (d.msg || '失败'), d.ok ? 'ok' : 'err');
    if (d.ok) { const i = $('newPwd'); if (i) i.value = ''; loadSecurity(); }
  },
  async 'media-clear'() {
    if (!confirm('清空图片缓存？下次推送会重新下载，不影响订阅数据。')) return;
    const d = await post('/api/media/clear', {});
    showMsg('mediaMsg', d.ok ? (d.msg || '已清空') : (d.msg || '失败'),
            d.ok ? 'ok' : 'err');
    safe('media', loadMedia);
  },
  'refresh-media': () => { safe('media', loadMedia); toast('已刷新', 'ok'); },
  /* 图床探测：模拟开放平台拉图的取法（不带 Referer）探一次。
     手机端看不到图多数是图床防盗链 —— 电脑端带 Referer 能看，
     平台拉图不带 Referer 就被 403。本地复现不了，只能探出来摆给用户。 */
  /* ⚠️ 置灰/换文字交给统一机制（beginBusy/endBusy）：
     这里以前自己改 textContent，跟统一机制的恢复叠在一起会互相覆盖，
     按钮最后会卡在"探测中…"。 */
  async 'media-probe'() {
    const box = document.getElementById('mdProbe');
    if (box) box.innerHTML = '<span style="opacity:.7">正在探测…</span>';
    try {
      const d = await api('/api/media/probe');
      const items = (d && Array.isArray(d.items)) ? d.items : [];
      if (!box) return;
      if (!items.length) {
        box.innerHTML = '<span style="opacity:.7">还没有可探测的图床 —— '
          + '推送一条带图的消息后再来</span>';
        return;
      }
      box.innerHTML = '<div style="margin-bottom:4px">不带 Referer 探测结果'
        + '（模拟开放平台拉图）：</div>'
        + items.map(function (it) {
          const ok = it.ok === true;
          const col = ok ? '#3ddc97' : (it.ok === false ? '#ff6b6b' : '#8b8b8b');
          const tag = ok ? '✅' : (it.ok === false ? '❌' : '⚪');
          return '<div style="margin-top:3px">' + tag + ' <b>' + esc(it.host)
            + '</b> <span style="color:' + col + '">' + esc(it.note || '')
            + '</span></div>';
        }).join('')
        + '<div style="margin-top:5px;opacity:.75">❌ 表示该图床开了防盗链：'
        + '要么去开放平台报备域名，要么把「发送方式」改成「本地下载直传」'
        + '（本地取图再上传，不依赖平台拉图）</div>';
    } finally {
      /* 按钮恢复由统一机制负责（见上方注释） */
    }
  },
  /* 测试上传：真造一张图走「上传 → 发送」，验证富媒体链路通不通。
     以前只有「清空缓存」，没有正向验证手段 —— 缓存是空的，
     根本看不出到底是没推过图，还是上传链路坏了。 */
  /* 同上：置灰交给统一机制，这里只管结果 */
  async 'media-test-upload'() {
    try {
      const d = await post('/api/media/test-upload', {});
      showMsg('mediaMsg', d.ok ? (d.msg || '已发送') : (d.msg || '失败'),
              d.ok ? 'ok' : 'err');
      if (d.ok && d.ttl) {
        const e = document.getElementById('mediaMsg');
        if (e) e.textContent += '（凭证 ' + d.ttl + ' 秒有效）';
      }
    } finally {
      safe('media', loadMedia);
    }
  },
  async 'restart-bot'() {
    if (!confirm('重启机器人？会断开重连，大约需要几秒。')) return;
    const d = await post('/api/system/restart', {});
    toast(d && d.ok ? (d.msg || '已请求重启') : ((d && d.msg) || '重启失败'),
          d && d.ok ? 'ok' : 'err');
  },
  'unban-ip': async () => {
    const d = await post('/api/harden/unban', {});
    if (d && d.ok) toast('已解封本机 IP', 'ok');
    else toast((d && d.msg) || '解封失败', 'err');
    await loadState();
  },
});


/* ═══ 头像 ═══
   · UP 主 → B站头像（后端从 ups 表取 face，写进 data-face）
   · 合集 → 官方不给合集封面，用统一的**箱子图标**，
     一眼区分"这是合集不是 UP 主"，形状也是圆角方形不是圆形。
   两者都写成 --av，由布局样式负责画出来。 */
function seasonBoxSVG() {
  var svg =
    '<svg xmlns="http://www.w3.org/2000/svg" width="80" height="80">' +
    '<defs><linearGradient id="b" x1="0" y1="0" x2="1" y2="1">' +
    '<stop offset="0" stop-color="#F0A868"/><stop offset="1" stop-color="#C97A3C"/>' +
    '</linearGradient></defs>' +
    '<rect width="80" height="80" fill="#2A3245"/>' +
    '<rect x="16" y="30" width="48" height="34" rx="3" fill="url(#b)"/>' +
    '<rect x="12" y="24" width="56" height="9" rx="2.5" fill="#F5C08A"/>' +
    '<rect x="37" y="24" width="6" height="40" fill="#8A5326" opacity=".85"/>' +
    '<rect x="16" y="44" width="48" height="5" fill="#8A5326" opacity=".55"/>' +
    '</svg>';
  return 'data:image/svg+xml,' + encodeURIComponent(svg);
}
function upAvatarSVG(name) {
  var t = (name || '?').trim(), ch = t[0] || '?';
  var h = 0;
  for (var i = 0; i < t.length; i++) h = (h * 31 + t.charCodeAt(i)) % 360;
  /* 纯色，不用渐变：小圆上渐变会有明显色阶和生硬边缘。
     色相由昵称哈希决定 —— 同一个人永远同一个颜色。 */
  var svg =
    '<svg xmlns="http://www.w3.org/2000/svg" width="80" height="80">' +
    '<rect width="80" height="80" fill="hsl(' + h + ',42%,52%)"/>' +
    '<text x="40" y="53" font-size="34" font-family="sans-serif" ' +
    'font-weight="600" fill="rgba(255,255,255,.95)" text-anchor="middle">' +
    ch.replace(/&/g, '&amp;').replace(/</g, '&lt;') + '</text></svg>';
  return 'data:image/svg+xml,' + encodeURIComponent(svg);
}
/* ⚠️ B站图床带防盗链：浏览器以 127.0.0.1 作 Referer 直接请求，
   拿到的往往不是头像而是占位图/403 —— 表现就是「头像获取异常」：
   face 地址明明有，界面上却是空的。走 /api/img 由服务端带 Referer 去取。 */
function proxyImg(u) {
  var s = String(u || '').trim();
  if (!s) return '';
  if (s.charAt(0) === '/' || s.indexOf('data:') === 0) return s;
  if (!/^https?:\/\//i.test(s)) return s;
  return '/api/img?u=' + encodeURIComponent(s);
}

/* 合集封面裁成 1:1 小方图。
   ⚠️ 合集封面原图是 16:9，直接当头像用：① 原图动辄几百 KB，为 34px
      的头像下整张太浪费；② 只靠 CSS background-size:cover 虽然也是裁剪，
      但浏览器拿到的是原图，比例差得越多裁掉的部分越不可控。
      所以让**图床**先裁：B 站图片支持 @宽w_高h_1c（1c = 裁剪填充，
      不是拉伸变形），拿到就是正方形小图。
   ⚠️ 只认 hdslb 图床：别的域名套 B站 的 @参数会取不到内容。 */
function crop1x1(u) {
  var s = String(u || '').trim();
  if (!s) return '';
  if (s.indexOf('data:') === 0) return s;
  if (!/^https?:\/\//i.test(s)) return s;
  try {
    var h = (new URL(s)).hostname.toLowerCase();
    if (h !== 'hdslb.com' && !/\.hdslb\.com$/.test(h)) return s;
  } catch (e) { return s; }
  if (/@[^/]*$/.test(s)) return s;             // 已经带过参数了
  return s + '@96w_96h_1c.jpg';
}

/* 合集头像：优先用合集封面（裁 1:1），没有才退回 UP 主头像。 */
function seasonFace(sv) {
  return crop1x1(sv && (sv.cover || sv.face)) || '';
}

function initAvatars() {
  var els = document.querySelectorAll('.li .av, .grp .av');
  for (var i = 0; i < els.length; i++) {
    var el = els[i];
    var face = proxyImg(el.getAttribute('data-face'));
    var url = face || (el.classList.contains('season-av')
      ? seasonBoxSVG() : upAvatarSVG(el.getAttribute('data-name') || ''));
    el.style.setProperty('--av', 'url("' + url + '")');
    el.classList.add('has-img');
  }
}

/* ---------- 详情浮层：抬到页面最顶层 ----------
   ⚠️ 为什么不用 CSS ::after 了：伪元素属于元素自己的层叠上下文，
      而 .grp 群卡片带 overflow:hidden（折叠动画必须），浮层往上弹出
      的那部分会被卡片**直接裁掉**；它也压不住后面的卡片 ——
      表现就是反馈里的「UI 被遮挡，不是顶层」。
   改成 body 下的 #tipLayer（position:fixed + z-index:9999），
   由 JS 算位置：优先显示在元素上方，上方放不下就翻到下方，
   左右夹在视口内，绝不出屏。 */
function tipHide() {
  const l = $('tipLayer');
  if (!l) return;
  l.style.display = 'none';
  l.textContent = '';
  l.setAttribute('aria-hidden', 'true');
}

function tipShow(el) {
  const l = $('tipLayer');
  if (!l) return;
  const txt = String(el.getAttribute('data-tip') || '');
  if (!txt) { tipHide(); return; }
  l.textContent = txt;
  l.style.visibility = 'hidden';       // 先量尺寸，避免闪一下再跳位
  l.style.display = 'block';
  l.style.left = '0px';
  l.style.top = '0px';
  const r = el.getBoundingClientRect();
  const w = l.offsetWidth, h = l.offsetHeight;
  const maxL = Math.max(8, window.innerWidth - w - 8);
  let left = r.right - w;              // 跟元素右边缘对齐（跟老版一致）
  if (left < 8) left = 8;
  if (left > maxL) left = maxL;
  let top = r.top - h - 7;             // 优先上方
  if (top < 8) top = r.bottom + 7;     // 上方不够就放下方
  l.style.left = Math.round(left) + 'px';
  l.style.top = Math.round(top) + 'px';
  l.style.visibility = 'visible';
  l.setAttribute('aria-hidden', 'false');
}

/* ⚠️ 用事件委托而不是逐个绑：群列表每 15 秒重画一次，
   直接绑在节点上的话，新画出来的节点一个都收不到事件。 */
function initTips() {
  if (initTips._done) return;          // 幂等：别重复绑两遍
  initTips._done = true;
  try { document.documentElement.classList.add('js-tips'); } catch (e) { /* 无所谓 */ }
  document.addEventListener('mouseover', function (e) {
    const t = e.target && e.target.closest ? e.target.closest('[data-tip]') : null;
    if (t) { tipShow(t); return; }
    if (e.target && e.target.id === 'tipLayer') return;
    tipHide();
  });
  document.addEventListener('mouseout', function (e) {
    const t = e.target && e.target.closest ? e.target.closest('[data-tip]') : null;
    if (t) tipHide();
  });
  // 重画 / 滚动后原位置就不对了，直接收掉，下次移上去重新算
  window.addEventListener('scroll', tipHide, true);
  document.addEventListener('click', tipHide);
}

/* ---------- 测试选项：从 localStorage 恢复三个开关 ---------- */
function testOpt(k, def) {
  try {
    const o = JSON.parse(localStorage.getItem('biliTestOpt') || '{}');
    return o[k] === undefined ? def : !!o[k];
  } catch (e) { return def; }
}
function restoreTestOpt() {
  // ⚠️ real 默认 true：占位数据里 pic/images 全是空，卡片内嵌不到图，
  //    测试就看不出图片链路通不通；默认取测试 UID 的真实封面/配图。
  const DEF = { real: true, mark: true, sandbox: true };
  document.querySelectorAll('[data-act="toggle-testopt"]').forEach(el => {
    const k = el.getAttribute('data-opt');
    el.classList.toggle('on', testOpt(k, DEF[k] !== false));
  });
}

/* ---------- 首屏：默认落在「概况」 ---------- */
(function bootHome() {
  document.addEventListener('DOMContentLoaded', () => {
    initTips();
    goto('home');
    // 轮询间隔输入框改了 → 心跳周期立刻跟着变
    const p = document.getElementById('cPoll');
    if (p) {
      // 改间隔时也要带在线态，否则没启动的机器人会画出 ECG
      const sync = () => setHb(p.value || 12,
        !((STATE.heartbeat || {}).alive === false));
      p.addEventListener('change', sync);
      p.addEventListener('input', sync);
    }
  });
})();
