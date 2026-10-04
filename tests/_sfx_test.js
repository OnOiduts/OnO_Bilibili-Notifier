/* v2.0.0 界面音效：真跑一遍验证「能不能响、响得吵不吵」。
 *
 * ⚠️ 只做静态文本断言是不够的 —— 音效能不能出声、频率会不会刺耳、
 *    节流有没有生效，都得真的把振荡器造出来看参数才知道。
 *    这里用假的 AudioContext 把每次造音的参数全记下来再断言。
 */
const fs = require('fs');

const SRC = '/data/workspace/bili-notify/src/static/sound.js';
let pass = 0, fail = 0;
function ok(name, cond, extra) {
  if (cond) { pass++; console.log('  [OK] ' + name); }
  else { fail++; console.log('  [FAIL] ' + name + (extra ? ' → ' + extra : '')); }
}

/* ---------- 假的 Web Audio ---------- */
const notes = [];   // 每个音一份记录
function makeCtx() {
  const ctx = {
    state: 'running',
    currentTime: 0,
    resumed: 0,
    resume() { this.resumed++; },
    destination: {},
    createOscillator() {
      const note = { type: null, freq: [], g: [], started: false };
      notes.push(note);
      return {
        set type(v) { note.type = v; },
        get type() { return note.type; },
        frequency: {
          setValueAtTime: v => note.freq.push(['set', v]),
          exponentialRampToValueAtTime: v => note.freq.push(['ramp', v]),
        },
        connect() {},
        start() { note.started = true; },
        stop() {},
      };
    },
    createGain() {
      /* ⚠️ sound.js 的 tone() 是先造振荡器、再造 gain，
         而且 gain 的赋值发生在 connect 之前 ——
         所以这里直接绑到"刚造出来那个音"上，不能在 connect 时才绑。 */
      const note = notes[notes.length - 1];
      return {
        gain: {
          setValueAtTime: v => note.g.push(['set', v]),
          exponentialRampToValueAtTime: v => note.g.push(['ramp', v]),
        },
        connect() {},
      };
    },
  };
  return ctx;
}

const store = {};
global.localStorage = {
  getItem: k => (Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); },
};
global.window = { AudioContext: function () { return makeCtx(); } };
global.setTimeout = (fn, ms) => { /* 不真的等，直接同步跑，方便断言 */ fn(); return 0; };

/* ---------- 加载被测脚本 ---------- */
const code = fs.readFileSync(SRC, 'utf-8');
(0, eval)(code);
const S = global.window.Sound;

console.log('== 界面音效 v2.0.0 ==');

ok('Sound 已挂到 window', !!S && typeof S.play === 'function');
ok('默认档位是「很轻」', S.level() === '1', S.level());
ok('默认处于开启状态', S.isOn() === true);

/* --- 能出声 --- */
notes.length = 0;
ok('play("ok") 返回 true', S.play('ok') === true);
ok('确实造出了振荡器', notes.length >= 1, 'notes=' + notes.length);
ok('振荡器真的 start 了', notes.every(n => n.started));

/* --- 不刺耳：波形、频率 --- */
const WAVE_OK = ['sine', 'triangle'];
ok('只用柔和波形（无方波/锯齿）',
   notes.every(n => WAVE_OK.indexOf(n.type) >= 0),
   notes.map(n => n.type).join(','));

const freqs = [];
notes.forEach(n => n.freq.forEach(f => freqs.push(f[1])));
ok('频率都在 330~990Hz 的柔和区间',
   freqs.length > 0 && freqs.every(f => f >= 330 && f <= 990),
   freqs.join(','));

/* --- 不刺耳：有淡入淡出（没有就会"啪"一声爆音） --- */
ok('每个音都有淡入（gain 从近零升到峰值）',
   notes.every(n => n.g.length >= 2 &&
     n.g[0][0] === 'set' && n.g[0][1] > 0 && n.g[0][1] < 0.001),
   JSON.stringify(notes.map(n => n.g[0])));
ok('每个音都有淡出（收尾降到近零）',
   notes.every(n => n.g[n.g.length - 1][1] < 0.001),
   JSON.stringify(notes.map(n => n.g[n.g.length - 1])));

/* --- 音量确实很轻 --- */
const peaks = notes.map(n => n.g[1] && n.g[1][1]).filter(v => typeof v === 'number');
ok('峰值增益不超过 0.2（比系统提示音轻一大截）',
   peaks.length > 0 && peaks.every(v => v <= 0.2), peaks.join(','));

/* --- 节流：连点不会连成一串 --- */
notes.length = 0;
const a = S.play('click');
const b = S.play('click');
ok('同一个音 90ms 内不重复响（节流生效）', a === true && b === false,
   'a=' + a + ' b=' + b);

/* --- 关掉就一声不出 --- */
S.setLevel(null);
ok('关闭后 isOn() 为假', S.isOn() === false);
notes.length = 0;
ok('关闭后 play 不出声', S.play('ok') === false && notes.length === 0);

/* --- 再打开又能响，且档位可变 --- */
S.setLevel('3');
ok('重新打开后能出声', S.play('done') === true);
ok('档位读回 3', S.level() === '3');

/* --- 音效名写错不能崩 --- */
let threw = false;
try { S.play('不存在的音效名'); } catch (e) { threw = true; }
ok('未知音效名不抛异常', threw === false);

console.log('  小计：' + pass + ' 通过 / ' + fail + ' 失败');
process.exit(fail ? 1 : 0);
