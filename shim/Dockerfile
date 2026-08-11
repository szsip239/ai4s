FROM python:3.12-slim
WORKDIR /app
# EDM 文档解析依赖（issue #48）：全部 manylinux wheel，免 apt、无模型；issue #49 pin 版本
# （与 shim/requirements-dev.txt 一致，本机跑测试用同一锁定集）。
# PyMuPDF 提供 fitz（PDF 解析，AGPL/商业双许可，私有部署内部使用）；docx/xlsx/pptx 走 OOXML 三库。
RUN pip install --no-cache-dir PyMuPDF==1.28.2 python-docx==1.2.0 openpyxl==3.1.5 python-pptx==1.0.2
COPY app.py admin_api.py edm_lib.py doc_extract.py ./
EXPOSE 8080
CMD ["python3", "app.py"]
