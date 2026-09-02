"""飞书群机器人签名共享实现（issue #70 清理批）：app.py 与 alert_poller.py 原各有一份
逐字节相同的 feishu_sign，收敛为单份，两处 import 引用（edm_lib 式小工具模块）。"""
import base64
import datetime
import hashlib
import hmac
import time


_CST = datetime.timezone(datetime.timedelta(hours=8))


def fmt_cst(ts=None):
    """飞书卡面时间统一东八："2026-09-02 21:36:45 UTC+8"。"""
    dt = datetime.datetime.fromtimestamp(ts if ts is not None else time.time(), _CST)
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC+8")


def day_key_cst(ts=None):
    """东八日界 yyyymmdd（审批/key 命名幂等键）。"""
    dt = datetime.datetime.fromtimestamp(ts if ts is not None else time.time(), _CST)
    return dt.strftime("%Y%m%d")


def iso_to_cst(s):
    """存储用 ISO Z（如 createdAt）→ 卡面东八显示；空值/非 ISO 原样返回（fail-open）。"""
    if not isinstance(s, str) or not s:
        return s or ""
    try:
        dt = datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(_CST)
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC+8")
    except Exception:
        return s


def feishu_sign(ts: str, secret: str) -> str:
    digest = hmac.new(f"{ts}\n{secret}".encode(), b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode()
