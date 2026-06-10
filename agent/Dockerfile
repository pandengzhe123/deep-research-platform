FROM python:3.11-slim

WORKDIR /app

# 配置 pip 使用清华源（加速下载，可自行替换为阿里云/中科大）
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple && \
    pip config set global.trusted-host pypi.tuna.tsinghua.edu.cn

COPY pyproject.toml .
COPY src/ src/

# 安装依赖：增加超时时间，去掉错误屏蔽，使用 && 确保前一步成功
RUN pip install --default-timeout=100 --no-cache-dir -e . && \
    pip install --default-timeout=100 --no-cache-dir \
        fastapi uvicorn sse-starlette chromadb sentence-transformers openai \
        tavily-python python-dotenv httpx beautifulsoup4 markdownify pydantic \
        duckduckgo-search scikit-learn pymupdf python-multipart

COPY .env .env

EXPOSE 8000
CMD ["uvicorn", "researcher.server:app", "--host", "0.0.0.0", "--port", "8000"]