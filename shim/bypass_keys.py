#!/usr/bin/env python3
"""Key 级 DLP 绕行名单（issue #129）。

用途：可信场景（自动化管道、合法处理 key 样文本的工具）的 API Key 可绕开 DLP 检测。
- scope="all"    → shim 侧全层跳过；网关 L1 regex 须走 /bv1 专用入口（静态配置无法按 key 跳过）
- scope="layers" → 只跳过 layers 列出的 shim 侧层（l1 归一化变体/l2 词表 PII/edm/rules/pg/judge/response）

安全纪律：
- 文件与接口返回值只存 token 的 SHA-256 哈希（id 字段），绝不落明文；key 高熵随机，不加盐。
- lookup 只认 enabled=true；鉴权失败/头缺失 = 不绕行（fail-closed）。
- 每次绕行命中由调用方（app.py）写 shadow_log 审计条 + stdout 日志行（不落原文不记 token）。

仅标准库；不 import admin_api（其消费本模块，反向成环）——原子写 tmp+os.replace 与
admin_api.write_json_atomic 同原理，目录约定与 settings.json 一致（/dlp）。
并发纪律（审计B 中3 修复，2026-09-03）：add/update/remove 的「读-改-写」整体由模块级
锁互斥（同 key_requests.py 模式）——ThreadingHTTPServer 下裸跑会撞固定 tmp 名
（交错写/丢更新/os.replace 抛 FileNotFoundError）。
"""
import hashlib
import json
import os
import threading
import time

_RMW_LOCK = threading.Lock()  # add/update/remove 读改写互斥（lookup 只读不进锁）

# 可被 shim 侧绕行的层词表（网关 L1 regex 不在内——只能经 /bv1 入口整体绕）
BYPASSABLE_LAYERS = ("l1", "l2", "edm", "rules", "pg", "judge", "response")

SCOPES = ("all", "layers")


def _default_path() -> str:
    return os.environ.get("BYPASS_KEYS_PATH", "/dlp/bypass-keys.json")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def load(path: str | None = None) -> dict:
    p = path or _default_path()
    if not os.path.exists(p):
        return {"version": 1, "keys": []}
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {"version": 1, "keys": []}
    if not isinstance(data, dict) or not isinstance(data.get("keys"), list):
        return {"version": 1, "keys": []}
    return data


def _save(data: dict, path: str | None) -> None:
    p = path or _default_path()
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, p)


def _validate(label, scope, layers) -> str | None:
    if not isinstance(label, str) or not label.strip():
        return "label 不能为空"
    if len(label) > 64:
        return "label 超长（≤64）"
    if scope not in SCOPES:
        return f"scope 必须是 {'/'.join(SCOPES)}"
    if scope == "layers":
        if not isinstance(layers, list) or not layers:
            return "scope=layers 时 layers 至少选一层"
        unknown = [x for x in layers if x not in BYPASSABLE_LAYERS]
        if unknown:
            return f"未知层: {', '.join(unknown)}（可选 {'/'.join(BYPASSABLE_LAYERS)}）"
    return None


def add(token: str, label: str, scope: str, layers, added_by: str, path: str | None = None) -> dict:
    """登记绕行 key：只存 SHA-256(token)，明文用过即弃。重复 token 报 ValueError。"""
    if not isinstance(token, str) or not token:
        raise ValueError("token 不能为空")
    err = _validate(label, scope, layers)
    if err:
        raise ValueError(err)
    with _RMW_LOCK:
        data = load(path)
        kid = _hash(token)
        if any(k.get("id") == kid for k in data["keys"]):
            raise ValueError("该 Key 已在绕行名单中")
        entry = {
            "id": kid,
            "label": label.strip(),
            "scope": scope,
            "layers": sorted(set(layers)) if scope == "layers" else [],
            "enabled": True,
            "added_by": added_by or "",
            "added_at": time.time(),
        }
        data["keys"].append(entry)
        _save(data, path)
        return dict(entry)


def lookup(token: str, path: str | None = None) -> dict | None:
    """按 token 查启用中的绕行条；未登记/已停用 → None。空 token → None（fail-closed）。"""
    if not token:
        return None
    kid = _hash(token)
    for k in load(path)["keys"]:
        if k.get("id") == kid and k.get("enabled"):
            return dict(k)
    return None


def covers(entry: dict, layer: str) -> bool:
    """该绕行条是否覆盖指定层：scope=all 全覆盖；scope=layers 看名单。"""
    if entry.get("scope") == "all":
        return True
    return layer in (entry.get("layers") or [])


def update(kid: str, fields: dict, path: str | None = None) -> dict:
    """改 label/scope/layers/enabled（fields 只取这四键，缺席=保持；{}=校验性 no-op 返回现状，
    单次落盘）。未知 id 报 KeyError，非法字段报 ValueError。"""
    with _RMW_LOCK:
        data = load(path)
        for k in data["keys"]:
            if k.get("id") == kid:
                label = fields.get("label", k["label"])
                scope = fields.get("scope", k["scope"])
                layers = fields.get("layers", k["layers"])
                err = _validate(label, scope, layers)
                if err:
                    raise ValueError(err)
                k["label"] = label.strip()
                k["scope"] = scope
                k["layers"] = sorted(set(layers)) if scope == "layers" else []
                if "enabled" in fields:
                    k["enabled"] = bool(fields["enabled"])
                _save(data, path)
                return dict(k)
    raise KeyError(kid)


def set_enabled(kid: str, enabled: bool, path: str | None = None) -> dict:
    return update(kid, {"enabled": enabled}, path)


def remove(kid: str, path: str | None = None) -> None:
    with _RMW_LOCK:
        data = load(path)
        data["keys"] = [k for k in data["keys"] if k.get("id") != kid]
        _save(data, path)
