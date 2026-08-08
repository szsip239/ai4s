#!/usr/bin/env python3
"""EDM 指纹算法共享库（issue #34）：入库（admin_api POST /dlp-admin/edm/corpus）与检测（app.py）同法。
契约 dlp-webhook-shim.md L3 铁律：归一化/指纹算法改动须同步两侧——两侧统一收编进本模块即单一事实源。
以检测侧 shim/app.py 实现为准绳提取（原 deploy/scripts/edm-add.py 与之一致，收编前实测无 drift）。
纯 stdlib，无 I/O（算法库；文件读写归 admin_api/app 各自纪律）。
"""
import hashlib

WINDOW = 50  # char-shingle 滑窗字符数（对齐无关——粘贴起点任意）
STEP = 1
LINE_MIN = 12  # 行级指纹最小行长（防 "import os" 类超短行误伤）


def normalize(text: str) -> str:
    """lowercase + 连续空白折叠为单空格 + strip。"""
    return " ".join(text.lower().split())


def shingles(text: str) -> list:
    """归一化文本的 50 字符滑窗（步长 1，全位置）；短于窗口时整段为唯一 shingle（空文本除外）。"""
    t = normalize(text)
    if len(t) < WINDOW:
        return [t] if t else []
    return [t[i:i + WINDOW] for i in range(0, len(t) - WINDOW + 1, STEP)]


def fp_of(shingle: str) -> str:
    return hashlib.sha256(shingle.encode()).hexdigest()


def line_hashes(text: str) -> set:
    """行级通道（抗乱序）：逐行归一化，≥ LINE_MIN 字符的行取 SHA-256。"""
    out = set()
    for line in text.splitlines():
        n = normalize(line)
        if len(n) >= LINE_MIN:
            out.add(fp_of(n))
    return out


def doc_fingerprints(text: str) -> dict:
    """单文档全量指纹：{"shingles": sorted [...], "lines": sorted [...]}（admin 入库粒度）。"""
    return {
        "shingles": sorted({fp_of(s) for s in shingles(text)}),
        "lines": sorted(line_hashes(text)),
    }
