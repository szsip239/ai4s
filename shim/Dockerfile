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
COPY app.py admin_api.py edm_lib.py doc_extract.py alert_poller.py ./
EXPOSE 8080
CMD ["python3", "app.py"]
