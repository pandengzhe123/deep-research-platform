FROM python:3.11-slim

ENV PYTHONUTF8=1

WORKDIR /app

# 配置 pip 使用清华源（加速下载，可自行替换为阿里云/中科大）
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple && \
    pip config set global.trusted-host pypi.tuna.tsinghua.edu.cn

COPY pyproject.toml .
COPY src/ src/

# 安装依赖：只认 pyproject.toml（单一事实源）。
# 原先这里还重复列了一遍包名，结果 python-multipart / pymupdf / python-docx
# 只写在 Dockerfile 里、pyproject 里没有 → pip install -e . 路径下缺依赖，
# Agent 起不来（FastAPI 建 UploadFile 路由时 RuntimeError）。
RUN pip install --default-timeout=100 --no-cache-dir -e .

# 预下载 Embedding 模型，打进镜像（~120MB），运行时无需联网
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')"
# 预下载 Cross-Encoder 精排模型（~700MB）
RUN python -c "from sentence_transformers import CrossEncoder; CrossEncoder('BAAI/bge-reranker-base')"

# 复制模板而不是 .env：.env 被 gitignore，仓库里不存在，
# 原先 `COPY .env .env` 让「clone 后直接 docker compose up」必然失败。
# 真实 Key 由 docker-compose 的 env_file 在运行时注入（config.py 的
# load_dotenv 默认不覆盖已有环境变量，所以运行时注入优先）。
COPY .env.example .env

EXPOSE 8000
CMD ["uvicorn", "researcher.server:app", "--host", "0.0.0.0", "--port", "8000"]