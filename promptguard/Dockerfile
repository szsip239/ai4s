FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir onnxruntime "transformers<5" numpy
COPY app.py .
EXPOSE 8092
CMD ["python3", "app.py"]
