FROM python:3.12-slim
WORKDIR /app
# 只要 onnxruntime + tokenizer（无 torch，避开 gliner #26 的 OOM 坑）
RUN pip install --no-cache-dir onnxruntime "transformers<5" numpy
COPY app.py .
EXPOSE 8091
CMD ["python3", "app.py"]
