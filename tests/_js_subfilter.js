const fs=require('fs');const {JSDOM}=require('jsdom');
const path=require('path');
const BASE=path.join(__dirname,'..','src');
const html=fs.readFileSync(BASE+'/templates/index.html','utf8');
const app=fs.readFileSync(BASE+'/static/app.js','utf8');
const dom=new JSDOM(html,{runScripts:'dangerously',url:'http://127.0.0.1:8088/'});
const w=dom.window; const D=w.document;
w.fetch=()=>Promise.resolve({ok:true,json:()=>Promise.resolve({ok:true}),text:()=>Promise.resolve('{}')});
w.alert=()=>{}; w.confirm=()=>true;
// jsdom 没有 requestAnimationFrame：KPI 数字滚动动画（countUp）会直接抛错，
// 于是第二次以后的 renderStats 全崩。真实浏览器里有，这里补个桩。
w.requestAnimationFrame=(fn)=>setTimeout(()=>fn(Date.now()),0);
w.cancelAnimationFrame=(id)=>clearTimeout(id);
const s=w.document.createElement('script'); s.textContent=app; w.document.body.appendChild(s);
console.log('启动失败项:', JSON.stringify(w.__BOOT_FAILS||[]));
// ⚠️ 脚本是 DOMContentLoaded 之后注入的，那个回调不会再触发，
//    这里手动跑一遍启动里的绑定（真实页面由 DOMContentLoaded 调用）
w.eval('initFormDirty()');

let pass=0, fail=0;
function ck(name, got, want){
  const ok = String(got)===String(want);
  console.log((ok?'[PASS] ':'[FAIL] ')+name+' → '+got+(ok?'':'（期望 '+want+'）'));
  ok?pass++:fail++;
}

// ---- 三个群，订阅大量重复：UP甲被 3 个群都订了，UP乙 2 个群，UP丙 1 个群
//      合集 S1 被 2 个群订，S2 被 1 个群订
const G=[
 {gid:'G1',name:'群一',subs:[{uid:'111',uname:'UP甲',kinds:{}},{uid:'222',uname:'UP乙',kinds:{}}],
  seasons:[{uid:'111',season_id:'S1',title:'合集一',uname:'UP甲'}]},
 {gid:'G2',name:'群二',subs:[{uid:'111',uname:'UP甲',kinds:{}}],
  seasons:[{uid:'111',season_id:'S1',title:'合集一',uname:'UP甲'}]},
 {gid:'G3',name:'群三',subs:[{uid:'111',uname:'UP甲',kinds:{}},{uid:'333',uname:'UP丙',kinds:{}}],
  seasons:[{uid:'333',season_id:'S2',title:'合集二',uname:'UP丙'}]},
];
w.eval('STATE.groups='+JSON.stringify(G)+'; EXPANDED.clear();');

// ---------- ① 订阅数去重 ----------
w.eval('renderStats()');
const cards=[...D.querySelectorAll('#statsBox .kpi')];
const subCard=cards.find(c=>c.querySelector('.lb').textContent==='订阅');
ck('订阅总数（去重 3UP+2合集）', subCard.querySelector('.kpi-num').textContent, 5);
ck('小字含 UP 主数', /UP 主 3/.test(subCard.querySelector('.sb').textContent), true);
ck('小字含合集数', /合集 2/.test(subCard.querySelector('.sb').textContent), true);
ck('小字标注已去重', /已去重/.test(subCard.querySelector('.sb').textContent), true);
// 未去重口径应为 4+3=7，确认两者确实不同（证明去重真起了作用）
w.eval(`(function(){var a=0,b=0;STATE.groups.forEach(function(x){a+=(x.subs||[]).length;b+=(x.seasons||[]).length;});window.__RAW=a+b;})()`);
ck('未去重口径（应≠5）', w.__RAW!==5, true);

// ---------- ② 按 UP 主筛群 ----------
const fk=D.getElementById('subFilterKind');
const fp=D.getElementById('subFilterPick');
ck('筛选下拉有"按 UP 主"', [...fk.options].some(o=>o.value==='up'), true);
ck('筛选下拉有"按合集"', [...fk.options].some(o=>o.value==='season'), true);
fk.value='up'; fk.dispatchEvent(new w.Event('change'));
ck('切到 UP 后条目行可见', D.getElementById('subFilterPickRow').hidden, false);
ck('候选条目数（3个UP+不限）', fp.options.length, 4);
ck('条目带群数（UP甲=3个群）', /UP甲.*3 个群/.test(fp.textContent), true);
// 条目按名称排序，默认落在第一个具体条目上（切过来就能用，不用再选一次）
const firstUp=[...fp.options].find(o=>o.value!=='').value;
ck('默认选中第一个具体条目', fp.value, firstUp);
ck('默认不是"不限"', fp.value!=='', true);
fp.value='111'; fp.dispatchEvent(new w.Event('change'));
w.eval('renderGroups(true)');
ck('筛出订了UP甲的群数', D.querySelectorAll('#groupsBox .grp').length, 3);
fp.value='333'; fp.dispatchEvent(new w.Event('change'));
ck('只订UP丙的群数', D.querySelectorAll('#groupsBox .grp').length, 1);
ck('留下的群是群三', D.querySelector('#groupsBox .grp-head').textContent.includes('群三'), true);

// ---------- ③ 按合集筛群 ----------
fk.value='season'; fk.dispatchEvent(new w.Event('change'));
ck('合集条目标签', D.getElementById('subFilterPickLabel').textContent, '选择合集');
ck('合集候选数（2个+不限）', fp.options.length, 3);
ck('默认选中第一个合集', fp.value, [...fp.options].find(o=>o.value!=='').value);
// ⚠️ 合集的键**只用 season_id**（B站合集ID全局唯一）：
//    以前拼 "uid:season_id"，某个群没存 uid 就拆成两条、一个合集数成两个
fp.value='S1'; fp.dispatchEvent(new w.Event('change'));
w.eval('renderGroups(true)');
ck('订了合集一的群数', D.querySelectorAll('#groupsBox .grp').length, 2);

// ---------- ③b 真实写法：数字型合集 ID ----------
w.eval(`STATE.groups=[
 {gid:'G1',name:'群一',subs:[],
  seasons:[{uid:'111',season_id:3175959,title:'合集甲',uname:'UP甲'}]},
 {gid:'G2',name:'群二',subs:[],
  seasons:[{uid:'0',season_id:3175959,title:'合集甲',uname:''}]},
 {gid:'G3',name:'群三',subs:[],
  seasons:[{uid:'333',season_id:3175960,title:'合集乙',uname:'UP丙'}]}];
 EXPANDED.clear();`);
fk.value='season'; fk.dispatchEvent(new w.Event('change'));
ck('数字ID：去重后候选数（2+不限）', fp.options.length, 3);
// uid 缺失那条不会被拆成第二条
ck('数字ID：同一个合集只列一次', /合集甲/.test(fp.textContent), true);
fp.value='3175959'; fp.dispatchEvent(new w.Event('change'));
w.eval('renderGroups(true)');
ck('数字ID：订了合集甲的群（含uid缺失那个）', D.querySelectorAll('#groupsBox .grp').length, 2);
w.eval('renderStats()');
// ⚠️ 必须重新查：renderStats 会整块重画 #statsBox，
//    拿 ① 里缓存的旧节点读到的还是上一次的数字。
ck('数字ID：订阅卡合集去重为2',
   [...D.querySelectorAll('#statsBox .kpi')].find(c=>c.querySelector('.lb').textContent==='订阅')
     .querySelector('.kpi-num').textContent, 2);

// 还原三组数据继续后面的用例
w.eval('STATE.groups='+JSON.stringify(G)+'; EXPANDED.clear();');
fk.value='season'; fk.dispatchEvent(new w.Event('change'));
fp.value='S1'; fp.dispatchEvent(new w.Event('change'));
w.eval('renderGroups(true)');

// ---------- ④ 与关键词叠加 ----------
const sb=D.getElementById('subSearch');
sb.value='群一'; sb.dispatchEvent(new w.Event('input'));
ck('合集一+关键词"群一"', D.querySelectorAll('#groupsBox .grp').length, 1);
sb.value='不存在的名字'; sb.dispatchEvent(new w.Event('input'));
ck('无匹配时提示', /没有符合条件的群/.test(D.getElementById('groupsBox').textContent), true);

// ---------- ⑤ 清空要连筛选一起复位 ----------
sb.value=''; 
const btn=[...D.querySelectorAll('[data-act="sub-search-clear"]')][0];
btn.click();
ck('清空后筛选方式复位', fk.value, 'all');
ck('清空后条目行隐藏', D.getElementById('subFilterPickRow').hidden, true);
ck('清空后显示全部群', D.querySelectorAll('#groupsBox .grp').length, 3);

// ---------- ⑥ 只按群名/群号 ----------
fk.value='name'; fk.dispatchEvent(new w.Event('change'));
sb.value='UP甲'; sb.dispatchEvent(new w.Event('input'));
ck('只按群名时不蹭中订阅项', D.querySelectorAll('#groupsBox .grp').length, 0);
sb.value='群二'; sb.dispatchEvent(new w.Event('input'));
ck('只按群名命中群名', D.querySelectorAll('#groupsBox .grp').length, 1);
sb.value=''; sb.dispatchEvent(new w.Event('input'));
fk.value='all'; fk.dispatchEvent(new w.Event('change'));

// ---------- ⑦ 折叠卡片 ----------
const img=D.getElementById('imgCard'), bk=D.getElementById('bkCard');
ck('图片卡片是 details', img.tagName, 'DETAILS');
ck('备份卡片是 details', bk.tagName, 'DETAILS');
ck('图片卡片默认收起', img.hasAttribute('open'), false);
ck('备份卡片默认收起', bk.hasAttribute('open'), false);
ck('图片卡片有摘要位', !!D.getElementById('imgCardSum'), true);
ck('备份卡片有摘要位', !!D.getElementById('bkCardSum'), true);
ck('图片卡片 h3 在 summary 内', img.querySelector('summary h3').textContent.includes('图片'), true);
// 展开后内容可见
img.setAttribute('open','');
ck('展开后内容在 DOM 里', !!img.querySelector('.fold-bd #cImgMode'), true);
// 摘要能跟着设置变
w.eval(`STATE.config=STATE.config||{};`);
const im=D.getElementById('cImgMode'); im.value='url';
w.eval('syncImgCardSum()');
ck('摘要跟随发送方式', D.getElementById('imgCardSum').textContent.includes('平台拉取'), true);
D.getElementById('cImage').checked=false;
w.eval('syncImgCardSum()');
ck('关掉带图后摘要说明', D.getElementById('imgCardSum').textContent.includes('纯文字'), true);

console.log('\n结果: '+pass+' 通过 / '+fail+' 失败');
console.log('动作失败:', JSON.stringify(w.__ACT_FAILS||[]));
try{w.close();}catch(e){}
process.exit(fail?1:0);
