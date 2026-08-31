# privacy-filter（OPF）sidecar 镜像（issue #127）
# 版本纪律（ADR-0002）：上游库 pin 到 commit；torch 用 CPU 轮（PyPI 默认轮带 CUDA 依赖 ~2.5GB，
# cpu index 轮 ~200MB——本 sidecar 面向无 GPU 机型预接入）。
FROM python:3.12-slim

RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir --no-deps \
      "opf @ https://github.com/openai/privacy-filter/archive/f7f00ca7fb869683eb732c010299d901457f19c3.tar.gz" \
 && pip install --no-cache-dir huggingface_hub numpy packaging safetensors tiktoken

COPY server.py /app/server.py

EXPOSE 8081
CMD ["python3", "/app/server.py"]
