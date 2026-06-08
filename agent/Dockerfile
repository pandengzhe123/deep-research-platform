FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir -e . 2>/dev/null; pip install --no-cache-dir fastapi uvicorn sse-starlette chromadb sentence-transformers openai tavily-python python-dotenv httpx beautifulsoup4 markdownify pydantic duckduckgo-search scikit-learn pymupdf

COPY src/ src/
COPY .env .env

EXPOSE 8000
CMD ["uvicorn", "researcher.server:app", "--host", "0.0.0.0", "--port", "8000"]
