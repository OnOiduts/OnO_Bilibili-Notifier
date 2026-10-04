/* 界面音效：轻柔的合成音，不依赖任何音频文件。
 *
 * 为什么用 Web Audio 合成而不是放 mp3：
 *   1. 不发网络请求、不占包体积，离线也能响；
 *   2. 音量、频率、长短都能按场景调，容易压住"吵"和"刺耳"。
 *
 * 「别太吵太刺耳」具体怎么落实：
 *   - 只用 sine / triangle 两种波形。方波、锯齿波谐波多，听着扎耳朵，一律不用。
 *   - 频率压在 330~900Hz 这段人耳最柔和的区间，不做高频"叮"。
 *   - 主音量默认 0.5 档（约 0.10 线性增益），比系统提示音轻一大截。
 *   - 每个音都有 10ms 淡入 + 150~260ms 淡出。
 *     ⚠️ 没有淡入会"啪"一声爆音，那正是最刺耳的来源。
 *   - 同类音效 90ms 内不重复响，连点按钮不会连成一串。
 */
(function () {
  'use strict';

  var LS_KEY = 'bn_sound';       // 'off' | '1' | '2' | '3'（数字越大越响）
  var _ctx = null;
  var _last = {};                // 每种音效上次响的时间，用来节流
  var _throttle = 90;            // ms

  /* 三档音量。1 = 很轻（默认），3 = 稍明显。都是线性增益，不超过 0.2。 */
  var GAIN = { '1': 0.07, '2': 0.11, '3': 0.17 };

  function level() {
    try {
      var v = localStorage.getItem(LS_KEY);
      if (v === 'off' || v === '0') return null;
      if (v === '2' || v === '3') return v;
      return '1';
    } catch (_) { return '1'; }
  }

  function setLevel(v) {
    try {
      if (v === null) localStorage.setItem(LS_KEY, 'off');
      else localStorage.setItem(LS_KEY, String(v));
    } catch (_) { /* 隐私模式下写不了，忽略即可 */ }
  }

  /* AudioContext 懒创建。
     ⚠️ 浏览器不允许页面一打开就出声（autoplay 策略），
        必须等用户有过一次交互才能 resume，否则 context 一直是 suspended、
        调用后什么也不会响。这里在第一次播放时尝试 resume。 */
  function ctx() {
    if (_ctx) return _ctx;
    var AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    try { _ctx = new AC(); } catch (_) { return null; }
    return _ctx;
  }

  /* 单个音：波形 + 起止频率 + 时长 + 音量。
     频率做成滑动（f0 → f1）而不是死板一个音，听着更"活"一点。 */
  function tone(o) {
    var c = ctx();
    if (!c) return;
    if (c.state === 'suspended' && c.resume) { try { c.resume(); } catch (_) {} }

    var now = c.currentTime;
    var dur = o.dur || 0.16;
    var osc = c.createOscillator();
    var g = c.createGain();

    osc.type = o.type || 'sine';
    osc.frequency.setValueAtTime(o.f0, now);
    if (o.f1 && o.f1 !== o.f0) {
      osc.frequency.exponentialRampToValueAtTime(o.f1, now + dur);
    }

    var peak = o.gain;
    /* 淡入 10ms —— 没这一步就是爆音 */
    g.gain.setValueAtTime(0.0001, now);
    g.gain.exponentialRampToValueAtTime(peak, now + 0.01);
    /* 淡出到近零。exponential 不能到 0，用 0.0001 收尾 */
    g.gain.exponentialRampToValueAtTime(0.0001, now + dur);

    osc.connect(g);
    g.connect(c.destination);
    osc.start(now);
    osc.stop(now + dur + 0.02);
  }

  /* 音效表。数值都是反复权衡过的：能听见，但不抢戏。 */
  var SFX = {
    /* 成功：柔和上行纯五度（C5→G5），确认感，不刺耳 */
    ok: [{ f0: 660, f1: 880, dur: 0.13, type: 'sine', at: 0 },
         { f0: 880, f1: 990, dur: 0.16, type: 'sine', at: 0.075 }],
    /* 失败：下行小三度，用 triangle 稍暖一点。
       不做成低沉"嗡"也不做成高频"滴"，避免吓人一跳 */
    err: [{ f0: 440, f1: 330, dur: 0.20, type: 'triangle' }],
    /* 提醒 / 新消息：C-E-G 大三和弦分解，很轻，像远处风铃 */
    notify: [{ f0: 523, f1: 523, dur: 0.12, type: 'sine', at: 0 },
             { f0: 659, f1: 659, dur: 0.12, type: 'sine', at: 0.08 },
             { f0: 784, f1: 784, dur: 0.20, type: 'sine', at: 0.16 }],
    /* 点击：一声极轻的短促音，只给主按钮用，普通按钮不响 */
    click: [{ f0: 520, f1: 460, dur: 0.07, type: 'sine' }],
    /* 开关：短促上滑，表示"拨过去了" */
    toggle: [{ f0: 420, f1: 700, dur: 0.10, type: 'sine' }],
    /* 展开 / 收起：一抹很轻的气声感，下滑 vs 上滑区分方向 */
    fold: [{ f0: 620, f1: 430, dur: 0.11, type: 'sine' }],
    unfold: [{ f0: 430, f1: 620, dur: 0.11, type: 'sine' }],
    /* 复制 / 已完成：单音，比 ok 更收敛 */
    done: [{ f0: 740, f1: 740, dur: 0.12, type: 'sine' }]
  };

  var MASTER_SCALE = { ok: 1, err: 0.9, notify: 0.75,
                       click: 0.5, toggle: 0.7, fold: 0.45,
                       unfold: 0.45, done: 0.8 };

  function play(name) {
    var lv = level();
    if (!lv) return false;                 // 关了就一声不出
    var seq = SFX[name];
    if (!seq) return false;

    /* 节流：同一个音 90ms 内不重复。
       连点保存、批量渲染 toast 时不会叠成一团噪音。 */
    var t = Date.now();
    if (_last[name] && (t - _last[name]) < _throttle) return false;
    _last[name] = t;

    var base = (GAIN[lv] || GAIN['1']) * (MASTER_SCALE[name] || 1);
    seq.forEach(function (s) {
      var at = s.at || 0;
      var fire = function () {
        tone({ f0: s.f0, f1: s.f1, dur: s.dur, type: s.type, gain: base });
      };
      if (at > 0) setTimeout(fire, at * 1000);
      else fire();
    });
    return true;
  }

  window.Sound = {
    play: play,
    level: level,
    setLevel: setLevel,
    isOn: function () { return !!level(); },
    /* 试听：给设置页的开关用，切到某一档时响一声让用户知道有多响 */
    preview: function () { return play('done'); },
    names: Object.keys(SFX)
  };
})();
