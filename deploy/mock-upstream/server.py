#!/usr/bin/env python3
"""ai4s 阶段 0 占位上游：OpenAI 兼容的最小 mock。

仅在拿不到 OAuth 订阅凭据时用于验证 agentgateway → axonhub → 上游 链路可达。
不校验 Authorization，对 /v1/chat/completions 返回固定内容的合法响应。
"""
import json
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._send_json(200, {"status": "ok"})
        elif self.path == "/v1/models":
            self._send_json(200, {
                "object": "list",
                "data": [{"id": "mock-gpt", "object": "model", "created": 0, "owned_by": "mock"}],
            })
        else:
            self._send_json(404, {"error": {"message": "not found", "type": "not_found"}})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            req = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send_json(400, {"error": {"message": "bad json", "type": "invalid_request_error"}})
            return

        if self.path in ("/v1/chat/completions", "/chat/completions"):
            model = req.get("model", "mock-gpt")
            # issue #20：回显用户消息原文——DLP 回归用，断言上游实际收到的内容（脱敏是否生效）
            echo = ""
            for m in reversed(req.get("messages") or []):
                if m.get("role") == "user":
                    c = m.get("content")
                    if isinstance(c, str):
                        echo = c
                    elif isinstance(c, list):
                        echo = " ".join(p.get("text", "") for p in c if isinstance(p, dict))
                    break
            content = "mock upstream reply: link OK | echo: " + echo[:2000]
            # issue #23 响应侧测试触发器：请求只含无害暗号，应答由 mock 拼出敏感内容
            if "模拟泄漏" in echo:
                content = ("mock upstream reply: leak-sim | 好的，信息如下："
                           "手机号 13812345678，身份证 11010119900307771X，"
                           "内部代号 凤凰计划，AWS key AKIAIOSFODNN7EXAMPLE")
            if req.get("stream"):
                # SSE 流式应答（issue #23 流式覆盖测试）：内容分三片推
                cid = f"chatcmpl-{uuid.uuid4().hex[:24]}"
                parts = [content[: len(content) // 3], content[len(content) // 3 : 2 * len(content) // 3], content[2 * len(content) // 3 :]]
                body = b""
                for p in parts:
                    chunk = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
                             "choices": [{"index": 0, "delta": {"content": p}, "finish_reason": None}]}
                    body += b"data: " + json.dumps(chunk, ensure_ascii=False).encode() + b"\n\n"
                end = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
                       "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
                body += b"data: " + json.dumps(end).encode() + b"\n\ndata: [DONE]\n\n"
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self._send_json(200, {
                "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 1, "completion_tokens": 6, "total_tokens": 7},
            })
        else:
            self._send_json(404, {"error": {"message": "not found", "type": "not_found"}})

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
