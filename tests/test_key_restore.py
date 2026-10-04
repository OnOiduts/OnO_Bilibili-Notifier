# -*- coding: utf-8 -*-
"""密钥被换掉后的自愈：靠 .machine_key.prev 把老密文救回来。

背景：发布包里一旦误带 .machine_key，用户"解压覆盖"就会把他自己那把密钥
换成打包机上的，config.yaml 里的密文随即全解不开——表现为机器人起不来、
提示"密钥可能被换过"。这个坑栽过两次（v1.31.5、v1.50.3）。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import secretbox


class _Env:
    """把密钥文件挪到临时目录，避免污染真实配置。"""

    def __init__(self):
        self.dir = tempfile.mkdtemp()
        self.old_key = secretbox.KEY_FILE
        self.old_prev = secretbox.PREV_FILE

    def __enter__(self):
        secretbox.KEY_FILE = os.path.join(self.dir, ".machine_key")
        secretbox.PREV_FILE = os.path.join(self.dir, ".machine_key.prev")
        secretbox._key_cache = None
        secretbox.DECRYPT_FAILED.clear()
        secretbox.RESTORED.clear()
        return self

    def __exit__(self, *a):
        secretbox.KEY_FILE = self.old_key
        secretbox.PREV_FILE = self.old_prev
        secretbox._key_cache = None
        secretbox.DECRYPT_FAILED.clear()
        secretbox.RESTORED.clear()

    def swap_key(self):
        """模拟"解压包带进来一份别人的密钥"。"""
        with open(secretbox.KEY_FILE, "wb") as f:
            f.write(b"X" * 32)
        secretbox._key_cache = None


def test_encrypt_decrypt_roundtrip():
    with _Env():
        c = secretbox.encrypt("hello-secret")
        assert c.startswith("enc1:")
        assert secretbox.decrypt(c, field="secret") == "hello-secret"


def test_swapped_key_without_backup_is_empty():
    """密钥被换且无备份 → 解出来是空，绝不能拿乱码去请求。"""
    with _Env():
        c = secretbox.encrypt("hello-secret")
        if os.path.exists(secretbox.PREV_FILE):
            os.remove(secretbox.PREV_FILE)
        with open(secretbox.KEY_FILE, "wb") as f:
            f.write(b"Y" * 32)
        secretbox._key_cache = None
        assert secretbox.decrypt(c, field="secret") == ""


def test_no_prev_backup_cannot_recover():
    """没有备份 → 老实返回空，并登记失败字段（不能瞎猜）。"""
    with _Env():
        c = secretbox.encrypt("hello-secret")
        # 换密钥，且删掉备份
        with open(secretbox.KEY_FILE, "wb") as f:
            f.write(b"Z" * 32)
        secretbox._key_cache = None
        if os.path.exists(secretbox.PREV_FILE):
            os.remove(secretbox.PREV_FILE)
        out = secretbox.decrypt(c, field="secret")
        assert out == "", "解不开就该返回空，不能拿乱码当凭据"
        assert "secret" in secretbox.DECRYPT_FAILED


def test_prev_backup_auto_restores():
    """有备份 → 自动救回，并把备份密钥恢复成当前密钥。"""
    with _Env():
        c = secretbox.encrypt("hello-secret")
        good = open(secretbox.KEY_FILE, "rb").read().strip()
        # 备份必须已经写下了
        assert os.path.exists(secretbox.PREV_FILE)
        assert open(secretbox.PREV_FILE, "rb").read().strip() == good

        # 坏密钥覆盖进去
        with open(secretbox.KEY_FILE, "wb") as f:
            f.write(b"W" * 32)
        secretbox._key_cache = None

        out = secretbox.decrypt(c, field="secret")
        assert out == "hello-secret", (
            "靠备份密钥应当能救回来（实际=%r；prev 存在=%s；prev 与好密钥一致=%s）"
            % (out, os.path.exists(secretbox.PREV_FILE),
               os.path.exists(secretbox.PREV_FILE)
               and open(secretbox.PREV_FILE, "rb").read().strip() == good))
        assert secretbox.RESTORED, "应当登记已自动恢复"
        # 并且把好密钥写回去了
        assert open(secretbox.KEY_FILE, "rb").read().strip() == good
        assert "secret" not in secretbox.DECRYPT_FAILED


def test_backup_not_overwritten_by_bad_key():
    """换上坏密钥后，备份不能被坏密钥污染。"""
    with _Env():
        secretbox.encrypt("hello-secret")
        good = open(secretbox.KEY_FILE, "rb").read().strip()
        with open(secretbox.KEY_FILE, "wb") as f:
            f.write(b"V" * 32)
        secretbox._key_cache = None
        _ = secretbox._load_key()          # 读坏密钥，会尝试备份
        assert open(secretbox.PREV_FILE, "rb").read().strip() == good, \
            "备份必须是最后一把好密钥，不能被坏密钥覆盖"


def test_hint_mentions_refill():
    with _Env():
        secretbox.DECRYPT_FAILED.add("secret")
        h = secretbox.broken_hint()
        assert "AppSecret" in h and "重新填写" in h


def test_pack_script_excludes_machine_key():
    """打包脚本必须点名排除密钥文件——这是事故的直接防线。"""
    for cand in ("/data/workspace/_pack.py",
                 os.path.join(os.path.dirname(os.path.dirname(
                     os.path.abspath(__file__))), "..", "_pack.py")):
        if os.path.exists(cand):
            src = open(cand, encoding="utf-8").read()
            assert ".machine_key" in src, "打包脚本必须显式排除 .machine_key"
            assert "keys=" in src, "打包后必须校验包内不含密钥文件"
            return


if __name__ == "__main__":
    import traceback
    _fns = [(n, o) for n, o in sorted(globals().items())
            if n.startswith("test_") and callable(o)]
    _p = _f = 0
    for _n, _o in _fns:
        try:
            _o()
            _p += 1
            print("PASS", _n)
        except Exception:
            _f += 1
            print("FAIL", _n)
            traceback.print_exc()
    print(f"--- {_p}/{_p + _f} passed")
    raise SystemExit(1 if _f else 0)
