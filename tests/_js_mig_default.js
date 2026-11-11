/* 真跑验证：订阅迁移清单
   ① 默认**一律不选**（迁移是破坏性操作，默认全勾等于把"确认"这一步省掉）
   ② 目标群已订过的 UP 主 / 合集：划线 + 置灰 + 标「目标群已有」
   ③ 换目标群要重画（哪些算"已有"变了，划线得跟着变）
   ④ 「只选已开启的」跳过目标群已经订过的
   ⑤ 且**禁止勾选**：点上去选不中（全选也不会带上）

   用法：node _js_mig_default.js（在 /data/workspace/_jsdom2 下，需 jsdom）
*/
const fs = require('fs');
const { JSDOM } = require('jsdom');
const ROOT = '/data/workspace/bili-notify/src';
const html = fs.readFileSync(ROOT + '/templates/index.html', 'utf8');
const app = fs.readFileSync(ROOT + '/static/app.js', 'utf8');

const dom = new JSDOM(html, { runScripts: 'dangerously', url: 'http://127.0.0.1:8088/' });
const w = dom.window;
w.fetch = () => Promise.resolve({
  ok: true, json: () => Promise.resolve({ ok: true }),
  text: () => Promise.resolve('{}'),
});
w.alert = () => {}; w.confirm = () => true;
const s = w.document.createElement('script');
s.textContent = app;
w.document.body.appendChild(s);
try {
  w.document.dispatchEvent(new w.Event('DOMContentLoaded', { bubbles: true }));
} catch (e) { console.log('派发异常:', e.message); }

const D = w.document;

function kinds(on) {
  const o = {};
  ['live', 'video', 'dynamic', 'top_comment'].forEach(k => {
    o[k] = { on: !!(on && on.indexOf(k) >= 0) };
  });
  return o;
}
/* 源群订了 A / B / C 三位；目标群已经订过 B 和 C */
const SRC = {
  gid: 'GSRC', name: '源群',
  subs: [
    { uid: '111', uname: '甲', kinds: kinds(['live', 'video']) },
    { uid: '222', uname: '乙', kinds: kinds(['dynamic']) },
    { uid: '333', uname: '丙', kinds: kinds(['live']) },
  ],
  seasons: [
    { uid: '111', season_id: '9001', title: '甲的合集', uname: '甲' },
    { uid: '222', season_id: '9002', title: '乙的合集', uname: '乙' },
  ],
};
const DST = {
  gid: 'GDST', name: '目标群',
  subs: [{ uid: '222', uname: '乙', kinds: kinds(['dynamic']) },
         { uid: '333', uname: '丙', kinds: kinds(['live']) }],
  seasons: [{ uid: '222', season_id: '9002', title: '乙的合集', uname: '乙' }],
};
const DST2 = { gid: 'GDST2', name: '另一个目标群', subs: [], seasons: [] };

/* app.js 里 STATE 是顶层 let —— 不挂 window，但从全局作用域 eval 能改到 */
w.eval('STATE.groups = ' + JSON.stringify([SRC, DST, DST2]) + '; STATE.config = {};');

function opt(sel, gid, name) {
  const o = D.createElement('option');
  o.value = gid; o.textContent = name;
  sel.appendChild(o);
}
const se = D.getElementById('migSrc');
const de = D.getElementById('migDst');
se.innerHTML = ''; de.innerHTML = '';
opt(se, SRC.gid, SRC.name); opt(de, DST.gid, DST.name); opt(de, DST2.gid, DST2.name);
se.value = SRC.gid;
de.value = DST.gid;
w.MIG_SCOPE = 'part';
w.MIG_SEASONS = true;

w.renderMigPick();

const ok = [];
function chk(name, cond, extra) {
  ok.push([name, !!cond]);
  console.log((cond ? 'PASS ' : 'FAIL ') + name + (extra ? '  → ' + extra : ''));
}

function cards() { return D.querySelectorAll('#migPickList .mig-card'); }
function seaCards() { return D.querySelectorAll('#migSeaList .mig-card'); }
function cardOf(uid) {
  return D.querySelector('#migPickList .mig-card[data-uid="' + uid + '"]');
}

// ① 默认一律不选
const anyOn = Array.prototype.filter.call(cards(), c => c.classList.contains('on')).length;
chk('① 默认一张都没勾（UP 主）', anyOn === 0, '实得 ' + anyOn + ' 张');
const anySeaOn = Array.prototype.filter.call(seaCards(), c => c.classList.contains('on')).length;
chk('① 默认一张都没勾（合集）', anySeaOn === 0, '实得 ' + anySeaOn + ' 张');
chk('① 清单确实渲染出来了', cards().length === 3, '实得 ' + cards().length + ' 张');

// ② 目标群已有 → 划线置灰 + 标签
const c1 = cardOf('111'), c2 = cardOf('222'), c3 = cardOf('333');
chk('② 甲（目标群没有）不划线', c1 && !c1.classList.contains('dup'));
chk('② 乙（目标群已订）划线置灰', c2 && c2.classList.contains('dup'));
chk('② 丙（目标群已订）划线置灰', c3 && c3.classList.contains('dup'));
const tagB = c2 && c2.querySelector('.dup-tag');
chk('② 带「目标群已有」标签', tagB && /目标群已有/.test(tagB.textContent),
  tagB ? tagB.textContent : '没有标签');
chk('② 划线卡片有悬停说明',
  c2 && /目标群已经订过/.test(c2.getAttribute('data-tip') || ''));
const s1 = D.querySelector('#migSeaList .mig-card[data-sid="9001"]');
const s2 = D.querySelector('#migSeaList .mig-card[data-sid="9002"]');
chk('② 合集：甲的不划线', s1 && !s1.classList.contains('dup'));
chk('② 合集：乙的（目标群已订）划线', s2 && s2.classList.contains('dup'));

// ⑤ 禁止勾选：点乙那张必须选不中（不能借迁移盖掉目标群的设置）
const ev = new w.MouseEvent('click', { bubbles: true, cancelable: true });
c2.dispatchEvent(ev);
chk('⑤ 划线卡片点不动（禁止重复迁移）', !c2.classList.contains('on'));
// 甲那张（目标群没有）仍然要能正常勾
c1.dispatchEvent(new w.MouseEvent('click', { bubbles: true, cancelable: true }));
chk('⑤ 目标群没有的仍能正常勾选', c1.classList.contains('on'));
c1.dispatchEvent(new w.MouseEvent('click', { bubbles: true, cancelable: true }));
chk('⑤ 再点一下能取消', !c1.classList.contains('on'));

// ⑤ 合集栏同样禁止
s2.dispatchEvent(new w.MouseEvent('click', { bubbles: true, cancelable: true }));
chk('⑤ 合集划线卡片点不动', !s2.classList.contains('on'));
s1.dispatchEvent(new w.MouseEvent('click', { bubbles: true, cancelable: true }));
chk('⑤ 合集不划线的仍能勾', s1.classList.contains('on'));
s1.dispatchEvent(new w.MouseEvent('click', { bubbles: true, cancelable: true }));

// ⑤「全选」也不能把目标群已有的带上
const allBtn = D.querySelector('[data-act="mig-pick"][data-all="1"]');
allBtn.dispatchEvent(new w.MouseEvent('click', { bubbles: true, cancelable: true }));
const onDupAfterAll = Array.prototype.filter.call(
  cards(), c => c.classList.contains('on') && c.classList.contains('dup')).length;
chk('⑤「全选」不勾划线卡片', onDupAfterAll === 0, '实得 ' + onDupAfterAll + ' 张');
const onAfterAll = Array.prototype.filter.call(cards(), c => c.classList.contains('on')).length;
chk('⑤「全选」仍勾上目标群没有的', onAfterAll === 1, '实得 ' + onAfterAll + ' 张');

// ④「只选已开启的」跳过目标群已有的
const btn = D.querySelector('[data-act="mig-pick"][data-all="on"]');
btn.dispatchEvent(new w.MouseEvent('click', { bubbles: true, cancelable: true }));
const onUids = Array.prototype.map.call(
  Array.prototype.filter.call(cards(), c => c.classList.contains('on')),
  c => c.getAttribute('data-uid'));
chk('④「只选已开启的」只勾上目标群没有的',
  onUids.length === 1 && onUids[0] === '111', '实得 ' + JSON.stringify(onUids));

// ③ 换目标群要重画：换成空群后不再有任何划线
de.value = DST2.gid;
w.renderMigPick();
const stillDup = Array.prototype.filter.call(cards(), c => c.classList.contains('dup')).length;
chk('③ 换目标群后划线全部消失', stillDup === 0, '实得 ' + stillDup + ' 张仍划线');
const stillDupSea = Array.prototype.filter.call(seaCards(), c => c.classList.contains('dup')).length;
chk('③ 合集栏同样重画', stillDupSea === 0, '实得 ' + stillDupSea + ' 张仍划线');

// 边界：源群 == 目标群时不该把所有人都划成"已有"
de.value = SRC.gid;
w.renderMigPick();
const selfDup = Array.prototype.filter.call(cards(), c => c.classList.contains('dup')).length;
chk('边界 源群=目标群 → 不划线', selfDup === 0, '实得 ' + selfDup + ' 张');

// 收集阶段最后一道闸：即使卡片同时带 on 和 dup 也不该进请求。
// ⚠️ 上面换过目标群、清单已重画，c2 是重画前的旧节点（已经不在 DOM 里），
//    必须重新取一次 —— 否则这一项会平凡通过（收集结果本来就是空）。
de.value = DST.gid;
w.renderMigPick();
const c2n = cardOf('222');
const c1n = cardOf('111');
chk('闸门前置：两张卡都还在 DOM 里', !!(c1n && c2n));
c2n.classList.add('on');
c1n.classList.add('on');
const pickedUids = (w.collectMigPicks() || []).map(p => String(p.uid));
chk('收集时滤掉划线的（最后一道闸）',
  pickedUids.indexOf('222') < 0, '实得 ' + JSON.stringify(pickedUids));
chk('最后一道闸没误伤目标群没有的',
  pickedUids.indexOf('111') >= 0, '实得 ' + JSON.stringify(pickedUids));
// 合集栏同理（重画后 s1/s2 也是旧节点，重新取）
const sn1 = D.querySelector('#migSeaList .mig-card[data-sid="9001"]');
const sn2 = D.querySelector('#migSeaList .mig-card[data-sid="9002"]');
sn1.classList.add('on');
sn2.classList.add('on');
const pickedSea2 = (w.collectMigSeasons() || []).map(String);
chk('合集栏同样滤掉划线的',
  pickedSea2.indexOf('9002') < 0 && pickedSea2.indexOf('9001') >= 0,
  '实得 ' + JSON.stringify(pickedSea2));

const css = fs.readFileSync(ROOT + '/static/style.css', 'utf8');
chk('CSS 有划线样式', /\.mig-card\.dup[^{]*\{[^}]*line-through/.test(css));
chk('CSS 有置灰样式', /\.mig-card\.dup:not\(\.on\)\s*\{[^}]*opacity/.test(css));

const bad = ok.filter(x => !x[1]);
console.log('\n合计 ' + ok.length + ' 项，失败 ' + bad.length + ' 项');
if (bad.length) { bad.forEach(x => console.log('  ✗ ' + x[0])); process.exit(1); }
console.log('ALL PASS');
