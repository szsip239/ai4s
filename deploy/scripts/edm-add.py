#!/usr/bin/env python3
"""ai4s EDM 指纹入库 CLI（issue #34 起为 admin API 薄壳；原直写文件逻辑已收编进 shim admin 平面）。

用法：cd deploy && python3 scripts/edm-add.py <文件或目录> [--name 文档名] [--remove]
- 地址：env DLP_ADMIN_URL（默认 http://localhost:18080）
- 凭据：env DLP_ADMIN_TOKEN，缺省读 deploy/.local/admin-jwt
- 单一写入路径：指纹算法/原子写纪律在 shim admin_api + edm_lib（入库/检测同法，契约铁律）
- name 校验 [A-Za-z0-9_.-]{1,64}（API 侧强制；中文文件名请用 --name 指定 ASCII 名）
- 同名重复入库会被 400 拒绝（原直写时代为指纹并集累加）：更新文档请 --remove 后重新入库
- 目录场景：多文件内容聚合为单文档一次入库；与历史"逐文件指纹并集"相比，跨文件拼接边界
  会多出永不命中的 shingle（原指纹全保留，漏检为零；行级通道不受影响）
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

DEPLOY_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADMIN_URL = os.environ.get("DLP_ADMIN_URL", "http://localhost:18080").rstrip("/")


def _token() -> str:
    t = os.environ.get("DLP_ADMIN_TOKEN")
    if t:
        return t.strip()
    p = os.path.join(DEPLOY_DIR, ".local", "admin-jwt")
    try:
        return open(p, encoding="utf-8").read().strip()
    except OSError:
        sys.exit(f"无凭据：env DLP_ADMIN_TOKEN 未设且 {p} 不可读")


def _api(method: str, path: str, payload=None):
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    req = urllib.request.Request(ADMIN_URL + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {_token()}")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}
    except urllib.error.URLError as e:
        sys.exit(f"admin API 不可达 {ADMIN_URL}: {e}")


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

    if remove:
        key = name or os.path.basename(target)
        status, body = _api("DELETE", "/dlp-admin/edm/corpus/" + urllib.parse.quote(key, safe=""))
        if status == 200:
            print(f"已移除 {key} 的指纹")
        elif status == 404:
            print(f"指纹库中无 {key}")
        else:
            sys.exit(f"删除失败 ({status}): {body.get('error', body)}")
        return

    key = name or os.path.basename(target.rstrip("/"))
    texts = []
    for f in iter_files(target):
        try:
            texts.append(open(f, encoding="utf-8", errors="ignore").read())
        except OSError as e:
            print(f"跳过不可读文件 {f}: {e}")
    if not texts:
        sys.exit("无可读文件")
    status, body = _api("POST", "/dlp-admin/edm/corpus", {"name": key, "text": "\n".join(texts)})
    if status != 200:
        sys.exit(f"入库失败 ({status}): {body.get('error', body)}")
    _, docs = _api("GET", "/dlp-admin/edm/corpus")  # 全库指纹总数（对齐原输出格式）
    total = sum(d["shingle_count"] + d["line_count"] for d in docs) if isinstance(docs, list) else 0
    print(f"{key}: shingle 指纹 {body['shingle_count']}、行级指纹 {body['line_count']}；全库 {total}")


if __name__ == "__main__":
    main()
