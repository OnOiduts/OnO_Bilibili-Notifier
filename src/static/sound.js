/* 界面音效：轻柔的合成音，不依赖任何音频文件。
 *
 * 为什么用 Web Audio 合成而不是放 mp3：
 *   1. 不发网络请求、不占包体积，离线也能响；
 *   2. 音量、频率、长短都能按场景调，容易压住"吵"和"刺耳"。
 *
 * 「别太吵太刺耳」具体怎么落实（新增音效时必须一并遵守）：
 *   - 只用 sine / triangle 两种波形。方波、锯齿波谐波多，听着扎耳朵，一律不用。
 *   - 频率压在 330~900Hz 这段人耳最柔和的区间，不做高频"叮"。
 *   - 主音量默认 1 档（约 0.07 线性增益），比系统提示音轻一大截。
 *   - 每个音都有 10ms 淡入 + 全程淡出。
 *     ⚠️ 没有淡入会"啪"一声爆音，那正是最刺耳的来源。
 *   - 同类音效 140ms 内不重复响，连点按钮不会连成一串。
 *   - 每个音效都要在 MASTER_SCALE 里配一个系数，越"背景"的音越轻。
 *     ⚠️ 漏配不是小事：漏了会取默认 1，等于让本该很轻的音按满音量响。
 *
 * ⚠️ 轮询类操作（每 12 秒一次的自动刷新）一律不加音效，
 *    否则界面会自己不停地响，再轻也烦人。
 */
(function () {
  'use strict';

  var LS_KEY = 'bn_sound';       // 'off' | '1' | '2' | '3'（数字越大越响）
  var _ctx = null;
  var _last = {};                // 每种音效上次响的时间，用来节流
  var _throttle = 140;           // ms

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

  /* 音效表。数值都是反复权衡过的：能听见，但不抢戏。
     同一个音可以写多段（at 是相对起点的秒数），做和弦或上下行。 */
  var SFX = {
    /* ---------- 结果类 ---------- */
    /* 成功：柔和上行（D5→G5→A5），确认感，不刺耳。
       ⚠️ 收尾音原本写到 990Hz，超出"330~900"这条硬指标；
          高音区每往上一点都会明显更扎耳，所以压回 880。 */
    ok: [{ f0: 587, f1: 784, dur: 0.13, type: 'sine', at: 0 },
         { f0: 784, f1: 880, dur: 0.16, type: 'sine', at: 0.075 }],
    /* 失败：下行小三度，用 triangle 稍暖一点。
       不做成低沉"嗡"也不做成高频"滴"，避免吓人一跳 */
    err: [{ f0: 440, f1: 330, dur: 0.20, type: 'triangle' }],
    /* 警告：两声持平的短音，比 err 温和，表示"要留意"而不是"坏了" */
    warn: [{ f0: 560, f1: 560, dur: 0.09, type: 'triangle', at: 0 },
           { f0: 560, f1: 560, dur: 0.09, type: 'triangle', at: 0.12 }],
    /* 完成 / 已就绪：比 ok 更收敛的单音 */
    done: [{ f0: 740, f1: 740, dur: 0.12, type: 'sine' }],

    /* ---------- 操作类 ---------- */
    /* 主按钮：一声极轻的短促音 */
    click: [{ f0: 520, f1: 460, dur: 0.07, type: 'sine' }],
    /* 普通按钮 / 次要操作：比 click 更轻更短，只是"点到了"的手感 */
    tap: [{ f0: 480, f1: 430, dur: 0.05, type: 'sine' }],
    /* 开关：短促上滑，表示"拨过去了" */
    toggle: [{ f0: 420, f1: 700, dur: 0.10, type: 'sine' }],
    /* 保存：上行两音，收尾比 ok 短，表示"写进去了" */
    save: [{ f0: 587, f1: 784, dur: 0.11, type: 'sine' }],
    /* 复制：一声清脆但不尖的短音 */
    copy: [{ f0: 660, f1: 880, dur: 0.08, type: 'sine' }],
    /* 添加订阅：上行，带一点"装进去"的完成感 */
    add: [{ f0: 523, f1: 784, dur: 0.14, type: 'sine' }],
    /* 移除订阅：下行，但不像 err 那么重 */
    remove: [{ f0: 660, f1: 440, dur: 0.14, type: 'sine' }],
    /* 删除：比 remove 更低一档，明确"没了" */
    del: [{ f0: 520, f1: 349, dur: 0.18, type: 'triangle' }],
    /* 清空：一声下行气声，配合列表整体消失 */
    clear: [{ f0: 700, f1: 380, dur: 0.20, type: 'sine' }],

    /* ---------- 界面类 ---------- */
    /* 展开 / 收起：一抹很轻的气声感，下滑 vs 上滑区分方向 */
    fold: [{ f0: 620, f1: 430, dur: 0.11, type: 'sine' }],
    unfold: [{ f0: 430, f1: 620, dur: 0.11, type: 'sine' }],
    /* 面板 / 弹层打开：上滑，比 unfold 略长 */
    panel: [{ f0: 440, f1: 660, dur: 0.14, type: 'sine' }],
    /* 面板 / 弹层关闭：下滑 */
    panelClose: [{ f0: 620, f1: 420, dur: 0.12, type: 'sine' }],
    /* 大板块切换：一记很轻的"翻页"感 */
    page: [{ f0: 490, f1: 620, dur: 0.10, type: 'sine' }],
    /* 小标签切换：比 page 更短更轻 */
    tab: [{ f0: 560, f1: 640, dur: 0.07, type: 'sine' }],
    /* 刷新：向上一段短滑，配合按钮转圈 */
    refresh: [{ f0: 500, f1: 720, dur: 0.12, type: 'sine' }],

    /* ---------- 状态类 ---------- */
    /* 连接中：两声试探性的短音 */
    connect: [{ f0: 466, f1: 466, dur: 0.08, type: 'sine', at: 0 },
              { f0: 587, f1: 587, dur: 0.08, type: 'sine', at: 0.11 }],
    /* 已连上：上行纯四度 + 停留，明确"通了" */
    connected: [{ f0: 523, f1: 698, dur: 0.14, type: 'sine' }],
    /* 断开：下行，温和，不吓人 */
    disconnect: [{ f0: 587, f1: 392, dur: 0.16, type: 'triangle' }],
    /* 登录成功：C-E-G 上行三音，比 ok 更"开"一点 */
    login: [{ f0: 523, f1: 523, dur: 0.10, type: 'sine', at: 0 },
            { f0: 659, f1: 659, dur: 0.10, type: 'sine', at: 0.08 },
            { f0: 784, f1: 784, dur: 0.18, type: 'sine', at: 0.16 }],

    /* ---------- 数据类 ---------- */
    /* 导入：连贯上行三音，像"东西进来了" */
    import: [{ f0: 440, f1: 523, dur: 0.10, type: 'sine', at: 0 },
             { f0: 523, f1: 659, dur: 0.10, type: 'sine', at: 0.08 },
             { f0: 659, f1: 784, dur: 0.15, type: 'sine', at: 0.16 }],
    /* 导出：连贯下行三音，与 import 成对 */
    export: [{ f0: 784, f1: 659, dur: 0.10, type: 'sine', at: 0 },
             { f0: 659, f1: 523, dur: 0.10, type: 'sine', at: 0.08 },
             { f0: 523, f1: 440, dur: 0.15, type: 'sine', at: 0.16 }],
    /* 下载完成：一声短促的落定 */
    download: [{ f0: 587, f1: 880, dur: 0.13, type: 'sine' }],

    /* ---------- 通知类 ---------- */
    /* 提醒 / 新消息：C-E-G 大三和弦分解，很轻，像远处风铃 */
    notify: [{ f0: 523, f1: 523, dur: 0.12, type: 'sine', at: 0 },
             { f0: 659, f1: 659, dur: 0.12, type: 'sine', at: 0.08 },
             { f0: 784, f1: 784, dur: 0.20, type: 'sine', at: 0.16 }],
    /* 铃声：两声，比 notify 短，用于一次性提醒 */
    bell: [{ f0: 784, f1: 784, dur: 0.13, type: 'sine', at: 0 },
           { f0: 659, f1: 659, dur: 0.18, type: 'sine', at: 0.13 }]
  };

  /* 每种音效的音量系数。越"背景"的操作越轻：
     折叠、普通点击这些高频动作必须压住，否则点几下就烦。
     ⚠️ 新增音效务必在这里登记，漏了会按 1 满音量响。 */
  var MASTER_SCALE = {
    ok: 1, err: 0.9, warn: 0.8, done: 0.8,
    click: 0.5, tap: 0.32, toggle: 0.7, save: 0.85, copy: 0.6,
    add: 0.85, remove: 0.8, del: 0.85, clear: 0.8,
    fold: 0.45, unfold: 0.45, panel: 0.6, panelClose: 0.55,
    page: 0.55, tab: 0.4, refresh: 0.6,
    connect: 0.5, connected: 0.75, disconnect: 0.7, login: 0.9,
    import: 0.7, export: 0.7, download: 0.75,
    notify: 0.75, bell: 0.7
  };

  function play(name) {
    var lv = level();
    if (!lv) return false;                 // 关了就一声不出
    var seq = SFX[name];
    if (!seq) return false;

    /* 节流：同一个音 140ms 内不重复。
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
