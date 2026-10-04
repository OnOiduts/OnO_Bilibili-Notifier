# 代码由 AI 生成：腾讯元宝「Hy4 preview」；作者整理、测试与维护。
"""QQ 官方 OpenAPI 直连封装（不依赖 botpy 的消息对象，方便完全控制 Markdown / 按钮）。

机器人仍需通过 botpy 保持 WebSocket 在线，本模块只负责「发消息」：
  正式环境 https://api.sgroup.qq.com
  沙箱环境 https://sandbox.api.sgroup.qq.com
"""
import time
from typing import Optional

import aiohttp

from security import redact

TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
BASE = "https://api.sgroup.qq.com"
BASE_SANDBOX = "https://sandbox.api.sgroup.qq.com"


class QQError(Exception):
    pass


class QQClient:
    def __init__(self, appid: str, secret: str, sandbox: bool = False, timeout: int = 15):
        self.appid = str(appid)
        self.secret = str(secret)
        self.base = BASE_SANDBOX if sandbox else BASE
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: Optional[aiohttp.ClientSession] = None
        self._token = ""
        self._token_expire = 0.0

    async def _sess(self) -> aiohttp.ClientSession:
        # ⚠️ 会话是缓存在实例上的，但 asyncio.run() 每次都会新建并关闭
        # 事件循环。会话一旦绑定到已关闭的循环，再用就报
        # "Event loop is closed"（表现为"第一个群成功，后面全失败"）。
        # 所以取会话前先确认它还能用，不行就丢掉重建。
        if self._session is not None and not self._session.closed:
            if self._session_dead():
                try:
                    # 别在已关闭的循环里 await close，会再抛一次
                    self._session = None
                except Exception:
                    self._session = None
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    def _session_dead(self) -> bool:
        """这个会话是不是绑在已经关闭的事件循环上了？"""
        try:
            loop = getattr(self._session, "loop", None) or getattr(
                self._session.connector, "_loop", None)
            if loop is None:
                return False
            return bool(loop.is_closed())
        except Exception:
            # 查不出来就当还活着，宁可多试一次也别无谓重建
            return False

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def get_token(self) -> str:
        """access_token 有效期一般 7200s，提前 5 分钟刷新。"""
        if self._token and time.time() < self._token_expire - 300:
            return self._token
        s = await self._sess()
        payload = {"appId": self.appid, "clientSecret": self.secret}
        async with s.post(TOKEN_URL, json=payload) as resp:
            data = await resp.json(content_type=None)
        if resp.status != 200 or "access_token" not in data:
            # 不要把响应原文里的任何凭据片段抛出去
            raise QQError(f"获取 access_token 失败: {redact(str(data))[:200]}")
        self._token = data["access_token"]
        self._token_expire = time.time() + int(data.get("expires_in", 7200))
        return self._token

    async def get(self, path: str) -> dict:
        token = await self.get_token()
        s = await self._sess()
        url = f"{self.base}{path}"
        headers = {
            "Authorization": f"QQBot {token}",
            "X-Union-Appid": self.appid,
        }
        async with s.get(url, headers=headers) as resp:
            text = await resp.text()
        try:
            import json as _json
            data = _json.loads(text) if text else {}
        except Exception:
            data = {}
        if resp.status >= 400:
            raise QQError(f"HTTP {resp.status}: {redact(text)[:300]}")
        return data

    async def put(self, path: str, body: dict) -> dict:
        token = await self.get_token()
        s = await self._sess()
        headers = {
            "Authorization": f"QQBot {token}",
            "X-Union-Appid": self.appid,
        }
        async with s.put(f"{self.base}{path}", headers=headers, json=body) as r:
            text = await r.text()
        try:
            import json as _json
            data = _json.loads(text) if text else {}
        except Exception:
            data = {}
        if r.status >= 400:
            raise QQError(f"HTTP {r.status}: {redact(text)[:300]}")
        return data

    async def delete(self, path: str) -> dict:
        token = await self.get_token()
        s = await self._sess()
        headers = {
            "Authorization": f"QQBot {token}",
            "X-Union-Appid": self.appid,
        }
        async with s.delete(f"{self.base}{path}", headers=headers) as r:
            text = await r.text()
        if r.status >= 400:
            raise QQError(f"HTTP {r.status}: {redact(text)[:300]}")
        return {}

    async def get_group_info(self, group_openid: str) -> dict:
        """获取群基本信息（官方 GET /v2/groups/{group_openid}/info）。

        能拿到：group_name（群名称）、group_member_num（成员数）、
        group_finger_memo（群简介）、group_class_text（群分类）、group_tags。

        ⚠️ 该接口为「白名单」接口。未开通时官方返回 err_code 11253
        （应用无接口访问权限），此时调用方应回退到手动备注，而不是报错。
        """
        try:
            data = await self.get(f"/v2/groups/{group_openid}/info")
        except QQError as e:
            return {"ok": False, "msg": str(e)[:200], "code": None}
        if not isinstance(data, dict):
            return {"ok": False, "msg": "返回格式异常"}
        # 官方失败响应带 err_code
        code = data.get("err_code") or data.get("code")
        if code:
            return {"ok": False, "code": int(code),
                    "msg": str(data.get("message") or data.get("err_msg") or "")[:200]}
        name = (data.get("group_name") or "").strip()
        if not name:
            return {"ok": False, "msg": "接口没返回群名"}
        return {
            "ok": True,
            "name": name,
            "member_num": data.get("group_member_num") or 0,
            "memo": (data.get("group_finger_memo") or "").strip(),
            "class_text": (data.get("group_class_text") or "").strip(),
            "tags": data.get("group_tags") or [],
        }

    # ⚠️ get_member_role / get_member_role_detail 已于 v2.2.0 删除：
    #    官方「获取群成员」接口是内邀白名单制，普通开发者拿不到，
    #    member_role 永远取不到值，谁都用不了（包括群主本人）。

    async def post(self, path: str, body: dict) -> dict:
        token = await self.get_token()
        s = await self._sess()
        url = f"{self.base}{path}"
        headers = {
            "Authorization": f"QQBot {token}",
            "Content-Type": "application/json",
            "X-Union-Appid": self.appid,
        }
        async with s.post(url, json=body, headers=headers) as resp:
            text = await resp.text()
        try:
            import json as _json
            data = _json.loads(text) if text else {}
        except Exception:
            data = {}
        if resp.status >= 400:
            raise QQError(f"HTTP {resp.status}: {redact(text)[:300]}")
        return data

    # ---------- 群消息 ----------
    async def send_text(self, group_openid: str, content: str,
                        msg_id: str = None, event_id: str = None) -> dict:
        """纯文本。内嵌格式（如 <@everyone>）只在 content 里生效。"""
        body = {"msg_type": 0, "content": content}
        if msg_id:
            body["msg_id"] = msg_id
        if event_id:
            body["event_id"] = event_id
        return await self.post(f"/v2/groups/{group_openid}/messages", body)

    async def send_markdown(self, group_openid: str, markdown: str,
                            keyboard: dict = None, msg_id: str = None,
                            event_id: str = None) -> dict:
        """Markdown 消息（msg_type=2），可挂载按钮。传 markdown 时 content 必须为空。"""
        body = {"msg_type": 2, "markdown": {"content": markdown}}
        if keyboard:
            body["keyboard"] = keyboard
        if msg_id:
            body["msg_id"] = msg_id
        if event_id:
            body["event_id"] = event_id
        return await self.post(f"/v2/groups/{group_openid}/messages", body)


    async def upload_group_file(self, group_openid: str, file_type: int = 1,
                                url: str = None, file_data: str = None,
                                srv_send_msg: bool = False) -> dict:
        """上传群聊富媒体文件，返回 file_info。

        file_type: 1=图片(png/jpg) 2=视频 3=语音 4=文件
        url       平台自己去下载转存（不走客户端，不受消息 URL 报备限制）
        file_data base64 编码的文件内容（本地直传，无需公网）
        srv_send_msg=False 只拿 file_info，不直接发（不额外占主动频次）

        返回体里有 file_info / file_uuid / ttl(秒)。
        """
        body = {"file_type": int(file_type), "srv_send_msg": bool(srv_send_msg)}
        if url:
            body["url"] = url
        if file_data:
            body["file_data"] = file_data
        return await self.post(
            f"/v2/groups/{group_openid}/files", body)

    async def send_media(self, group_openid: str, file_info: str,
                         msg_id: str = None, event_id: str = None) -> dict:
        """发送富媒体消息（msg_type=7）。file_info 原样透传，不要解析。"""
        body = {"msg_type": 7, "media": {"file_info": file_info}}
        if msg_id:
            body["msg_id"] = msg_id
        if event_id:
            body["event_id"] = event_id
        return await self.post(f"/v2/groups/{group_openid}/messages", body)


    # ---------- 指令面板（官方 /v2/panels） ----------
    # 只做 group 场景 —— 用户只把机器人用在群里，不做频道。
    async def list_panels(self) -> dict:
        """查询指令面板列表。"""
        return await self.get("/v2/panels")

    async def create_panel(self, items: list, remark: str = "",
                           group_openids: list = None,
                           target_type: str = "all") -> dict:
        """创建（或重建）群聊指令面板。

        items: [{"type":"command","name":"订阅","desc":"订阅 UP 主"}, ...]
          - type 只允许 command / link
          - name 最多 14 字符，desc 最多 30 字符，最多 20 个
          - type=link 时额外给 link 字段
        一个机器人最多 20 个面板；先查列表删掉旧的再建，避免堆积。
        """
        body = {
            "scope": "group",
            "target_type": target_type,
            "panel": {"items": items, "remark": remark or "OnOB站通知订阅工具"},
        }
        if target_type == "specific" and group_openids:
            body["group_openids"] = list(group_openids)[:20]
        return await self.post("/v2/panels", body)

    async def update_panel(self, panel_id: str, items: list,
                           remark: str = "", version: int = 0) -> dict:
        """修改指令面板。version 需传当前版本号。"""
        body = {"panel": {"items": items, "remark": remark or "OnOB站通知订阅工具"}}
        if version:
            body["panel"]["version"] = int(version)
        return await self.put(f"/v2/panels/{panel_id}", body)

    async def delete_panel(self, panel_id: str) -> bool:
        try:
            await self.delete(f"/v2/panels/{panel_id}")
            return True
        except Exception:
            return False


def build_keyboard(buttons: list) -> dict:
    """构造跳转按钮。buttons: [(显示的文字, 跳转链接, 点击后的文字), ...]

    按官方「消息按钮」规范：
      action.type = 0        → 跳转按钮，data 填 http 链接
      permission.type = 2    → 所有人可点击
      render_data.style = 1  → 按钮样式
      visited_label          → 点击后按钮上显示的文字
    自定义按钮最多 5 行、每行最多 5 个，这里一行放一个，视觉最清晰。

    注意：官方要求消息里的链接（网页/图片）需要提前在
    「开发设置 → 消息 URL 配置」报备，否则可能展示异常。
    """
    rows = []
    for i, item in enumerate(buttons):
        label, url = item[0], item[1]
        visited = item[2] if len(item) > 2 else None
        if not url:
            continue
        render_data = {"label": label[:10], "style": 1}
        if visited:
            render_data["visited_label"] = visited[:10]
        rows.append({
            "buttons": [{
                "id": f"btn_{i + 1}",
                "render_data": render_data,
                "action": {
                    "type": 0,
                    "data": url,
                    "permission": {"type": 2},
                    "unsupport_tips": "请升级 QQ 版本查看",
                },
            }]
        })
    return {"content": {"rows": rows}} if rows else None
