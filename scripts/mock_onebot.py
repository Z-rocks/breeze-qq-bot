"""极简 OneBot 11 假服务器，用来在没有 QQ 账号的情况下验证 qq_mcp 链路。

    python scripts/mock_onebot.py 3999

然后：
    ONEBOT_HTTP=http://127.0.0.1:3999 python qq_mcp/server.py   （喂 MCP 请求即可）

所有收到的 action 都会打印出来，方便确认 record 消息段的格式。
"""

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

LOG = []


class Handler(BaseHTTPRequestHandler):
    def _send(self, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode("utf-8") if n else "{}"
        action = self.path.lstrip("/")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"_raw": raw}

        # 语音段的 file 字段可能是超长 base64，截断显示
        shown = json.dumps(payload, ensure_ascii=False)
        if len(shown) > 300:
            shown = shown[:300] + f"... ({len(shown)} chars)"
        print(f"[mock] {action} <- {shown}", flush=True)

        if action == "get_login_info":
            return self._send({"status": "ok", "retcode": 0, "data": {"user_id": 10001, "nickname": "mock-bot"}})
        if action == "get_group_list":
            return self._send({"status": "ok", "retcode": 0, "data": [{"group_id": 123456, "group_name": "测试群"}]})
        if action == "get_friend_list":
            return self._send({"status": "ok", "retcode": 0, "data": [{"user_id": 10002, "nickname": "测试好友"}]})
        if action in ("send_group_msg", "send_private_msg"):
            return self._send({"status": "ok", "retcode": 0, "data": {"message_id": 9001}})
        if action.endswith("_msg_history"):
            return self._send(
                {
                    "status": "ok",
                    "retcode": 0,
                    "data": {
                        "messages": [
                            {"sender": {"nickname": "小明"}, "message": [{"type": "text", "data": {"text": "在吗"}}]},
                            {"sender": {"nickname": "mock-bot"}, "message": [{"type": "record", "data": {}}]},
                        ]
                    },
                }
            )
        self._send({"status": "failed", "retcode": 1404, "data": None})

    def log_message(self, *_args):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 3999
    print(f"[mock] listening on http://127.0.0.1:{port}", flush=True)
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
