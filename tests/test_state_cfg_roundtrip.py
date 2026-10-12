# -*- coding: utf-8 -*-
"""面板能改的字段，/api/state 必须回读 —— 否则就是「假保存」。

事故复盘（v2.3.4「启动后首轮静默」）：
  新增这个开关时，只写了 /api/config 的**保存**分支，漏了 /api/state 的
  **回读**。后果链条很长且每一环都「看起来正常」：

    1. 点开关 → 前端自动保存 → POST /api/config 返回 200「已保存。」✅
    2. config.yaml 里真的写进了 startup_grace_rounds: 0          ✅
    3. 前端 loadState() 拉 /api/state，返回的 config **没有这个键**
    4. fillCfg(): `c.startup_grace_rounds` → undefined
       → Number(undefined) → NaN → `isNaN(g)` 兜成默认值 1
    5. 开关按 `gv > 0` 重新 checked=true → **关掉又弹回开**

  用户看到的就是「点一下有动画、有 toast，但一刷新又回到原参数」。
  服务端日志全是 200，config.yaml 也对，唯独前端永远拿不到真实值 ——
  这种 bug 靠看日志和配置文件都查不出来，只有前后端一起验才现形。

两条防线：
  ① 静态扫描：保存分支写过的键，state 必须回（白名单里的除外）
  ② 端到端：真起 Flask，POST 之后 GET 必须读回同一个值
"""
import os
import re
import sys
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

WEBUI = os.path.join(ROOT, "webui.py")

# 有意不放进 config 的键（都已在别处返回，前端拿得到，不是漏项）：
#   secret           → 泄密，前端只看 has_secret 布尔位
#   templates        → 在 /api/state **顶层**单独返回（config 外面）
#   _cfg_migrated_v141 → 内部迁移标记，面板不需要
INTENTIONAL_ABSENT = {"secret", "templates", "_cfg_migrated_v141"}


def _state_cfg_keys():
    """/api/state 里 config 字典的键（靠缩进识别，只看那一段）。"""
    src = open(WEBUI, encoding="utf-8").read()
    lines = src.splitlines()
    start = next(i for i, ln in enumerate(lines)
                 if ln.strip().startswith('"config": {'))
    keys = set()
    for ln in lines[start:start + 80]:
        m = re.match(r'\s*"([a-z_][a-z0-9_]*)":', ln)
        if m:
            keys.add(m.group(1))
        if ln.strip() == "}," and keys:
            break
    return keys


def _save_cfg_keys():
    """/api/config 里写进 cfg 的键。"""
    src = open(WEBUI, encoding="utf-8").read()
    lines = src.splitlines()
    start = next(i for i, ln in enumerate(lines)
                 if ln.startswith('@app.route("/api/config"'))
    end = next(i for i in range(start + 1, len(lines))
               if lines[i].startswith("@app.route"))
    return set(re.findall(r'cfg\[\s*"([a-z_][a-z0-9_]*)"\s*\]',
                          "\n".join(lines[start:end])))


class TestStateCfgKeys(unittest.TestCase):
    """① 静态：保存能写的，state 必须能回。"""

    def test_no_key_saved_but_never_returned(self):
        missing = (_save_cfg_keys() - _state_cfg_keys()) - INTENTIONAL_ABSENT
        self.assertEqual(set(), missing,
                         "这些字段能保存但 /api/state 不返回，前端必然回弹："
                         + ", ".join(sorted(missing)))

    def test_scan_actually_found_keys(self):
        """防止正则失效导致『空集 - 空集 = 空』这种假通过。"""
        self.assertGreater(len(_save_cfg_keys()), 10)
        self.assertGreater(len(_state_cfg_keys()), 10)
        self.assertIn("startup_grace_rounds", _state_cfg_keys())


class TestGraceRoundtrip(unittest.TestCase):
    """② 端到端：真起 Flask，存进去必须能原样读出来。"""

    @classmethod
    def setUpClass(cls):
        cls._dir = tempfile.mkdtemp()
        os.environ["ONOBN_DATA_DIR"] = cls._dir
        import webui
        app = webui.app
        app.config["TESTING"] = True
        cls.c = app.test_client()
        cls.H = {"HTTP_HOST": "127.0.0.1",
                 "HTTP_X_REQUESTED_WITH": "bili-notify"}

    def _read(self):
        r = self.c.get("/api/state",
                       environ_base={"HTTP_HOST": "127.0.0.1"})
        cfg = (r.get_json() or {}).get("config") or {}
        return cfg.get("startup_grace_rounds")

    def _save(self, v):
        r = self.c.post("/api/config", environ_base=self.H,
                        json={"startup_grace_rounds": v})
        self.assertEqual(200, r.status_code)
        return r

    def test_default_before_any_save(self):
        """没存过时必须是 1（默认开），不能是 None ——
        None 进前端就是 NaN → 兜 1，看着对其实是蒙的。"""
        self.assertEqual(1, self._read())

    def test_zero_roundtrips(self):
        """关掉开关 = 0，必须读回 0（这就是回弹那条链）。"""
        self._save(0)
        self.assertEqual(0, self._read())

    def test_each_value_roundtrips(self):
        for v in (1, 2, 3, 4, 5):
            self._save(v)
            self.assertEqual(v, self._read())

    def test_turn_off_then_on(self):
        self._save(0)
        self.assertEqual(0, self._read())
        self._save(3)
        self.assertEqual(3, self._read())

    def test_out_of_range_clamped(self):
        """超范围要钳住，别把脏值透给前端。"""
        self._save(99)
        self.assertLessEqual(self._read(), 5)
        self._save(-5)
        self.assertGreaterEqual(self._read(), 0)


if __name__ == "__main__":
    unittest.main()
