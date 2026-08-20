"""飞书群机器人签名共享实现（issue #70 清理批）：app.py 与 alert_poller.py 原各有一份
逐字节相同的 feishu_sign，收敛为单份，两处 import 引用（edm_lib 式小工具模块）。"""
import base64
import hashlib
import hmac


def feishu_sign(ts: str, secret: str) -> str:
    digest = hmac.new(f"{ts}\n{secret}".encode(), b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode()
