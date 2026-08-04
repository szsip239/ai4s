FROM python:3.12-slim
WORKDIR /app
# CPU 版 torch + gliner（模型运行期下载到挂载卷）
# transformers 必须 <5：v5 的缓存/离线加载行为与 gliner 0.2.x 不兼容（issue #26 实测踩坑）
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir gliner==0.2.28 "transformers<5"
COPY app.py .
EXPOSE 8090
CMD ["python3", "app.py"]
