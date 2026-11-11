# -*- coding: utf-8 -*-
"""前端「自动续期回显」真值测试。

⚠️ 不是「源码里有没有这段字」那种字符串断言：把 checkAutoRenew
   的函数体从 app.js 里切出来，放进 node 的 vm，配上假的 api / DOM，
   真的调用它，检查它写进元素的文本和定时器行为。

反向验证见 _rv_auto_renew.py（删掉函数 / 删掉挂载 / 删掉 DOM 元素都要红）。
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
APPJS = os.path.join(ROOT, "src", "static", "app.js")
HTML = os.path.join(ROOT, "src", "templates", "index.html")

FAILS = []


def chk(name, cond, extra=""):
    if cond:
        print(f"  [ok] {name}")
    else:
        print(f"  [FAIL] {name} {extra}")
        FAILS.append(name)


def slice_check_fn(js: str) -> str:
    """取出 let AUTO_RENEW_LAST 到 checkAutoRenew 结束这一段。"""
    a = js.find("let AUTO_RENEW_LAST")
    b = js.find("async function checkAutoRenew", a)
    c = js.find("\nasync function pollBrowser", b)
    if a < 0 or b < 0 or c < 0:
        return ""
    return js[a:c]


NODE_HARNESS = r"""
const vm = require('vm');
const assert = require('assert');
const fs = require('fs');
const fnSrc = fs.readFileSync(process.argv[2], 'utf8');

function makeCtx(reply, withEl) {
  const el = withEl ? {className: '', textContent: '', writes: 0} : null;
  const missing = {_missing: true, className: '', textContent: ''};
  const timers = [];
  const ctx = {
    api: async () => reply,
    $: id => (id === 'autoRenewNote' && el) ? new Proxy(el, {
      set(t, k, v) { t[k] = v; t.writes = (t.writes || 0) + 1; return true; }
    }) : (id === 'autoRenewNote' ? missing : missing),
    setInterval: (fn, ms) => { timers.push(ms); return 1; },
    pollRefresh: () => {},
    REFRESH_TIMER: null,
    console,
  };
  ctx.$ = id => {
    if (id !== 'autoRenewNote') return missing;
    if (!el) return missing;
    return new Proxy(el, {set(t,k,v){ t[k]=v; t.writes=(t.writes||0)+1; return true; }});
  };
  vm.createContext(ctx);
  vm.runInContext(fnSrc + '\nglobalThis.__run = checkAutoRenew;', ctx);
  return {ctx, el, timers};
}

(async () => {
  const results = [];
  function t(name, fn) {
    try { fn(); results.push(['ok', name]); }
    catch (e) { results.push(['FAIL', name + ' :: ' + e.message]); }
  }
  await (async () => {
    // 1. 首次拿到自动续期文案 → 显示在独立元素里
    let {ctx, el, timers} = makeCtx({ok:true, auto:'到期提醒触发，已自动开始续期', running:false}, true);
    await ctx.__run();
    t('首次文案写进 autoRenewNote', () => {
      assert.ok(el.textContent.includes('到期提醒触发'), el.textContent);
      assert.ok(el.className.includes('show'), el.className);
    });
    // 2. 同一文案再来一次 → 不重复写（否则 15s 轮询会反复重画/响音效）
    const before = el.writes || 0;
    await ctx.__run();
    t('同一文案不重复写', () => assert.equal(el.writes || 0, before));
    // 3. 文案变了 → 更新
    ctx.api = async () => ({ok:true, auto:'自动续期未能启动，10 分钟后再试', running:false});
    await ctx.__run();
    t('文案变化会更新', () => assert.ok(el.textContent.includes('未能启动'), el.textContent));
    // 4. 文案清空 → 元素收起
    ctx.api = async () => ({ok:true, auto:'', running:false});
    await ctx.__run();
    t('清空后收起', () => {
      assert.equal(el.textContent, '');
      assert.ok(!el.className.includes('show'), el.className);
    });
    // 5. 后台任务在跑且没有手动轮询 → 挂 2s 进度轮询
    ({ctx, el, timers} = makeCtx({ok:true, auto:'', running:true}, true));
    await ctx.__run();
    t('自动任务运行时挂 2000ms 轮询', () => assert.deepEqual(timers, [2000]));
    // 6. 元素缺失（旧模板）→ 不抛异常
    ({ctx, el, timers} = makeCtx({ok:true, auto:'x', running:false}, false));
    let threw = false;
    try { await ctx.__run(); } catch (e) { threw = true; }
    t('DOM 元素缺失不抛异常', () => assert.equal(threw, false));
    // 7. 接口失败 → 不写
    ({ctx, el, timers} = makeCtx({ok:false}, true));
    await ctx.__run();
    t('接口 ok=false 不写元素', () => assert.equal(el.textContent, ''));
  })();
  for (const [s, n] of results) console.log(s === 'ok' ? `  [ok] ${n}` : `  [FAIL] ${n}`);
  process.exit(results.some(r => r[0] === 'FAIL') ? 1 : 0);
})();
"""


def run_node(fn_src: str) -> tuple:
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "fn.js")
        with open(p, "w", encoding="utf-8") as f:
            f.write(fn_src)
        h = os.path.join(td, "h.js")
        with open(h, "w", encoding="utf-8") as f:
            f.write(NODE_HARNESS)
        try:
            r = subprocess.run(["node", h, p], capture_output=True,
                               text=True, timeout=60)
            return r.returncode, (r.stdout or "") + (r.stderr or "")
        except FileNotFoundError:
            return -1, "node 不可用"
        except subprocess.TimeoutExpired:
            return -1, "node 超时"


def main():
    js = open(APPJS, encoding="utf-8").read()
    html = open(HTML, encoding="utf-8").read()
    fn = slice_check_fn(js)

    print("== 结构检查 ==")
    chk("app.js 存在 checkAutoRenew 定义", "async function checkAutoRenew" in js)
    chk("app.js 把 checkAutoRenew 挂到初始化", "safe('autoRenew'" in js)
    chk("app.js 挂了 15s 定时", "checkAutoRenew().catch" in js)
    chk("index.html 有 autoRenewNote 元素", 'id="autoRenewNote"' in html)
    chk("autoRenewNote 与 biliMsg 是不同元素",
        'id="autoRenewNote"' in html and html.count('id="autoRenewNote"') == 1)

    print("== node vm 真跑 checkAutoRenew ==")
    if not fn:
        chk("能切出函数体", False)
    else:
        rc, out = run_node(fn)
        print(out.rstrip())
        chk("node vm 7 项全过", rc == 0, out[-400:])
        for line in out.splitlines():
            if "[FAIL]" in line:
                chk("node: " + line.strip(), False)

    print()
    if FAILS:
        print("[FAIL] %d 项：%s" % (len(FAILS), "; ".join(FAILS)))
        return 1
    print("[PASS] 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
