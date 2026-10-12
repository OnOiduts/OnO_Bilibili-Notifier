/* 真跑验证：「启动后首轮静默」开关点得动、外观会变、会自动保存。
   用法：node _grace.js   （在 /data/workspace/_jsdomtest 下，需 jsdom） */
const fs = require('fs');
const { JSDOM } = require('jsdom');
const html = fs.readFileSync('/data/workspace/bili-notify/src/templates/index.html', 'utf8');
const app = fs.readFileSync('/data/workspace/bili-notify/src/static/app.js', 'utf8');

const dom = new JSDOM(html, { runScripts: 'dangerously', url: 'http://127.0.0.1:8088/' });
const w = dom.window;
const posts = [];
w.fetch = (u, o) => {
  try {
    const b = JSON.parse((o && o.body) || '{}');
    if (b && Object.prototype.hasOwnProperty.call(b, 'startup_grace_rounds')) posts.push(b.startup_grace_rounds);
  } catch (e) { /* 忽略非 JSON */ }
  return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true }), text: () => Promise.resolve('{}') });
};
w.alert = () => {}; w.confirm = () => true;
const s = w.document.createElement('script');
s.textContent = app;
w.document.body.appendChild(s);
/* JSDOM 里文档早已加载完，DOMContentLoaded 不会再触发，
   而 initGraceOpt / initSwitches / initFormDirty 都挂在它上面 ——
   必须手动派发一次，否则一个监听都绑不上（表现为"点了毫无反应"）。 */
try {
  w.document.dispatchEvent(new w.Event('DOMContentLoaded', { bubbles: true }));
} catch (e) { console.log('派发异常:', e.message); }

const D = w.document;
const on = D.getElementById('cGraceOn');
const num = D.getElementById('cGraceRounds');
const lb = on && on.closest ? on.closest('.sw-btn') : null;

function click(el) {
  const ev = new w.MouseEvent('click', { bubbles: true, cancelable: true });
  el.dispatchEvent(ev);
}
function snap(tag) {
  console.log(tag, '| checked=', on.checked, '| label.on=', lb.classList.contains('on'),
    '| rounds=', num.value, '| disabled=', num.disabled, '| posts=', JSON.stringify(posts));
}

console.log('注入异常/启动失败:', JSON.stringify(w.__BOOT_FAILS || []));
snap('初始            ');

// 1) 关掉开关（初始默认开）
click(lb);
snap('点一下（关）    ');

// 2) 再打开
click(lb);
snap('再点一下（开）  ');

// 3) 改轮数（开着的框可改）
num.value = '3';
num.dispatchEvent(new w.Event('change', { bubbles: true }));
snap('轮数改成 3      ');
console.log('     改轮数后外观是否跟着变：label.on=', lb.classList.contains('on'),
  'checked=', on.checked, '→',
  lb.classList.contains('on') === on.checked ? '一致' : '不一致（滑块没跟上）');

// 4) 关掉后轮数应为 0
click(lb);
snap('再关掉          ');

const ok = [];
ok.push(['关掉后 checked=false', on.checked === false]);
ok.push(['关掉后外观 .on 已移除', lb.classList.contains('on') === false]);
ok.push(['关掉后轮数归 0', num.value === '0']);
ok.push(['关掉后框被禁用', num.disabled === true]);
ok.push(['改动触发了自动保存', posts.length >= 3]);
ok.push(['保存值最后为 0', posts[posts.length - 1] === 0]);
// 改轮数会让开关跟着开/关：此时外观必须同步（程序赋值不触发 change，
// 所以 syncGraceOn 里必须手动 paintSwitches）
console.log('     [复检] 置为 3 → 开');
num.value = '3';
num.dispatchEvent(new w.Event('change', { bubbles: true }));
ok.push(['改轮数后滑块跟着变', lb.classList.contains('on') === on.checked]);
console.log('---- 判定 ----');
ok.forEach(([k, v]) => console.log(v ? 'PASS' : 'FAIL', k));
console.log('全部通过:', ok.every(x => x[1]));
try { w.close(); } catch (e) {}
process.exit(0);
