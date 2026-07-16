"""结构化 Trace 模块 —— 记录每次研究的完整调用链路。

用 JSONL 格式记录：LLM 调用（token/耗时/模型）、搜索（query/结果数/耗时/来源）、
Agent 进度事件、轮次转换、上下文压缩。每次研究一个 JSONL 文件，存储在 reports/ 目录下。

用法：
  async with TraceRun(question="...", output_dir="reports/xxx", ...) as trace:
      agent.trace = trace
      agent.llm.trace = trace
      report = await agent.run(question)
      # trace.jsonl 在 __aexit__ 时自动写入
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path


class TraceRun:
    """一次研究的完整 trace。作为异步上下文管理器使用。"""

    def __init__(
        self,
        question: str,
        output_dir: str,
        level: int = 2,
        model: str = "",
        search_mode: str = "web_only",
    ):
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)

        self._events: list[str] = []       # 每行 JSONL
        self._lock = threading.Lock()

        self._llm_calls = 0
        self._llm_errors = 0
        self._search_calls = 0
        self._search_errors = 0
        self._total_prompt_tokens = 0
        self._total_completion_tokens = 0
        self._rounds_completed = 0

        self._level = level
        self._model = model
        self._search_mode = search_mode
        self._question = question
        self._t0 = time.time()

    # ================================================================
    # 异步上下文管理器
    # ================================================================

    async def __aenter__(self):
        self._append({
            "type": "run_start",
            "ts": time.time(),
            "question": self._question,
            "level": self._level,
            "model": self._model,
            "search_mode": self._search_mode,
        })
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        duration_s = round(time.time() - self._t0, 3)
        error = str(exc_val) if exc_val else None

        self._append({
            "type": "run_end",
            "ts": time.time(),
            "duration_s": duration_s,
            "error": error,
            "summary": {
                "llm_calls": self._llm_calls,
                "llm_errors": self._llm_errors,
                "search_calls": self._search_calls,
                "search_errors": self._search_errors,
                "total_prompt_tokens": self._total_prompt_tokens,
                "total_completion_tokens": self._total_completion_tokens,
                "rounds_completed": self._rounds_completed,
            },
        })
        self._flush()
        return False  # 不吞掉异常

    # ================================================================
    # 记录方法
    # ================================================================

    def record_event(self, step: str, message: str, round_num: int = 0,
                     extra: dict | None = None):
        """同步方法——由 emit 回调包装器调用。镜像现有的 emit() 事件。"""
        event: dict = {
            "type": "agent_event",
            "ts": time.time(),
            "step": step,
            "message": message,
        }
        if round_num:
            event["round"] = round_num
        if extra:
            event.update(extra)
        self._append(event)

    async def record_llm(self, method: str, model: str, usage: dict | None,
                         duration_ms: int, request_id: str = "",
                         success: bool = True, purpose: str = "",
                         retries: int = 0, error: str = ""):
        """记录一次 LLM 调用。"""
        if usage:
            self._total_prompt_tokens += usage.get("prompt_tokens", 0)
            self._total_completion_tokens += usage.get("completion_tokens", 0)

        self._llm_calls += 1
        if not success:
            self._llm_errors += 1

        self._append({
            "type": "llm_call",
            "ts": time.time(),
            "method": method,
            "model": model,
            "usage": usage,
            "duration_ms": duration_ms,
            "request_id": request_id,
            "success": success,
            "purpose": purpose[:120] if purpose else "",
            "retries": retries,
            "error": error,
        })

    async def record_search(self, queries: list[str], result_count: int,
                            deduped_count: int = 0,
                            total_duration_ms: int = 0,
                            cache_hit: bool = False,
                            success: bool = True, error: str = ""):
        """记录一次搜索调用。"""
        self._search_calls += 1
        if not success:
            self._search_errors += 1

        self._append({
            "type": "search_call",
            "ts": time.time(),
            "queries": queries,
            "result_count": result_count,
            "deduped_count": deduped_count,
            "total_duration_ms": total_duration_ms,
            "cache_hit": cache_hit,
            "success": success,
            "error": error,
        })

    async def record_round(self, round_num: int, max_rounds: int,
                           event: str = "start"):
        """记录轮次转换。"""
        self._append({
            "type": f"round_{event}",
            "ts": time.time(),
            "round": round_num,
            "max_rounds": max_rounds,
        })
        if event == "end":
            self._rounds_completed = max(self._rounds_completed, round_num)

    async def record_compress(self, before_chars: int, after_chars: int,
                              duration_ms: int, success: bool = True):
        """记录上下文压缩事件。"""
        self._append({
            "type": "context_compress",
            "ts": time.time(),
            "before_chars": before_chars,
            "after_chars": after_chars,
            "duration_ms": duration_ms,
            "success": success,
        })

    async def record_error(self, source: str, message: str,
                           round_num: int = 0):
        """记录异常事件。"""
        event: dict = {
            "type": "error",
            "ts": time.time(),
            "source": source,
            "message": message,
        }
        if round_num:
            event["round"] = round_num
        self._append(event)

    # ================================================================
    # 内部方法
    # ================================================================

    def _append(self, event: dict):
        """线程安全地追加事件到内存缓冲。"""
        line = json.dumps(event, ensure_ascii=False)
        with self._lock:
            self._events.append(line)

    def _flush(self):
        """写入 JSONL 文件并清空缓冲。"""
        output_path = self._output_dir / "trace.jsonl"
        with self._lock:
            if not self._events:
                return
            with open(output_path, "w", encoding="utf-8") as f:
                for line in self._events:
                    f.write(line + "\n")
            self._events.clear()
        print(f"\n  [trace] 追踪日志已保存: {output_path}")
        print(f"     LLM 调用 {self._llm_calls} 次 (失败 {self._llm_errors})")
        print(f"     搜索调用 {self._search_calls} 次 (失败 {self._search_errors})")
        print(f"     总 prompt tokens: {self._total_prompt_tokens}")
        print(f"     总 completion tokens: {self._total_completion_tokens}")
        print(f"     完成轮次: {self._rounds_completed}")
