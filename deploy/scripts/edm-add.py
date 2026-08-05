#!/usr/bin/env python3
"""ai4s EDM 指纹入库（issue #29）：把商密文档转成 shingle SHA-256 指纹。

用法：cd deploy && python3 scripts/edm-add.py <文件或目录> [--name 文档名] [--remove]
- 归一化：lowercase + 连续空白折叠为单空格 + strip（与 shim 检测侧同法，改动须同步）
- shingle：50 字符滑窗、步长 1（全位置，对齐无关——粘贴起点任意）
- 指纹库 deploy/edm/fingerprints.json（gitignored）：只存哈希与文档名，不存原文
"""
import hashlib
import json
import os
import sys
import time

DEPLOY_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FP_PATH = os.path.join(DEPLOY_DIR, "edm", "fingerprints.json")
WINDOW = 50
STEP = 1


def normalize(text: str) -> str:
    return " ".join(text.lower().split())


def shingles(text: str):
    t = normalize(text)
    if len(t) < WINDOW:
        return [t] if t else []
    return [t[i:i + WINDOW] for i in range(0, len(t) - WINDOW + 1, STEP)]


def fp_of(shingle: str) -> str:
    return hashlib.sha256(shingle.encode()).hexdigest()


LINE_MIN = 12  # 行级指纹最小行长（防 "import os" 类超短行误伤）


def line_hashes(text: str):
    out = set()
    for line in text.splitlines():
        n = normalize(line)
        if len(n) >= LINE_MIN:
            out.add(fp_of(n))
    return out


def load_store() -> dict:
    try:
        return json.load(open(FP_PATH, encoding="utf-8"))
    except Exception:
        return {"version": 1, "docs": {}}


def save_store(store: dict):
    os.makedirs(os.path.dirname(FP_PATH), exist_ok=True)
    tmp = FP_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=1)
    os.replace(tmp, FP_PATH)  # 原子替换：shim 每请求重读，防截断读


def iter_files(path: str):
    if os.path.isfile(path):
        yield path
    else:
        for root, _, files in os.walk(path):
            for f in sorted(files):
                if not f.startswith("."):
                    yield os.path.join(root, f)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit("用法: edm-add.py <文件或目录> [--name 文档名] [--remove]")
    target = args[0]
    remove = "--remove" in sys.argv
    name = None
    if "--name" in sys.argv:
        i = sys.argv.index("--name")
        name = sys.argv[i + 1] if i + 1 < len(sys.argv) else None
    store = load_store()

    if remove:
        key = name or os.path.basename(target)
        if store["docs"].pop(key, None) is not None:
            save_store(store)
            print(f"已移除 {key} 的指纹")
        else:
            print(f"指纹库中无 {key}")
        return

    key = name or os.path.basename(target.rstrip("/"))
    hashes, lhashes = set(), set()
    for f in iter_files(target):
        try:
            text = open(f, encoding="utf-8", errors="ignore").read()
        except Exception as e:
            print(f"跳过不可读文件 {f}: {e}")
            continue
        for sh in shingles(text):
            hashes.add(fp_of(sh))
        lhashes |= line_hashes(text)
    doc = store["docs"].get(key) or {"shingles": [], "lines": []}
    if isinstance(doc, list):  # 兼容旧格式
        doc = {"shingles": doc, "lines": []}
    doc["shingles"] = sorted(set(doc["shingles"]) | hashes)
    doc["lines"] = sorted(set(doc["lines"]) | lhashes)
    store["docs"][key] = doc
    store["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    save_store(store)
    total = sum(len(v["shingles"]) + len(v["lines"]) for v in store["docs"].values())
    print(f"{key}: shingle 指纹 {len(doc['shingles'])}、行级指纹 {len(doc['lines'])}；全库 {total}")


if __name__ == "__main__":
    main()
