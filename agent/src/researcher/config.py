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
    max_search_rounds: int = int(os.getenv("MAX_SEARCH_ROUNDS", "10"))
    max_supervisor_rounds: int = int(os.getenv("MAX_SUPERVISOR_ROUNDS", "6"))
    max_parallel_researchers: int = int(os.getenv("MAX_PARALLEL_RESEARCHERS", "5"))

    # ---- 上下文 ----
    # 对话历史字符数上限。DeepSeek V4 Flash 有 1M token 上下文，
    # 中文约 2 chars/token，留一半给当前轮次搜索和 prompt → 默认 1M 字符
    max_history_chars: int = int(os.getenv("MAX_HISTORY_CHARS", "1000000"))
    # 单个搜索结果最大字符数，防止搜索结果 OOM
    max_results_chars: int = int(os.getenv("MAX_RESULTS_CHARS", "300000"))
    # 保留最近几轮合并后的搜索结果
    max_round_results: int = int(os.getenv("MAX_ROUND_RESULTS", "3"))

    # ---- Redis ----
    # 搜索缓存 / URL 去重 / 会话锁等热数据。
    # 开发：docker run -d -p 6379:6379 redis:7-alpine
    # 生产：换成托管 Redis 地址（redis://:password@host:6379/0），代码零改动
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # ---- 报告生成 ----
    # 报告类调用的输出上限。显式设置的理由：不传 max_tokens 时用的是服务端默认值，
    # 而这个默认值实测并不稳定（同一个 prompt 有时自然结束在 9.7K tokens，有时 8192
    # 就被切）—— 依赖一个自己预测不了的默认值，等于把「报告会不会被截断」交给运气。
    # 显式设一个足够大的上限，把这份不确定性去掉。
    #
    # 384000 = DeepSeek 官方目前提供的输出上限（实测 API 接受；代码里旧的
    # "65537 会 400" 注释已过时）。
    # ⚠️ 这是「上限」不是「目标」：模型写完就停（finish_reason=stop）。实测本项目
    #    报告的自然长度约 9.7K tokens / 23K 字符，384K 只是 40 倍余量。
    #    上限越大越要留意：报告会追加进对话历史，而 max_history_chars 默认
    #    1,000,000 —— 单份超大报告会把后续追问直接顶进上下文压缩流程。
    report_max_tokens: int = int(os.getenv("REPORT_MAX_TOKENS", "384000"))


# 全局单例
config = Config()
