/* 「立即检测」结果块 + 合集封面裁剪：在**真实执行**层面验证（v1.98.6）。

   test_season_cover.py 是源码级断言（有没有写这段）；这份是把 crop1x1 /
   seasonFace / pushCheckNowCard 三个函数从 app.js 里抠出来，配一个最小
   DOM 桩**真跑一遍**，验的是行为不是字符串：
     · hdslb 封面确实被补成 @96w_96h_1c（1:1 裁剪），别家域名不动
     · 跑完「正在检测所有订阅…」那行确实被 remove 掉（旧代码会留着）
     · 结果块确实是 <details>、最新块展开、上一块收起
     · 失败/重跑不会把历史结果块一起抹掉

  ⚠️ 为什么不用 jsdom：沙盒装不上（无外网），而这三个函数只用到了
     createElement / querySelectorAll / insertBefore，最小桩足够。
*/
const fs=require('fs');
const path=require('path');
const _ROOT=path.resolve(__dirname,'..');
const JS=fs.readFileSync(path.join(_ROOT,'src','static','app.js'),'utf8');

// 从 app.js 里抠出要测的三个函数，配最小 DOM 桩真跑一遍
function grab(name){
  const i=JS.indexOf('function '+name+'(');
  if(i<0) throw new Error('找不到 '+name);
  let d=0,j=JS.indexOf('{',i);
  for(let k=j;k<JS.length;k++){ if(JS[k]==='{')d++; else if(JS[k]==='}'){d--; if(d===0) return JS.slice(i,k+1);} }
}
function escFn(v){return String(v==null?'':v).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
const esc=escFn;

// 最小 DOM：children / innerHTML / classList / querySelectorAll
class El{
  constructor(tag){this.tag=tag;this.children=[];this.className='';this._html='';this.attrs={};}
  set innerHTML(v){this._html=v;}
  get innerHTML(){return this._html;}
  setAttribute(k,v){this.attrs[k]=v;}
  getAttribute(k){return this.attrs[k];}
  classList(){const self=this;return {add(c){self.className=(self.className+' '+c).trim();},remove(c){self.className=self.className.replace(new RegExp('\\b'+c+'\\b'),'').trim();}};}
  insertBefore(el,ref){ if(ref==null)this.children.push(el); else this.children.splice(this.children.indexOf(ref),0,el);}
  removeChild(el){const i=this.children.indexOf(el);if(i>=0)this.children.splice(i,1);}
  get lastChild(){return this.children[this.children.length-1];}
  get firstChild(){return this.children[0];}
  querySelectorAll(sel){
    // 支持 ".cls" 与 "tag.cls"（真实的 CSS 选择器语义）
    let tag=null, cls=null;
    const m=sel.match(/^([a-zA-Z]*)(?:\.(.+))?$/);
    if(m){ tag=m[1]||null; cls=m[2]||null; } else { cls=sel.replace('.',''); }
    const out=[];
    const walk=n=>{ for(const c of n.children){
      const okTag=!tag||c.tag===tag;
      const okCls=!cls||(c.className||'').split(/\s+/).includes(cls);
      if(okTag&&okCls) out.push(c); walk(c);} };
    walk(this); return out;
  }
  remove(){ if(this._parent) this._parent.removeChild(this); }
}
const document={createElement:(t)=>new El(t)};
global.document=document;
global.setTimeout=(f,t)=>0;

eval(grab('crop1x1'));
eval(grab('seasonFace'));
eval(grab('pushCheckNowCard'));
global.CHECK_NOW_CARD_MAX=3;

let P=0,F=0;
const ck=(c,n)=>{ if(c){P++;console.log('  ok  ',n);} else {F++;console.log('  FAIL',n);} };

console.log('\n== crop1x1 真跑 ==');
ck(crop1x1('https://i0.hdslb.com/bfs/a.jpg')==='https://i0.hdslb.com/bfs/a.jpg@96w_96h_1c.jpg',
   'hdslb 图床 → 补 @96w_96h_1c.jpg：'+crop1x1('https://i0.hdslb.com/bfs/a.jpg'));
ck(crop1x1('https://i0.hdslb.com/bfs/a.jpg@672w_378h_1c.jpg')==='https://i0.hdslb.com/bfs/a.jpg@672w_378h_1c.jpg',
   '已经带过 @ 参数就不再叠一个');
ck(crop1x1('https://example.com/a.jpg')==='https://example.com/a.jpg',
   '非 hdslb 域名不动（套 B站参数会取不到）');
ck(crop1x1('')==='' && crop1x1(null)==='', '空值安全');
ck(crop1x1('data:image/svg+xml,xx')==='data:image/svg+xml,xx', 'data: URI 不动');

console.log('\n== seasonFace：优先封面 ==');
ck(seasonFace({cover:'https://i0.hdslb.com/bfs/c.jpg',face:'https://i0.hdslb.com/bfs/f.jpg'})
   ==='https://i0.hdslb.com/bfs/c.jpg@96w_96h_1c.jpg','有封面就用封面（裁1:1）');
ck(seasonFace({face:'https://i0.hdslb.com/bfs/f.jpg'})==='https://i0.hdslb.com/bfs/f.jpg@96w_96h_1c.jpg',
   '没封面退回 UP 主头像（老订阅不至于空白）');
ck(seasonFace({})==='' && seasonFace(null)==='', '都没有就是空 → 画占位箱子图标');

console.log('\n== pushCheckNowCard：清掉进度文字 + 折叠 ==');
const box=new El('div');
// 模拟「正在检测所有订阅…」那一行
const prog=new El('div'); prog.className='progress-text'; prog._parent=box;
box.children.push(prog);
const old1=new El('details'); old1.className='diag-card fold'; old1.open=true; old1._parent=box;
box.children.push(old1);

pushCheckNowCard(box,{at:'20:00:00',elapsed:'2.2',summary:'状态都没变，不推送',note:'x'},'<div>row</div>');
ck(box.querySelectorAll('.progress-text').length===0,
   '跑完「正在检测所有订阅…」那行没了（旧代码会留着）');
const cards=box.querySelectorAll('details');
ck(cards.length===2, '历史结果块没被清掉，共 2 块：'+cards.length);
const newest=cards[0];
ck(newest.tag==='details', '新块是 <details>（可折叠）');
ck(newest.className.indexOf('fold')>=0, '带 fold 类');
ck(newest.open===true, '最新那块默认展开');
ck(old1.open===false, '新块进来时把上一块收起');
ck(newest._html.indexOf('<summary class="fold-hd dcard-head">')>=0, '标题在 summary 里，整行可点');
ck(newest._html.indexOf('用了 2.2s')>=0, '块头有耗时');
ck(newest._html.indexOf('状态都没变，不推送')>=0, '块头有结论');
ck(newest._html.indexOf('<div>row</div>')>=0, '明细在 fold-bd 里');

console.log('\n== 只留最近 3 块 ==');
for(let i=0;i<3;i++) pushCheckNowCard(box,{at:'t'+i,summary:'s'+i},'<b>'+i+'</b>');
ck(box.children.length===3, '超过 3 块会丢掉最旧的：'+box.children.length);

console.log('\n通过 '+P+' / 失败 '+F);
process.exit(F?1:0);
