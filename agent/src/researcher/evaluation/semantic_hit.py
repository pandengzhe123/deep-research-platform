"""语义命中判断 —— 方案 B（embedding 语义相似度）。

背景问题（缺陷 6）：
  消融/回归的命中判定曾用 all(kw in result for kw in expected) 字面匹配，
  语义缺失——同义词/翻译/缩写漏判。曾试方案 C'（LLM 逐题判断），但：
    - LLMClient 是异步的，消融/回归是同步循环，async/sync 不匹配 → 死代码
    - 即使修好，逐题调 LLM 成本爆炸 + 模式对比引入不一致测量误差
    - result[:3000] 截断保留"substring 奖励宽 chunk"偏置

方案 B（embedding 语义相似度）：
  ① expected_chunks 向量化
  ② 检索结果按段落切块，每块向量化
  ③ 任一 chunk 与任一 expected 的余弦相似度 ≥ 阈值 → 命中
  特点：同步、便宜（一次批量 embedding）、稳定（无逐题随机性）、可归因

用法：
  from researcher.evaluation.semantic_hit import SemanticHit
  hit = SemanticHit()          # 复用阿里云 embedding
  found, score, mode = hit.check(question, expected_chunks, result_text)
  # mode: "semantic"（语义命中） / "literal"（字面命中） / "miss"
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env"))

# 语义命中阈值：余弦相似度 ≥ 0.8 视为语义等价
SEMANTIC_THRESHOLD = 0.8


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def _split_paragraphs(text: str, max_chars: int = 300) -> list[str]:
    """检索结果按段落/句号切块，控制每块长度。"""
    # 先按段落切，段落过长再按句号切
    chunks = []
    for para in re.split(r"\n{2,}|(?<=[。！？.!?])\s+", text):
        para = para.strip()
        if not para or len(para) < 10:
            continue
        # 长段落按句号切
        if len(para) > max_chars:
            for sent in re.split(r"(?<=[。！？.!?])", para):
                if sent.strip() and len(sent.strip()) >= 10:
                    chunks.append(sent.strip()[:max_chars])
        else:
            chunks.append(para[:max_chars])
    return chunks


class SemanticHit:
    """语义命中判断器（方案 B 改版：用完整问题做语义单位）。

    关键洞察：expected_chunks 是短关键词（2-4 字），embedding 区分度差
    （命中 0.4-0.6、未命中 0.4 混在一起）。而 question 是完整语义单元，
    embedding 区分度清晰（命中 0.6-0.8、未命中 0.33-0.41）。

    所以语义命中的正确做法：判断"检索结果是否回答了这个问题"，
    而不是"检索结果是否包含这些关键词"。
    """

    def __init__(self, threshold: float = 0.5):
        from researcher.kb import _DashScopeEmbeddings
        self._embedder = _DashScopeEmbeddings()
        self._threshold = threshold

    def _is_literal_hit(self, expected: list[str], result: str) -> bool:
        """字面全中 → 直接命中（充分条件，零成本）。"""
        return all(kw in result for kw in expected)

    def check(self, question: str, expected: list[str], result: str) -> tuple[bool, float, str]:
        """判断检索结果是否命中 question（语义层面）。

        返回 (命中与否, 最高相似度, 判定方式)：
          (True, score, "literal")   → 字面全中（零成本）
          (True, score, "semantic")  → question embedding 语义命中
          (False, score, "miss")     → 未命中
        """
        if not expected:
            return False, 0.0, "miss"
        # 第 1 层：字面全中 → 直接命中（零成本）
        if self._is_literal_hit(expected, result):
            return True, 1.0, "literal"

        # 第 2 层：embedding 语义判断
        try:
            chunks = _split_paragraphs(result)[:30]  # 控制块数，避免 embedding 太多
            if not chunks:
                return False, 0.0, "miss"
            # 一次批量 embedding：expected + chunks 一起算
            texts = list(expected) + chunks
            vecs = self._embedder.embed(texts)
            expected_vecs = vecs[:len(expected)]
            # 用 question（完整语义单元）而非 expected_chunks（短词）做语义判定
            # 短词 embedding 区分度差，question embedding 区分度清晰（命中 0.6+ / 未命中 0.4-）
            qv = self._embedder.embed([question])[0]
            chunk_vecs = vecs[len(expected):]

            best = 0.0
            for cv in chunk_vecs:
                s = _cosine(qv, cv)
                if s > best:
                    best = s
            found = best >= self._threshold
            return found, round(best, 3), ("semantic" if found else "miss")
        except Exception:
            return False, 0.0, "miss"
