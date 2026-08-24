#!/usr/bin/env python3
"""shadow 判定观测闭环（issue #92）：语义 judge / 注入 PG 的 shadow 判定持久化与统计。

动机：两侧判定原本只 print 到容器 stdout（app.py [semantic.shadow]/[injection.shadow]），
无持久化、无聚合、无告警——契约「shadow 观察期误报可控再议转级」的门槛无数据通道可证。
本模块提供最小闭环底座：判定逐条落 JSONL（不落原文——judge 只存命中实体数，不存实体
字符串），tail/stats 供 admin 查询出口（/dlp-admin/shadow-verdicts）与 alert_poller
可用率告警消费。
issue #103：PG 高分阻断试点的阻断事件同槽落条（blocked=True + block_threshold/model
脱敏字段——告警卡片只需这些，绝无原文/key），alert_poller 新增阻断巡检 tail 消费。

纪律：
- record 永不抛（检测路径纪律，与 pg_guard fail-open 同语义）——持久化失败只 print；
- 仅标准库；不 import alert_poller（其需消费本模块 stats，反向会成环）——STATE_PATH
  默认值按同一 env 名自取，目录约定与 key-requests.json 一致；
- 超 MAX_BYTES 截尾保留新的一半（tmp+os.replace 原子换文件，与 admin_api 原子写同原理）。
"""
import json
import os
import time

# 路径解析每次调用走 env（测试注入友好）：SHADOW_LOG_PATH 显式指定 >
# STATE_PATH 同目录 shadow-verdicts.jsonl（与 key-requests.json 目录约定一致）
def _default_path() -> str:
    p = os.environ.get("SHADOW_LOG_PATH", "")
    if p:
        return p
    d = os.path.dirname(os.environ.get("STATE_PATH", "/state/alert-state.json"))
    return os.path.join(d or ".", "shadow-verdicts.jsonl")


# 截尾阈值：1MB 约 5k 条判定，观察期统计（窗口 20/50）远在覆盖内。每次调用走 env（测试注入友好）
def _max_bytes() -> int:
    return int(os.environ.get("SHADOW_LOG_MAX_BYTES", "1048576"))


def record(layer: str, hit=None, score=None, confidence=None, latency_ms=None,
           error=None, entities=None, path=None, blocked=None, block_threshold=None, model=None):
    """追加一条 shadow 判定。entities 只存命中数（不存字符串——不落原文）；
    error 非 None 表示该次判定不可用（hit/score/confidence 应为 None）。永不抛。
    issue #103：blocked=True 表示该次判定触发了 451 阻断（PG 高分试点；语义层永不阻断），
    block_threshold/model 随阻断条落盘——告警卡片脱敏字段（阈值/请求模型名，无原文无 key）。
    三个阻断字段只在非 None 时写入（未阻断条保持旧形状逐字节一致——jsonl 体积纪律，
    消费方一律 rec.get()）。"""
    rec = {
        "ts": time.time(),
        "layer": layer,
        "hit": hit,
        "score": score,
        "confidence": confidence,
        "latency_ms": latency_ms,
        "error": error,
        "entities": entities,
    }
    for k, v in (("blocked", blocked), ("block_threshold", block_threshold), ("model", model)):
        if v is not None:
            rec[k] = v
    p = path or _default_path()
    try:
        d = os.path.dirname(p)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        _trim_if_oversize(p)
    except Exception as e:
        print(f"[shadow] 持久化失败（不影响检测）: {type(e).__name__}", flush=True)


def _trim_if_oversize(path: str):
    """超 MAX_BYTES 截尾留新的一半（tmp + os.replace 原子换文件，读者只见完整旧/新版）。"""
    try:
        if os.path.getsize(path) <= _max_bytes():
            return
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        keep = lines[len(lines) // 2:]
        tmp = f"{path}.tmp.{os.getpid()}"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.writelines(keep)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        print(f"[shadow] 日志截尾 {len(lines)} -> {len(keep)} 行", flush=True)
    except Exception as e:
        print(f"[shadow] 截尾失败: {type(e).__name__}", flush=True)


def tail(n: int, layer: str = None, path=None) -> list:
    """读最近 n 条（新到旧）；layer 非空按层过滤。坏行跳过。文件缺失返回 []。"""
    p = path or _default_path()
    try:
        with open(p, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return []
    except Exception as e:
        print(f"[shadow] 读取失败: {type(e).__name__}", flush=True)
        return []
    out = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if layer and rec.get("layer") != layer:
            continue
        out.append(rec)
        if len(out) >= n:
            break
    return out


def stats(layer: str, window: int = 50, path=None) -> dict:
    """最近 window 条该层判定的聚合：total/errors/error_rate/hits/avg_latency_ms/last_ts。
    供 alert_poller 可用率告警与 admin 查询出口消费。无记录时数值归零、时间为 None。"""
    recs = tail(window, layer=layer, path=path)
    errors = sum(1 for r in recs if r.get("error"))
    hits = sum(1 for r in recs if r.get("hit"))
    lats = [r["latency_ms"] for r in recs
            if r.get("latency_ms") is not None and not r.get("error")]
    return {
        "total": len(recs),
        "errors": errors,
        "error_rate": (errors / len(recs)) if recs else 0.0,
        "hits": hits,
        "avg_latency_ms": int(sum(lats) / len(lats)) if lats else None,
        "last_ts": recs[0]["ts"] if recs else None,
    }


if __name__ == "__main__":  # CLI 查询出口（issue #92 AC：CLI 或页面其一）：python3 shadow_log.py [n]
    import sys
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    for rec in tail(n):
        print(json.dumps(rec, ensure_ascii=False))
