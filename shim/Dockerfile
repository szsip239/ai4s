FROM python:3.12-slim
WORKDIR /app
# Tesseract OCR 引擎（issue #50，本地识别数据不出域）：debian 包随 base 镜像 apt 仓库，
# 2026-08 重建实测 tesseract 5.5.0（重建时以 `docker exec ai4s-shim tesseract --version` 复核）。
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*
# EDM 文档解析依赖（issue #48/#50）：全部 manylinux wheel，免编译；pin 版本（issue #49，
# 与 shim/requirements-dev.txt 一致）。PyMuPDF 提供 fitz（AGPL/商业双许可，私有部署内部使用）。
RUN pip install --no-cache-dir PyMuPDF==1.28.2 python-docx==1.2.0 openpyxl==3.1.5 python-pptx==1.0.2 \
    pytesseract==0.3.13 Pillow==12.3.0
# PromptGuard 2 进程内推理依赖（issue #67：promptguard 服务并入；版本 pin 自原 promptguard
# 容器实测 pip show）。pg_engine 函数级懒加载（issue #49）：pg.enabled=false 时这些库不被 import。
RUN pip install --no-cache-dir onnxruntime==1.28.0 transformers==4.57.6 numpy==2.5.2
# 新建 key 审批归属步骤（issue #72）：createAPIKey 无 userID 入参，建后 SQL 直改 api_keys.user_id。
# alert_poller 函数级懒加载（同 issue #49 纪律），未配置 AXONHUB_DB_DSN 时不 import。
RUN pip install --no-cache-dir "psycopg[binary]==3.2.12"
COPY app.py admin_api.py self_api.py key_requests.py edm_lib.py doc_extract.py alert_poller.py pg_engine.py feishu_lib.py ./
EXPOSE 8080
CMD ["python3", "app.py"]
