"""评测结果目录管理 —— 统一时间戳归档，每次评测结果放同一目录下。

结构：
  results/
  ├── <时间戳>_ab/        A/B 对比（meta + old/new reports + judge）
  ├── <时间戳>_rag/       RAG 组件评测（retriever/generator/ablation/run_eval）
  └── _legacy/            历史散落文件归档

用法：
  from researcher.evaluation._results import new_run_dir
  d = new_run_dir("rag")   # → results/20260709_1830_rag/
  json.dump(data, open(os.path.join(d, "run_eval_rag.json"), "w"), ...)
"""

import os
import time

RESULTS_ROOT = os.path.join(os.path.dirname(__file__), "results")


def ts() -> str:
    """分钟级时间戳。不用 datetime.now()（会破坏 workflow resume），用 time.strftime。"""
    return time.strftime("%Y%m%d_%H%M")


def new_run_dir(tag: str) -> str:
    """创建带时间戳的评测目录，返回绝对路径。tag 如 'ab' / 'rag'。"""
    d = os.path.join(RESULTS_ROOT, f"{ts()}_{tag}")
    os.makedirs(d, exist_ok=True)
    return d


def latest_run_dir(tag: str) -> str | None:
    """找到最近一次某类型的评测目录。"""
    if not os.path.isdir(RESULTS_ROOT):
        return None
    dirs = sorted(
        d for d in os.listdir(RESULTS_ROOT)
        if d.endswith(f"_{tag}") and os.path.isdir(os.path.join(RESULTS_ROOT, d))
    )
    return os.path.join(RESULTS_ROOT, dirs[-1]) if dirs else None


def run_dir_for(tag: str, reuse_within_min: int = 30) -> str:
    """获取评测目录：若最近一次同类目录在 reuse_within_min 分钟内则复用，否则新建。

    让同一批评测的多个脚本（generator → e2e_diagnostic）写进同一个目录，
    而隔了很久的新评测自动开新目录。
    """
    latest = latest_run_dir(tag)
    if latest:
        name = os.path.basename(latest)  # 20260709_1830_rag
        try:
            t = time.strptime(name[:13], "%Y%m%d_%H%M")
            age_min = (time.time() - time.mktime(t)) / 60
            if age_min < reuse_within_min:
                return latest
        except Exception:
            pass
    return new_run_dir(tag)

