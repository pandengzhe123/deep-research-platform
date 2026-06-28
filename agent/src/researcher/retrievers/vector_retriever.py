"""LangChain 向量检索器 —— 阿里云 text-embedding-v4（OpenAI 兼容格式）。"""

import os

from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings


def get_embeddings():
    """获取阿里云 text-embedding-v4 的 embedding 客户端（OpenAI 兼容）。"""
    return OpenAIEmbeddings(
        model=os.getenv("EMBED_MODEL", "text-embedding-v4"),
        api_key=os.getenv("DASHSCOPE_API_KEY", ""),
        base_url=os.getenv(
            "EMBED_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
    )


def build_vector_retriever(documents, persist_dir, collection_name, k=5):
    """
    用 LangChain Chroma 构建向量检索器。

    Args:
        documents: LangChain Document 列表
        persist_dir: Chroma 持久化目录
        collection_name: collection 名称（用于多租户隔离）
        k: 返回 Top K 个结果

    Returns:
        Chroma.as_retriever()
    """
    embeddings = get_embeddings()
    vectorstore = Chroma.from_documents(
        documents=documents,
        embedding=embeddings,
        persist_directory=persist_dir,
        collection_name=collection_name,
    )
    return vectorstore.as_retriever(search_kwargs={"k": k})
