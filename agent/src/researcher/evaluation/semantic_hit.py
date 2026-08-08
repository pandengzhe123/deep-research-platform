"""语义命中判断 —— 修复"字面关键词匹配"的语义缺失。

背景问题（缺陷 6）：
  消融/回归的命中判定用 all(kw in result for kw in expected)，即"字面字符串
  是否出现在返回文本里"。这有语义缺失：
    - 不必要性（漏判）：答案对了但换说法/翻译/缩写 → 误判没命中
    - 不充分性（误判）：短词免费子串命中（"200"撞"2009"）→ 无关也算命中
    - 系统性扭曲：substring 奖励宽 chunk、惩罚精准检索

混合策略（方案 C'，成本可控）：
  第 1 层：字面全中 → 直接算命中（零成本，大多数题在这层解决）
  第 2 层：字面不中 → 调 LLM 判断"检索结果是否语义包含信息点"
          （只对疑似漏判的题调 LLM，成本可控）

用法：
  from researcher.evaluation.semantic_hit import semantic_hit
  found, mode = semantic_hit(question, expected_chunks, result_text, llm=None)
  # mode: "literal"（字面命中） / "llm_equiv"（LLM 识别语义等价）
"""

# LLM 语义判断 prompt（做法一：直接问"检索结果是否包含信息点"）
VERIFY_HIT_PROMPT = """判断下面检索结果是否真正包含了问题所要求的全部信息点。

问题：{question}

需要的信息点：
{expected}

检索结果：
{result}

判断规则：
- 如果检索结果真正包含全部信息点（允许同义词、翻译、缩写、换说法等语义等价表达）→ true
- 如果缺少任何一个信息点，或某个词只是字面出现但意思不符（如数字"200"撞到"2009"）→ false
- 如果信息点出现在与问题无关的文档/上下文里 → false

只返回 true 或 false，不要加任何解释。"""


def semantic_hit(question: str, expected: list[str], result: str, llm=None) -> tuple[bool, str]:
    """判断检索结果是否命中 expected 信息点。

    返回 (命中与否, 判定方式)：
      (True, "literal")  → 字面全中（零成本）
      (True, "llm_equiv") → LLM 识别语义等价
      (False, "llm_miss") → LLM 确认确实未命中
      (False, "literal_miss_no_llm") → 字面不中且无 LLM，降级为未命中
    """
    # 第 1 层：字面全中 → 直接命中（充分条件，零成本）
    if all(kw in result for kw in expected):
        return True, "literal"

    # 第 2 层：字面不中 → 调 LLM 判断语义等价
    if llm is not None:
        try:
            resp = llm.chat(
                system_prompt="你是检索质量判断助手。严格按规则返回 true 或 false。",
                user_message=VERIFY_HIT_PROMPT.format(
                    question=question,
                    expected="、".join(str(e) for e in expected),
                    result=result[:3000],
                ),
            )
            ok = resp.strip().lower().startswith("true")
            return ok, "llm_equiv" if ok else "llm_miss"
        except Exception:
            return False, "literal_miss_no_llm"
    return False, "literal_miss_no_llm"
