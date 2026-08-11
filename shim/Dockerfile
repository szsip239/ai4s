FROM python:3.12-slim
WORKDIR /app
# EDM 文档解析依赖（issue #48）：全部 manylinux wheel，免 apt、无模型。
# PyMuPDF 提供 fitz（PDF 解析，AGPL/商业双许可，私有部署内部使用）；docx/xlsx/pptx 走 OOXML 三库。
RUN pip install --no-cache-dir PyMuPDF python-docx openpyxl python-pptx
COPY app.py admin_api.py edm_lib.py doc_extract.py ./
EXPOSE 8080
CMD ["python3", "app.py"]
