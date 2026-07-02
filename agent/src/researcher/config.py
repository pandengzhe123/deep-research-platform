"""配置管理 —— 一个简单的配置类，从环境变量读取。"""

import os

from pathlib import Path

from dotenv import load_dotenv

# 从 agent 目录加载 .env（兼容不同 CWD）
env_path = Path(__file__).parent.parent.parent / ".env"
load_dotenv(env_path) if env_path.exists() else load_dotenv()


class Config:
    """全局配置，从环境变量加载。"""

    # ---- LLM ----
    llm_provider: str = os.getenv("LLM_PROVIDER", "deepseek")
    llm_model: str = os.getenv("LLM_MODEL", "deepseek-v4-flash")
    llm_api_key: str = os.getenv("DEEPSEEK_API_KEY", "")
    llm_base_url: str = os.getenv(
        "DEEPSEEK_BASE_URL", "https://api.deepseek.com"
    )

    # ---- 搜索 ----
    tavily_api_key: str = os.getenv("TAVILY_API_KEY", "")
    max_search_workers: int = int(os.getenv("MAX_SEARCH_WORKERS", "3"))
    max_content_length: int = int(os.getenv("MAX_CONTENT_LENGTH", "20000"))

    # ---- 循环控制 ----
    max_search_rounds: int = int(os.getenv("MAX_SEARCH_ROUNDS", "5"))
    max_supervisor_rounds: int = int(os.getenv("MAX_SUPERVISOR_ROUNDS", "3"))
    max_parallel_researchers: int = int(os.getenv("MAX_PARALLEL_RESEARCHERS", "3"))

    # ---- 上下文 ----
    # 对话历史字符数上限。DeepSeek V4 Flash 有 1M token 上下文，
    # 中文约 2 chars/token，留一半给当前轮次搜索和 prompt → 默认 1M 字符
    max_history_chars: int = int(os.getenv("MAX_HISTORY_CHARS", "1000000"))
    # 单个搜索结果最大字符数，防止搜索结果 OOM
    max_results_chars: int = int(os.getenv("MAX_RESULTS_CHARS", "300000"))
    # 保留最近几轮合并后的搜索结果
    max_round_results: int = int(os.getenv("MAX_ROUND_RESULTS", "3"))


# 全局单例
config = Config()
