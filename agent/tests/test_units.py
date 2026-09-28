"""单元测试 —— 只测确定性的纯函数，不调 LLM，不联网。

运行: cd D:\deep_research\agent && .venv\Scripts\python tests\test_units.py
"""

import os
import random
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# ============================================================
# kb.py
# ============================================================

from researcher.kb import chunk_text, read_file, parse_source_docs, KnowledgeBase


def test_chunk_short_paragraph():
    chunks = chunk_text("一段短文本。")
    assert len(chunks) == 1
    assert chunks[0] == "一段短文本。"


def test_chunk_long_splits_by_sentence():
    text = ("第一句。第二句。第三句。第四句。第五句。") * 10
    chunks = chunk_text(text, chunk_size=200)
    for c in chunks:
        assert len(c) <= 300, f"chunk 过长: {len(c)}"


def test_chunk_multiple_paragraphs():
    """多段落切分：短块会与后续块合并（设计如此），但不得越界、不得粘回一整块。

    旧断言 `chunks[0] == "短段落。"` 与实现语义冲突：min_size(默认300) > chunk_size(100)
    时合并会把所有块粘成一块，len(chunks)==1。
    """
    text = "短段落。\n\n" + "长段落。" * 50
    chunks = chunk_text(text, chunk_size=100)
    assert len(chunks) >= 2, f"应切成多块，实际 {len(chunks)} 块"
    assert chunks[0].startswith("短段落。"), f"首块应从第一段开始: {chunks[0][:20]!r}"
    for c in chunks:
        assert len(c) <= 100, f"chunk 超过 chunk_size: {len(c)}"
    # 不丢内容
    joined = "".join(chunks)
    assert joined.count("长段落。") == 50, f"内容丢失: {joined.count('长段落。')} 段"


def test_chunk_empty():
    assert chunk_text("") == []
    assert chunk_text("\n\n\n") == []


def test_chunk_size_never_exceeds_limit():
    text = "A" * 2000
    chunks = chunk_text(text, chunk_size=500, overlap=100)
    for c in chunks:
        assert len(c) <= 600, f"chunk 过长: {len(c)} > 600"


def test_chunk_overlap_not_less_than_chunk_size():
    """回归：overlap >= chunk_size 曾让步长 <= 0 —— 步长为 0 抛 ValueError，
    步长为负则一块都不返回、内容被静默丢弃。"""
    chunks = chunk_text("字" * 300, chunk_size=50, overlap=100)
    assert chunks, "内容被静默丢弃（返回 0 块）"
    assert sum(len(c) for c in chunks) >= 300, "内容不完整"
    for c in chunks:
        assert len(c) <= 50, f"chunk 超过 chunk_size: {len(c)}"


def test_chunk_min_size_larger_than_chunk_size():
    """回归：min_size > chunk_size 曾让合并产出超过 chunk_size 的块（401 > 250）。"""
    chunks = chunk_text("A" * 200 + "\n\n" + "B" * 200, chunk_size=250)
    for c in chunks:
        assert len(c) <= 250, f"chunk 超过 chunk_size: {len(c)}"


def test_chunk_overlap_only_applies_in_char_fallback():
    """overlap 只在字符级降级路径生效 —— 段落/句子级没有重叠。

    实测（agent/eval 全部 82 个文档）没有任何 >500 字的句子，所以 overlap
    对实际内容**从未生效**。这不是 bug（段落/句子本身就是语义完整单元），
    但 `overlap=100` 这个参数名会让人误以为"相邻块有重叠"，据此推断检索行为
    会得出错误结论 —— 本项目的早期分析就踩过。这个测试把真实契约钉住。
    """
    from researcher.kb import chunk_text

    # 段落级：两块之间不应有重叠
    # （段落长 360 字：超过 min_size=300 所以不会被合并，又 < chunk_size=500）
    text = "\n\n".join(["第一段内容。" * 60, "第二段内容。" * 60])
    chunks = chunk_text(text, chunk_size=500, overlap=100, min_size=300)
    assert len(chunks) >= 2, f"应至少切出 2 块，实际 {len(chunks)}"
    for a, b in zip(chunks, chunks[1:]):
        assert not b.startswith(a[-50:]), "段落级不应产生重叠"

    # 字符级：单个超长句（无句末标点，无法按句切）会走降级路径，那里才有重叠
    long_sent = "啊" * 1200
    cs = chunk_text(long_sent, chunk_size=500, overlap=100, min_size=300)
    assert len(cs) >= 2, f"超长句应被切成多块，实际 {len(cs)}"
    # step = 500 - 100 = 400 → 相邻块应共享 100 字
    assert cs[0][-100:] == cs[1][:100], "字符级路径应产生 100 字重叠"


def test_read_txt_file():
    d = tempfile.mkdtemp()
    try:
        p = Path(d) / "test.txt"
        p.write_text("Hello 世界\n第二行", encoding="utf-8")
        text = read_file(p)
        assert "Hello 世界" in text
        assert "第二行" in text
    finally:
        shutil.rmtree(d)


def test_read_md_file():
    d = tempfile.mkdtemp()
    try:
        p = Path(d) / "readme.md"
        p.write_text("# 标题\n正文内容。", encoding="utf-8")
        text = read_file(p)
        assert "标题" in text
        assert "正文内容" in text
    finally:
        shutil.rmtree(d)


def test_read_file_not_found():
    try:
        read_file(Path("/nonexistent/file.txt"))
        assert False, "应该抛异常"
    except FileNotFoundError:
        pass


def test_read_unsupported_extension():
    d = tempfile.mkdtemp()
    try:
        p = Path(d) / "data.csv"
        p.write_text("a,b,c")
        try:
            read_file(p)
            assert False, "应该抛异常"
        except ValueError as e:
            assert "不支持" in str(e)
    finally:
        shutil.rmtree(d)


# ============================================================
# 搜索格式化逻辑 —— 提取纯函数验证
# ============================================================

def test_url_dedup():
    """URL 去重：相同 URL 只保留第一次出现。"""
    results = [
        {"url": "https://a.com", "title": "A"},
        {"url": "https://b.com", "title": "B"},
        {"url": "https://a.com", "title": "A 重复"},  # 重复
    ]
    seen: dict[str, dict] = {}
    for r in results:
        url = r["url"]
        if url not in seen:
            seen[url] = r
    assert len(seen) == 2
    assert seen["https://a.com"]["title"] == "A"  # 保留第一次


def test_markdown_link_pattern():
    pattern = r"\[([^\]]+)\]\(https?://[^)]+\)"
    matches = re.findall(pattern, "参考 [百度](https://baidu.com) 和 [谷歌](http://google.com)")
    assert matches == ["百度", "谷歌"]

    # 无链接
    assert re.findall(pattern, "纯文本没有链接") == []

    # Markdown 图片（![]开头）不算
    text = "![图片](https://img.com/a.png) 不是引用链接"
    matches = re.findall(pattern, text)
    assert "图片" in matches  # 基础正则会匹配，这是已知限制


def test_search_result_format():
    """格式化搜索结果。"""
    results = {
        "https://a.com": {
            "title": "标题A",
            "content": "摘要内容A\n多行文本",
        },
        "https://b.com": {
            "title": "标题B",
            "content": "摘要内容B",
        },
    }
    # 模拟 search_fast 的格式化逻辑
    output_parts = ["# 搜索结果\n"]
    for i, (url, r) in enumerate(results.items()):
        output_parts.append(f"\n--- 来源 {i+1}: {r['title']} ---")
        output_parts.append(f"URL: {url}")
        output_parts.append(f"\n{r['content']}")
        output_parts.append("\n" + "-" * 60)

    formatted = "\n".join(output_parts)
    assert "标题A" in formatted
    assert "https://a.com" in formatted
    assert "摘要内容B" in formatted
    assert "--- 来源 1:" in formatted
    assert "--- 来源 2:" in formatted


def test_source_label_citation():
    """验证报告中的引用格式检查。"""
    # 有效的引用
    text = "参考 [百度](https://baidu.com) 和 [Google](https://google.com)。来源： [GitHub](https://github.com)"
    pattern = r"\[([^\]]+)\]\(https?://[^)]+\)"
    links = re.findall(pattern, text)
    assert len(links) == 3

    # Sources 章节
    assert bool(re.search(r"(Sources|参考来源|参考源)", "## Sources")) is True
    assert bool(re.search(r"(Sources|参考来源|参考源)", "### 参考来源")) is True
    assert bool(re.search(r"(Sources|参考来源|参考源)", "没有这个章节")) is False


# ============================================================
# 中文检测
# ============================================================

def test_chinese_detection():
    cn_pattern = re.compile(r"[一-鿿]")

    assert len(cn_pattern.findall("量子计算")) == 4
    assert len(cn_pattern.findall("Hello World")) == 0
    assert len(cn_pattern.findall("量子 Quantum 计算")) == 4  # 量、子、计、算


# ============================================================
# agent.py —— 压缩窗口 tool_calls 配对安全（TODO 6.4）
# ============================================================

import asyncio

from researcher.agent import (
    _safe_window_start, _truncate_context, _drop_dangling_tail, SUMMARY_PREFIX,
)


def _msg(role, content="", tool_calls=None, tool_call_id=None):
    m = {"role": role, "content": content}
    if tool_calls:
        m["tool_calls"] = tool_calls
    if tool_call_id:
        m["tool_call_id"] = tool_call_id
    return m


def _tc(cid):
    return {"id": cid, "type": "function", "function": {"name": "search", "arguments": "{}"}}


def _assert_pairing_complete(messages):
    """窗口内 assistant(tool_calls) 与 tool 响应必须一一配对（OpenAI 协议不变量）。"""
    need = set()
    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                need.add(tc["id"])
        elif m.get("role") == "tool":
            tid = m.get("tool_call_id")
            assert tid in need, f"孤立 tool 消息: {tid}"
            need.discard(tid)
    assert not need, f"悬空 tool_calls: {need}"


def test_safe_window_start_plain_window():
    """窗口起点落在普通消息上 → 不扩展"""
    msgs = [_msg("user", "q")] + [_msg("assistant", f"a{i}") for i in range(10)]
    assert _safe_window_start(msgs, 5) == len(msgs) - 5


def test_safe_window_start_orphan_tool():
    """起点切在 assistant 与其 tool 响应之间 → 向前扩展包含 assistant"""
    msgs = [
        _msg("user", "q"),
        _msg("assistant", "", tool_calls=[_tc("c1")]),
        _msg("tool", "r1", tool_call_id="c1"),
        _msg("assistant", "done"),
    ]
    assert _safe_window_start(msgs, 2) == 1  # start=2 是孤立 tool → 扩到 1


def test_safe_window_start_aligned_assistant():
    """起点正好是 assistant(tool_calls)，其响应都在窗口内 → 不扩展"""
    msgs = [
        _msg("user", "q"),
        _msg("assistant", "old"),
        _msg("user", "q2"),
        _msg("assistant", "", tool_calls=[_tc("c1")]),
        _msg("tool", "r1", tool_call_id="c1"),
    ]
    assert _safe_window_start(msgs, 3) == 2


def test_safe_window_start_multi_tool_calls():
    """一个 assistant 发起多个 tool_calls → 全部响应在窗口内才安全"""
    msgs = [
        _msg("user", "q"),
        _msg("assistant", "", tool_calls=[_tc("c1"), _tc("c2")]),
        _msg("tool", "r1", tool_call_id="c1"),
        _msg("tool", "r2", tool_call_id="c2"),
        _msg("assistant", "done"),
    ]
    assert _safe_window_start(msgs, 2) == 1
    assert _safe_window_start(msgs, 4) == 1


def _build_tool_loop_messages(rounds=8):
    """构造 len=1+2*rounds 的消息：user + (assistant tool_calls, tool 响应)*rounds"""
    msgs = [_msg("user", "问题" * 50)]
    for i in range(rounds):
        msgs.append(_msg("assistant", "", tool_calls=[_tc(f"c{i}")]))
        msgs.append(_msg("tool", "结果" * 50, tool_call_id=f"c{i}"))
    return msgs


def test_truncate_context_compressed_pairing_complete():
    """集成：超限压缩后，窗口内配对必须完整"""
    class FakeLLM:
        async def chat(self, system_prompt, user_message, **kw):
            return "压缩摘要"

    msgs = _build_tool_loop_messages()
    new_msgs, _ = asyncio.run(_truncate_context(
        msgs, total_chars=10 ** 6, max_chars=100,
        llm=FakeLLM(), emit=lambda e: None, round_num=1, context_warned=False,
    ))
    assert len(new_msgs) < len(msgs), "应发生压缩"
    assert new_msgs[0] is msgs[0], "messages[0] 必须保留"
    _assert_pairing_complete(new_msgs)


def test_truncate_context_hard_truncate_pairing_complete():
    """集成：压缩失败走硬截断，配对同样必须完整"""
    class FailLLM:
        async def chat(self, *a, **kw):
            raise RuntimeError("LLM 不可用")

    msgs = _build_tool_loop_messages()
    new_msgs, _ = asyncio.run(_truncate_context(
        msgs, total_chars=10 ** 6, max_chars=100,
        llm=FailLLM(), emit=lambda e: None, round_num=1, context_warned=False,
    ))
    _assert_pairing_complete(new_msgs)


def test_truncate_context_keeps_summary_on_hard_truncate():
    """B6：压缩后仍超限触发硬截断时，必须保留刚生成的压缩摘要。"""
    class FakeLLM:
        async def chat(self, system_prompt, user_message, **kw):
            return "关键约束A"

    msgs = [{"role": "user", "content": "问题" * 100}]
    for _ in range(10):
        msgs.append({"role": "assistant", "content": "内容" * 300})

    new_msgs, _ = asyncio.run(_truncate_context(
        msgs, total_chars=10 ** 6, max_chars=3000,
        llm=FakeLLM(), emit=lambda e: None, round_num=1, context_warned=False,
    ))
    assert new_msgs[0] is msgs[0], "messages[0] 必须保留"
    has_summary = any(
        str(m.get("content", "")).startswith("[早期对话摘要]") for m in new_msgs
    )
    assert has_summary, (
        "硬截断后摘要丢失: "
        + str([str(m.get("content", ""))[:20] for m in new_msgs])
    )


def test_safe_window_start_dangling_assistant():
    """assistant 的 tool_calls 在整段消息里都没有响应 → 任何含它的窗口都不安全"""
    msgs = [
        _msg("user", "q"),
        _msg("assistant", "old"),
        _msg("user", "q2"),
        _msg("assistant", "", tool_calls=[_tc("c9")]),   # 悬空：无 tool 响应
        _msg("user", "q3"),
    ]
    assert _safe_window_start(msgs, 2) == 1


def test_safe_window_start_large_input_is_fast():
    """B30：长会话压缩时不得 O(n²)。3000 条消息必须秒级完成。"""
    import time
    msgs = _build_tool_loop_messages(rounds=1500)
    t0 = time.time()
    start = _safe_window_start(msgs, 5)
    elapsed = time.time() - t0
    assert start > 0
    assert elapsed < 1.0, f"耗时 {elapsed:.3f}s，疑似退化为 O(n²)"


def test_drop_dangling_tail_dangling_assistant():
    """B31：尾部悬空 assistant(tool_calls) 必须删除"""
    msgs = [
        _msg("user", "q"),
        _msg("assistant", "", tool_calls=[_tc("c1")]),
        _msg("tool", "r1", tool_call_id="c1"),
        _msg("assistant", "", tool_calls=[_tc("c2")]),   # 悬空
    ]
    out = _drop_dangling_tail(msgs)
    assert len(out) == 3, out
    _assert_pairing_complete(out)


def test_drop_dangling_tail_partial_group():
    """B31：多 tool_calls 只回了一部分 → 整组删除"""
    msgs = [
        _msg("user", "q"),
        _msg("assistant", "old"),
        _msg("assistant", "", tool_calls=[_tc("c1"), _tc("c2")]),
        _msg("tool", "r1", tool_call_id="c1"),           # 缺 c2
    ]
    out = _drop_dangling_tail(msgs)
    assert len(out) == 2, out
    _assert_pairing_complete(out)


def test_drop_dangling_tail_orphan_tool():
    """B31：尾部孤立 tool（对应 assistant 已丢）必须删除"""
    msgs = [
        _msg("user", "q"),
        _msg("assistant", "old"),
        _msg("tool", "r1", tool_call_id="c1"),
    ]
    out = _drop_dangling_tail(msgs)
    assert len(out) == 2, out


def test_drop_dangling_tail_keeps_complete():
    """配对完整时不得误删"""
    msgs = [
        _msg("user", "q"),
        _msg("assistant", "", tool_calls=[_tc("c1"), _tc("c2")]),
        _msg("tool", "r1", tool_call_id="c1"),
        _msg("tool", "r2", tool_call_id="c2"),
    ]
    assert _drop_dangling_tail(msgs) == msgs


# ============================================================
# 上下文保护 —— 差分对拍与退化输入
# 手工构造的用例只能覆盖「想得到的」形状；切点逻辑一旦退化（比如差分数组
# 边界写错），漏掉的往往正是想不到的形状。这里用暴力参考实现对拍。
# ============================================================

def _pairing_ok(messages):
    """独立实现的协议检查器（刻意不复用被测代码的差分思路）。

    返回 (是否合法, 原因)。合法性 = 每个 assistant 的 tool_calls 都有响应，
    且每个 tool 都能找到它的 assistant。
    """
    need = {}
    for idx, m in enumerate(messages):
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                need[tc["id"]] = idx
        elif m.get("role") == "tool":
            tid = m.get("tool_call_id")
            if tid not in need:
                return False, f"孤立 tool {tid!r} @{idx}"
            del need[tid]
    if need:
        return False, f"悬空 tool_calls {sorted(need)}"
    return True, ""


def _true_window(msgs, s):
    """_truncate_context 实际保留的东西：messages[0] 单独保留 + messages[s:]。

    只检查 msgs[s:] 会把「其 assistant 恰好是 messages[0]」的 tool 误判成孤立。
    """
    return [msgs[0]] + msgs[s:]


def _bf_safe_start(msgs, keep):
    """暴力参考：最大的合法切点 s ∈ [1, max(1,n-keep)]；无解返回 None。

    O(n²)，只用于对拍，不用于生产。
    """
    start0 = max(1, len(msgs) - keep)
    for s in range(start0, 0, -1):
        if _pairing_ok(_true_window(msgs, s))[0]:
            return s
    return None


def _gen_wellformed(rnd, defect=0.0):
    """生成对话：默认每个 assistant 的 tool_calls 都被紧随其后的 tool 全部应答。

    defect=0  → 良构（等价于生产中的稳态：每轮追加完整的 tool 组）
    defect>0  → 按概率注入真实故障：中断的组、孤立 tool
    """
    msgs = [_msg("user", "任务锚点")]
    for i in range(rnd.randint(1, 8)):
        if rnd.random() < 0.30:
            msgs.append(_msg("assistant", "思考文本"))
            continue
        k = rnd.randint(1, 3)
        msgs.append(_msg("assistant", "", tool_calls=[_tc(f"g{i}_{j}") for j in range(k)]))
        n_resp = k if rnd.random() >= defect else rnd.randint(0, max(0, k - 1))
        for j in range(n_resp):
            msgs.append(_msg("tool", "结果", tool_call_id=f"g{i}_{j}"))
        if rnd.random() < defect * 0.5:
            msgs.append(_msg("tool", "结果", tool_call_id=f"orphan{i}"))
    return msgs


def test_safe_window_start_matches_bruteforce_wellformed():
    """差分对拍（良构 3000 例）：切点必须与暴力解一致、协议合法、且最小。

    三个性质缺一不可：
      一致   —— 结果是暴力解的同一个 s
      合法   —— 用独立检查器验真实窗口 [messages[0]] + messages[s:]
      最小   —— 结果与理想起点之间不存在合法 s（不得比必要多丢历史）
    良构输入下「无解」应恒为 0：否则说明切点算法存在无法处理的常态形状。
    """
    rnd = random.Random(20260913)
    nosol = 0
    for _ in range(3000):
        msgs = _gen_wellformed(rnd, defect=0.0)
        keep = rnd.randint(1, 8)
        got = _safe_window_start(msgs, keep)
        want = _bf_safe_start(msgs, keep)
        roles = [m.get("role") for m in msgs]

        assert want is not None, f"良构输入竟无合法切点: keep={keep} roles={roles}"
        assert got == want, f"与暴力解不一致: keep={keep} got={got} want={want} roles={roles}"
        ok, why = _pairing_ok(_true_window(msgs, got))
        assert ok, f"结果协议非法: keep={keep} s={got} {why} roles={roles}"
        start0 = max(1, len(msgs) - keep)
        for s in range(got + 1, start0 + 1):
            assert not _pairing_ok(_true_window(msgs, s))[0], (
                f"不最小：s={s} 也合法，却丢了更多历史 (got={got}) keep={keep} roles={roles}"
            )
    assert nosol == 0


def test_safe_window_start_matches_bruteforce_with_defects():
    """差分对拍（注入缺陷 3000 例）：残缺组/孤立 tool 下，只要有解就必须一致且合法。

    缺陷输入可能真的「无解」（任何切点都会切断某组配对）——这是上游异常残留，
    生产由 _drop_dangling_tail 在出口处清理尾部。本测试只约束「有解时」的行为，
    并记录无解比例，防止未来改动悄悄放大这一比例。
    """
    rnd = random.Random(20260913)
    nosol = 0
    checked = 0
    for _ in range(3000):
        msgs = _gen_wellformed(rnd, defect=0.25)
        keep = rnd.randint(1, 8)
        got = _safe_window_start(msgs, keep)
        want = _bf_safe_start(msgs, keep)
        roles = [m.get("role") for m in msgs]

        if want is None:
            nosol += 1
            continue
        checked += 1
        assert got == want, f"与暴力解不一致: keep={keep} got={got} want={want} roles={roles}"
        ok, why = _pairing_ok(_true_window(msgs, got))
        assert ok, f"结果协议非法: keep={keep} s={got} {why} roles={roles}"

    assert checked > 0, "对拍未真正执行"
    assert nosol < 3000 * 0.6, f"无解比例异常膨胀: {nosol}/3000"


def _tail_defect(rnd, body):
    """构造上游异常残留在**尾部**的三种故障（真实发生位置）。

    孤立 tool 必须先确保它自成一段尾部 tool 游程 —— 若它紧跟在「完整组的 tool 响应」
    后面，两者会连成同一段游程，落进 _drop_dangling_tail 的一个已知缺口（见文件末尾
    「已知缺口」注释）：该分支只校验 asked ⊆ answered，不校验 answered 中的多余项。
    这里用一条无 tool_calls 的 assistant 把游程隔开，使测试只覆盖实现真正承诺的契约。
    """
    kind = rnd.randint(0, 2)
    if kind == 0:      # 悬空 assistant：有 tool_calls，无任何响应
        return [_msg("assistant", "", tool_calls=[_tc("tail_d")])]
    if kind == 1:      # 残缺组：2 个 tool_calls 只回 1 条
        return [_msg("assistant", "", tool_calls=[_tc("tail_p1"), _tc("tail_p2")]),
                _msg("tool", "结果", tool_call_id="tail_p1")]
    # 孤立 tool：对应 assistant 已被切掉
    sep = [] if (not body or body[-1].get("role") != "tool") else [_msg("assistant", "分隔")]
    return sep + [_msg("tool", "结果", tool_call_id="tail_orphan")]


def test_drop_dangling_tail_is_idempotent():
    """幂等：清理过的数组再清理一次必须不变（每轮都调用，不该反复抖动）。

    注意不断言「全数组配对完整」——按设计中部配对由 _safe_window_start 保证，
    _drop_dangling_tail 只负责尾部。这里刻意混入中部孤立 tool 来验证它不会
    越权改动中部内容，同时保持幂等。
    """
    rnd = random.Random(7)
    for _ in range(500):
        msgs = _gen_wellformed(rnd, defect=0.3)
        once = _drop_dangling_tail(list(msgs))
        twice = _drop_dangling_tail(list(once))
        assert once == twice, "非幂等"


def test_drop_dangling_tail_cleans_tail_defects():
    """良构主体 + 尾部故障 → 清理后全数组配对必须完整。

    这是生产真实形态：正常轮次累积出完整对话，某一轮被上游异常（工具超时、
    任务取消、输出截断）打断，在尾部留下残缺分组。清理不干净会直接 400。
    """
    rnd = random.Random(11)
    for _ in range(500):
        body = _gen_wellformed(rnd, defect=0.0)
        msgs = body + _tail_defect(rnd, body)
        out = _drop_dangling_tail(list(msgs))
        _assert_pairing_complete(out)
        assert out, "不应删空（主体是良构的）"
        assert out[0] is msgs[0], "首条（任务锚点）被误删"


def test_drop_dangling_tail_degenerate_inputs():
    """退化输入不得抛异常，且不得产出协议非法数组。

    「全 tool」「只有悬空 assistant」会整体删空——这是正确的：没有对应的
    assistant 就一条都不该留；返回空数组由上层决定如何处理。
    """
    cases = {
        "空": ([], 0),
        "仅 user": ([_msg("user", "q")], 1),
        "全 tool": ([_msg("tool", "r", tool_call_id="x")], 0),
        "只有悬空 assistant": ([_msg("assistant", "", tool_calls=[_tc("a")])], 0),
        "完整组": ([_msg("user", "q"),
                    _msg("assistant", "", tool_calls=[_tc("a")]),
                    _msg("tool", "r", tool_call_id="a")], 3),
    }
    for name, (msgs, want_len) in cases.items():
        out = _drop_dangling_tail(list(msgs))
        assert len(out) == want_len, f"{name}: 期望 {want_len} 条，实际 {len(out)}"
        _assert_pairing_complete(out)


def test_truncate_context_summary_role_is_user():
    """B32：压缩摘要必须是 user 角色（对话中段的 system 会被部分后端拒绝）"""
    class FakeLLM:
        async def chat(self, system_prompt, user_message, **kw):
            return "压缩摘要"

    msgs = _build_tool_loop_messages()
    new_msgs, _ = asyncio.run(_truncate_context(
        msgs, total_chars=10 ** 6, max_chars=100,
        llm=FakeLLM(), emit=lambda e: None, round_num=1, context_warned=False,
    ))
    summaries = [m for m in new_msgs if str(m.get("content", "")).startswith(SUMMARY_PREFIX)]
    assert summaries, "未生成摘要"
    for m in summaries:
        assert m["role"] == "user", f"摘要角色应为 user，实际 {m['role']}"
    # 除首条外不允许出现 system 消息
    assert all(m.get("role") != "system" for m in new_msgs[1:]), new_msgs


def test_truncate_context_drops_dangling_tail():
    """B31 集成：_truncate_context 出口处必须清理尾部悬空 tool_calls"""
    class FailLLM:
        async def chat(self, *a, **kw):
            raise RuntimeError("LLM 不可用")

    msgs = _build_tool_loop_messages(rounds=3)
    msgs.append(_msg("assistant", "", tool_calls=[_tc("dangling")]))  # 上游异常残留
    new_msgs, _ = asyncio.run(_truncate_context(
        msgs, total_chars=10 ** 6, max_chars=100,
        llm=FailLLM(), emit=lambda e: None, round_num=1, context_warned=False,
    ))
    _assert_pairing_complete(new_msgs)


def test_kb_presearch_keeps_task_anchor_at_index_zero():
    """B43：KB 预搜必须追加到任务根消息，不能 insert 一条 system 把锚点挤到 [1]。

    否则 _truncate_context 保护的是 KB 摘要，真正的任务根消息反而会被压缩掉；
    且对话中出现 system 消息部分后端会 400。
    """
    from researcher.agent import Level2Agent

    captured = {}

    class FakeMsg:
        content = "报告"
        tool_calls = None

    class FakeLLM:
        trace = None

        async def chat_with_tools(self, system_prompt, messages, tools):
            captured["messages"] = [dict(m) for m in messages]
            return FakeMsg()

        async def chat(self, system_prompt, user_message, **kw):
            return "报告正文"

    class FakeKB:
        _trace = None

        def search(self, q, user_id=None, doc_ids=None, mode=None):
            return "知识库里有：某文档提到 X"

    # 隔离掉真实 SearchTool：它构造时会 new 一个 LLMClient / TavilyClient，
    # 没有 API Key 的环境下直接抛 OpenAIError → 这个纯逻辑单测变成"环境依赖"。
    # 单测不该需要凭据，这里打桩。
    import researcher.agent as agent_mod

    class StubSearchTool:
        def __init__(self, *a, **kw):
            self.trace = None

    saved = agent_mod.SearchTool
    agent_mod.SearchTool = StubSearchTool
    try:
        agent = Level2Agent(llm=FakeLLM(), kb_enabled=True, search_mode="hybrid")
        agent.kb = FakeKB()
        asyncio.run(agent.run("测试问题"))
    finally:
        agent_mod.SearchTool = saved

    msgs = captured["messages"]
    assert msgs[0]["role"] == "user", f"任务锚点被挤走: {msgs[0]}"
    assert "测试问题" in msgs[0]["content"], "任务根消息内容丢失"
    assert "[知识库预检索]" in msgs[0]["content"], "KB 摘要未注入"
    assert all(m.get("role") != "system" for m in msgs), "messages 中不应出现 system 消息"


def test_normalize_queries_string_not_split():
    """回归：queries 是字符串时必须当成「一条查询」，绝不能逐字符拆开。

    事故现场（2026-09-10）：LLM 违反 schema 传 {"queries": "AI 未来十年发展趋势"}，
    `for q in queries` 逐字符迭代 → 一条查询变成 40 次单字符搜索，Tavily/DDG 全失败，
    整轮搜索报销 + 白烧 40 次请求（把额度打空）。
    """
    from researcher.search import normalize_queries

    got = normalize_queries("AI 未来十年发展趋势")
    assert got == ["AI 未来十年发展趋势"], got
    assert len(got) == 1, f"被拆成了 {len(got)} 条搜索"


def test_normalize_queries_types_and_edges():
    """归一规则：str→[str]、strip、去空与非字符串、非法类型→[]"""
    from researcher.search import normalize_queries

    assert normalize_queries(["a", "b"]) == ["a", "b"]
    assert normalize_queries(["  a  ", "b "]) == ["a", "b"], "应 strip"
    assert normalize_queries(["a", "", "   ", None, 3]) == ["a"], "应去空与非字符串"
    assert normalize_queries(("a", "b")) == ["a", "b"], "应容忍 tuple"
    assert normalize_queries(None) == []
    assert normalize_queries(123) == []
    assert normalize_queries([]) == []


# ============================================================
# 已知缺口（离线探针 2026-09-13 发现，尚未修复，故不作为断言）
# ============================================================
#
# 缺口 1：_drop_dangling_tail 不清理混进完整组的孤立 tool
#
#   最小复现（尾部 tool 游程把两者连成一段）：
#       [..., assistant(tool_calls=[g]), tool(g), tool(orphan)]
#   i 回退到 assistant(g)，asked={g} ⊆ answered={g, orphan} → 判定完整、原样返回，
#   orphan 存活 → 下一轮 chat_with_tools 400 invalid_request_error。
#   该分支只校验 asked ⊆ answered，未校验 answered 中是否存在多余项。
#
# 缺口 2：_truncate_context 不保证把总量压到 max_chars 以下
#
#   a) 丢弃单位是「消息条数」(keep=5) 而非「体积」。一条 tool 结果可达
#      MAX_RESULTS_CHARS(300000)，即整个 MAX_HISTORY_CHARS(1000000) 的 30%，
#      保留 5 条巨型消息必然超标。实测：600K x 5 轮 → 3,600,986 压到 2,400,692
#      仍超 1,400,692。
#   b) _safe_window_start 返回 1 时整段压缩退化为 no-op：old_msgs = messages[1:1]
#      为空 → 不压缩；硬截断 head=messages[:1] 且 start2=1 →
#      messages[:1] + messages[1:] 与原数组相同。实测 300K x 3 轮：
#      1,200,606 → 1,200,606（一字未减），但仍已发出 80% 告警。
#   c) 无失败出口：压不下去只 emit 一句「上下文仍接近上限（已用 X%，X 可 >100）」，
#      随后仍把超预算 payload 发给模型。真实模型将返回 context_length_exceeded。
#
# 缺口 3：压缩链是 lossy-on-lossy
#
#   第二次及以后的压缩，输入里含的是上一版 [早期对话摘要]（已实测确认），
#   原始消息早已不在窗口内；摘要文本还自带「请在后续回答中继续遵守…」的元指令，
#   会被反复再摘要。信息单调衰减且不可恢复，连续触发时每轮白烧一次 LLM 调用。
#
# 缺口 4：record_compress 是死代码
#
#   trace.py 定义了 record_compress，但全项目零调用 → 轨迹评测
#   trajectory_eval.py 统计的 compress_events 结构性恒为 0，显示出来像
#   「压缩 0 次，一切正常」。实测：全部 89 份 report 中
#   「<!-- 上下文压缩记录 -->」零命中 —— 压缩在历史运行中一次都没成功触发过。


# ============================================================
# agent.py —— Level 4 Supervisor 工具分发的配对不变量
# ============================================================
# Level 4 与 Level 2 是两套独立的工具分发代码。Level 2 一直为每种失败补一条
# tool 响应，Level 4 曾在这三处都不补：
#   ① arguments 不是合法 JSON          → 只 print + continue
#   ② 工具名未识别                     → 两个 elif 都不命中，静默丢弃
#   ③ ConductResearch 缺 research_topic → args["research_topic"] 直接 KeyError
# 而带全部 tool_calls 的 assistant 消息此时已经入库，于是该 tool_call 永远悬空
# → 下一轮 chat_with_tools 直接 400 invalid_request_error。
#
# 雪上加霜的是 Level 4 只在 total_chars 超限时才调 _truncate_context，
# 而清理悬空尾部的 _drop_dangling_tail 挂在它末尾 → 未超限的轮次毫无兜底。
#
# 修法：无条件调用 _truncate_context + 三条失败路径都回填 tool 响应。
# 这个测试用假 LLM 把三条路径一次性触发，断言交给下一轮的消息配对完整。


class _Fn:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _TC:
    def __init__(self, tid, name, arguments):
        self.id = tid
        self.function = _Fn(name, arguments)


class _SupMsg:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


def test_level4_supervisor_backfills_every_tool_response():
    """Supervisor 必须为每个 tool_call 回填 tool 响应，否则下一轮 400。"""
    from researcher.agent import Level4Agent

    captured: list[list[dict]] = []

    class FakeLLM:
        trace = None

        async def chat(self, system_prompt, user_message, **kw):
            return "研究简报"          # 供 _generate_research_brief

        async def chat_with_tools(self, system_prompt, messages, tools):
            captured.append([dict(m) for m in messages])
            if len(captured) == 1:
                return _SupMsg("", [
                    _TC("ok", "think_tool", '{"reflection": "先规划"}'),
                    _TC("badjson", "think_tool", "{这不是合法 JSON"),
                    _TC("unknown", "NoSuchTool", '{"x": 1}'),
                    _TC("notopic", "ConductResearch", '{}'),
                ])
            return _SupMsg("信息足够", None)   # 无 tool_calls → 结束循环

    # llm 可注入（与 Level2Agent 一致）→ 这个测试不需要凭据
    agent = Level4Agent(llm=FakeLLM(), on_progress=lambda e: None, search_mode="web_only")
    asyncio.run(agent.run("测试问题"))

    assert len(captured) >= 2, f"至少两轮才能检验配对，实际 {len(captured)} 轮"
    for msgs in captured:
        _assert_pairing_complete(msgs)
    answered = {m.get("tool_call_id") for m in captured[1] if m.get("role") == "tool"}
    assert {"ok", "badjson", "unknown", "notopic"} <= answered, f"缺少 tool 响应: {answered}"


# ============================================================
# kb._fmt / parse_source_docs —— 检索结果的格式契约
# ============================================================
# 这两个函数是一对逆运算，而且是**所有检索指标的入口**：
# retriever_test / e2e_diagnostic / run_regression 都靠解析 _fmt 的输出来判定命中。
#
# 它们曾经分居两个文件、各自手写解析，于是漂移了：
#   _fmt 输出的是**全角**「（相关度 78%）」，
#   e2e_diagnostic 却只 strip 半角 "(" → 解析出的"文档名"变成
#   "doc1.txt （相关度 78%）"，与 expected_docs 永不相等 → 命中率假性归零。
#   只有 hybrid 模式（doc 不带 distance/rerank_score → 无标注）侥幸正确，
#   所以这个 bug 被"hybrid 下 73% 看着合理"掩盖了很久。
# 现在格式与解析同处 kb.py，并有下面的单测钉住，避免再次漂移。


def _rdoc(content="正文", doc_id="doc1.txt", distance=None, rerank_score=None):
    d = {"content": content, "meta": {"doc_id": doc_id}}
    if distance is not None:
        d["distance"] = distance
    if rerank_score is not None:
        d["rerank_score"] = rerank_score
    return d


def test_fmt_empty_returns_not_found():
    assert KnowledgeBase._fmt([]) == "知识库中未找到相关信息。"


def test_fmt_distance_shows_relevance():
    out = KnowledgeBase._fmt([_rdoc(distance=0.9)])
    assert "来源 1: doc1.txt （相关度 10%）---" in out, out


def test_fmt_rerank_zero_is_not_falsy():
    """rerank_score=0.0 是合法分数，不能被真值判断吞掉。

    实测过的 bug：`if r:` 让 0.0 落到 distance 分支，于是「精排判为最不相关」
    被显示成「相关度 10%」—— 那是**另一个文档的向量距离**换算来的分数。
    """
    out = KnowledgeBase._fmt([_rdoc(rerank_score=0.0, distance=0.9)])
    assert "（精排 0.0%）" in out, out
    assert "相关度" not in out, out


def test_fmt_rerank_takes_priority_over_distance():
    out = KnowledgeBase._fmt([_rdoc(rerank_score=0.85, distance=0.9)])
    assert "（精排 85.0%）" in out, out
    assert "相关度" not in out, out


def test_fmt_hybrid_has_no_annotation():
    """hybrid 的 doc 既无 distance 也无 rerank_score → 不应出现相似度标注。"""
    out = KnowledgeBase._fmt([_rdoc()])
    assert "来源 1: doc1.txt ---" in out, out


def test_fmt_label_goes_into_header():
    out = KnowledgeBase._fmt([_rdoc()], "（混合检索）")
    assert out.startswith("# 知识库检索结果（混合检索）"), out


def test_parse_source_docs_strips_fullwidth_annotation():
    """全角括号标注必须能被剥离 —— e2e_diagnostic 假性归零的根因。"""
    text = "--- 来源 1: doc1.txt （相关度 78%）---"
    assert parse_source_docs(text) == ["doc1.txt"], parse_source_docs(text)


def test_parse_source_docs_strips_rerank_annotation():
    text = "--- 来源 1: doc1.txt （精排 85.0%）---"
    assert parse_source_docs(text) == ["doc1.txt"], parse_source_docs(text)


def test_parse_source_docs_strips_halfwidth_annotation():
    text = "--- 来源 1: doc1.txt (相关度 78%)---"
    assert parse_source_docs(text) == ["doc1.txt"], parse_source_docs(text)


def test_parse_source_docs_without_annotation():
    text = "--- 来源 1: doc1.txt ---"
    assert parse_source_docs(text) == ["doc1.txt"], parse_source_docs(text)


def test_parse_source_docs_multiple_and_order():
    text = (
        "# 知识库检索结果（全链路）\n"
        "\n--- 来源 1: a.txt （精排 91.0%）---\n正文A\n"
        "\n--- 来源 2: b.txt （精排 80.0%）---\n正文B\n"
    )
    assert parse_source_docs(text) == ["a.txt", "b.txt"], parse_source_docs(text)


def test_parse_source_docs_keeps_filename_with_parens():
    """文件名自身以括号结尾时不能被误剥（只剥含 % 或已知关键词的标注）。"""
    text = "--- 来源 1: 报告(最终版) ---"
    assert parse_source_docs(text) == ["报告(最终版)"], parse_source_docs(text)


def test_parse_source_docs_ignores_header_and_not_found():
    assert parse_source_docs("知识库中未找到相关信息。") == []
    assert parse_source_docs("# 知识库检索结果（混合检索）\n\n正文") == []


def test_fmt_parse_roundtrip():
    """_fmt → parse_source_docs 往返无损：格式契约的核心不变量。"""
    docs = [
        _rdoc(doc_id="a.txt", distance=0.8),
        _rdoc(doc_id="b.txt", rerank_score=0.0),
        _rdoc(doc_id="c.txt"),
    ]
    assert parse_source_docs(KnowledgeBase._fmt(docs)) == ["a.txt", "b.txt", "c.txt"]


# ============================================================
# evaluation/ —— 检索指标与命中判定
# ============================================================
# 评测模块此前**一个单测都没有**（agent/tests 下没有任何文件 import researcher.evaluation）。
# 而它算出来的正是被写进 README / 简历 / 面试话术的那些数字，
# 所以口径漂移没有任何东西拦得住：
#   · run_regression 的运行路径与 --update-baseline 各写了一份判定，已经不一致
#   · no_answer 题的 MRR 在两条路径下处理不同（一条只在拒答时记 1.0，
#     另一条无条件记 1.0）→ 基准被凭空抬高
#   · semantic_hit 的 SEMANTIC_THRESHOLD = 0.8 从来没被引用，实际跑的是 0.5


def test_ir_metrics_precision_recall_mrr():
    """retriever_test 的三个传统 IR 指标（文档名口径）。"""
    from researcher.evaluation.retriever_test import precision_at_k, recall_at_k, mrr

    ret = ["a", "b", "c"]      # 检索顺序
    rel = {"b"}                # 只有 b 相关
    assert precision_at_k(rel, ret, 3) == 1 / 3
    assert precision_at_k(rel, ret, 5) == 1 / 5   # 分母固定为 k，不是 len(ret)
    assert recall_at_k(rel, ret, 5) == 1.0
    assert mrr(rel, ret) == 1 / 2                 # b 排第 2 位
    assert mrr({"z"}, ret) == 0.0                 # 无相关文档
    assert recall_at_k(set(), ret, 5) == 0.0      # 空 relevant 不应除零


def test_calc_mrr_is_keyword_in_block():
    """run_regression._calc_mrr 按「关键词是否出现在该来源块」判定。

    与 retriever_test.mrr 同名但不同义（一个是 chunk 关键词口径、
    一个是文档名口径），不可互换 —— 见其 docstring。
    """
    from researcher.evaluation.run_regression import _calc_mrr

    text = (
        "--- 来源 1: a.txt ---\n无关内容\n"
        "--- 来源 2: b.txt ---\n这里有 Guido\n"
        "--- 来源 3: c.txt ---\n另一段\n"
    )
    assert _calc_mrr(text, ["Guido"]) == 1 / 2
    assert _calc_mrr(text, ["不存在的词"]) == 0.0
    assert _calc_mrr("没有来源行", ["Guido"]) == 0.0
    assert _calc_mrr("", ["Guido"]) == 0.0


def test_classify_retrieval_four_kinds():
    """classify_retrieval 的四类判定；no_answer 题不参与 MRR。"""
    from researcher.evaluation.run_regression import classify_retrieval

    class FakeHit:
        def __init__(self, found):
            self.found = found

        def check(self, question, expected, result):
            return self.found, 1.0, "literal" if self.found else "miss"

    na = {"type": "no_answer", "question": "库外问题", "expected_chunks": ["未找到"]}

    # ① no_answer + 正确拒答
    r = classify_retrieval(na, "知识库中未找到相关信息。", FakeHit(False))
    assert r["kind"] == "refused" and r["hit"] is True and r["mrr"] is None

    # ② no_answer + 该拒答却返回了内容
    r = classify_retrieval(na, "--- 来源 1: a.txt ---\n有些内容", FakeHit(False))
    assert r["kind"] == "not_refused" and r["hit"] is False and r["mrr"] is None
    assert r["missing"] == ["应返回未找到但实际有结果"]

    ans = {"type": "simple", "question": "Q", "expected_chunks": ["Guido"]}
    hit_text = "--- 来源 1: a.txt ---\nGuido van Rossum"

    # ③ 有答案 + 命中
    r = classify_retrieval(ans, hit_text, FakeHit(True))
    assert r["kind"] == "hit" and r["hit"] is True
    assert r["mrr"] == 1.0 and r["missing"] == []

    # ④ 有答案 + 未命中（missing 列出缺的词）
    r = classify_retrieval(ans, "--- 来源 1: a.txt ---\n无关", FakeHit(False))
    assert r["kind"] == "miss" and r["hit"] is False
    assert r["mrr"] == 0.0 and r["missing"] == ["Guido"]


# ============================================================
# kb —— BM25 索引缓存
# ============================================================
# 缓存带来的唯一风险是「文档更新后仍检索旧内容」。这里用 __new__ 绕过 __init__
# （避免建真实 Chroma 客户端），只验证缓存的状态机：命中 / 失效 / 上限。


def _fresh_kb():
    from researcher.kb import KnowledgeBase

    kbx = KnowledgeBase.__new__(KnowledgeBase)
    kbx._bm25_cache = {}
    kbx._kb_generation = {}
    kbx._bm25_hits = 0
    kbx._bm25_misses = 0
    return kbx


def test_bm25_cache_hit_returns_same_index():
    kbx = _fresh_kb()
    kbx._get_v2_docs = lambda uid: [{"content": "Python 由 Guido 创建。", "meta": {"doc_id": "a"}}]

    a = kbx._get_bm25("u1")
    b = kbx._get_bm25("u1")
    assert a is not None and a is b, "同一代际下应复用同一个索引对象"
    assert (kbx._bm25_hits, kbx._bm25_misses) == (1, 1)
    assert kbx.bm25_cache_stats()["hit_rate"] == "50.0%"


def test_bm25_cache_invalidated_on_write():
    """写操作后代际 +1 → 必须重建，不能复用旧索引（否则新文档搜不到）。"""
    kbx = _fresh_kb()
    kbx._get_v2_docs = lambda uid: [{"content": "旧内容", "meta": {"doc_id": "a"}}]

    first = kbx._get_bm25("u1")
    kbx._invalidate_bm25("u1")
    second = kbx._get_bm25("u1")
    assert second is not first, "失效后应重建"
    assert kbx._kb_generation["u1"] == 1
    assert kbx._bm25_misses == 2

    # 连续两次失效也应各自生效（代际是累加的，不是布尔开关）
    kbx._invalidate_bm25("u1")
    assert kbx._kb_generation["u1"] == 2
    third = kbx._get_bm25("u1")
    assert third is not second


def test_bm25_cache_empty_kb_returns_none():
    kbx = _fresh_kb()
    kbx._get_v2_docs = lambda uid: []
    assert kbx._get_bm25("nobody") is None
    # 空库不应被记成 miss（没有可缓存的东西），也不该写进缓存
    assert kbx._bm25_cache == {}


def test_bm25_cache_bounded_across_users():
    """索引常驻内存，条目数必须封顶 —— 否则随上传用户数线性吃内存。"""
    kbx = _fresh_kb()
    kbx._get_v2_docs = lambda uid: [{"content": f"内容 {uid}", "meta": {"doc_id": uid}}]

    for i in range(kbx._BM25_CACHE_MAX + 2):
        kbx._get_bm25(f"u{i}")

    assert len(kbx._bm25_cache) == kbx._BM25_CACHE_MAX
    assert "u0" not in kbx._bm25_cache, "最早的条目应被淘汰"
    assert f"u{kbx._BM25_CACHE_MAX + 1}" in kbx._bm25_cache, "最新的条目应保留"


# ============================================================
# retrievers —— BM25 的词法相关性闸
# ============================================================


def test_bm25_only_returns_positive_score_docs():
    """BM25 只返回分数 > 0 的文档：零分 = 与查询没有任何词项重叠。

    为什么触发得很少（值得记住的机制）：jieba 会把空格切成一个 token，
    而空格几乎出现在每个文档里 → rank_bm25 的 epsilon 机制给这种
    「语料中过半文档都有」的词项一个较小的**正** idf → 含空格的查询
    会让所有文档都得分 > 0。所以这道闸只在**纯中文查询**（无空格 token）
    且部分文档确实零重叠时才起作用 —— 实测 130 题里只有 2 条。
    """
    from researcher.retrievers.bm25_retriever import BM25Retriever, _tokenize

    docs = [
        {"page_content": "分布式系统共识算法"},
        {"page_content": "数据库索引优化"},
        {"page_content": "网络协议分层"},
    ]
    bm = BM25Retriever(docs, k=10)

    q = "分布式系统共识算法"
    scores = bm._bm25.get_scores(_tokenize(q))
    positive = [i for i, s in enumerate(scores) if s > 0]
    got = [d["page_content"] for d in bm.invoke(q)]

    # 契约：返回的正好是「分数 > 0」的那些文档
    assert got == [docs[i]["page_content"] for i in positive], (got, positive)
    # 且确实剔除了零重叠的文档（否则这个测试等于没测）
    assert len(got) < len(docs), f"应有文档被剔除，实际全返回: {got}"

    # 空语料不应崩
    assert BM25Retriever([], k=10).invoke("任意查询") == []


# ============================================================
# retrievers —— 查询改写的容错解析
# ============================================================
# 这个环节曾因为「只认裸数组」而**静默失效 73%**（详见 query_rewriter 的 docstring）。
# 解析是纯函数，用离线用例把实测观测到的各种形态钉住 ——
# 其中「模型把 response_format 原样回显成 content」是最反直觉的一种。


def test_query_rewriter_parse_shapes():
    from researcher.retrievers.query_rewriter import QueryRewriter

    cases = [
        ('["a","b","c"]', ["a", "b", "c"]),                          # 裸数组（不传 response_format 时的正常形态）
        ('{"queries": ["a","b","c"]}', ["a", "b", "c"]),             # json_object 包装
        ('{"type": "json_object", "content": ["a","b"]}', ["a", "b"]),  # response_format 被原样回显
        ('{"data": {"queries": ["a"]}}', ["a"]),                     # 嵌套两层
        ('好的，结果如下：\n["a","b"]\n希望有帮助', ["a", "b"]),          # 前后有散文
        ('{"foo": ["a","b"]}', ["a", "b"]),                          # 键名未知 → 兜底遍历
        ('["  a  ", "", "b"]', ["a", "b"]),                          # strip + 去空
    ]
    for raw, want in cases:
        got = QueryRewriter._parse(raw)
        assert got == want, f"{raw!r} → {got!r}，期望 {want!r}"


def test_query_rewriter_parse_gives_up_cleanly():
    """挖不到时返回 None（调用方据此回退原问题），不抛异常、不返回垃圾。"""
    from researcher.retrievers.query_rewriter import QueryRewriter

    for raw in ('{"type": "json_object"}', "我无法完成这个任务", "",
                None, "[]", '{"queries": []}', '{"queries": [1, 2]}'):
        assert QueryRewriter._parse(raw) is None, f"{raw!r} 应为 None"


# ============================================================
# answer_check —— 拒答闸的失败姿态（离线可测）
# ============================================================


def test_answer_check_empty_context_is_no_answer():
    """没检索到任何内容 = 没答案，不该去调 LLM。"""
    from researcher.answer_check import doc_has_answer

    assert doc_has_answer("q", []) is False
    assert doc_has_answer("q", ["   "]) is False
    assert doc_has_answer("q", ["", ""]) is False


def test_answer_check_failure_posture():
    """判定服务不可用时的姿态：生产侧 fail-open、评测侧 fail-closed。

    两者取舍相反且都是刻意的，所以做成显式参数：
      · 生产（kb.search 的拒答闸）宁可多答不可误拒 —— 判定抖动不该让
        整个知识库变成"什么都答不了"
      · 评测（faithfulness 的 no_answer 分支）沿用原语义：判不出来就不算
        "文档有答案"
    """
    import os

    from researcher.answer_check import doc_has_answer

    saved = os.environ.get("DEEPSEEK_BASE_URL")
    # 指向一个必定连不上的端口，制造调用失败（不产生任何计费）
    os.environ["DEEPSEEK_BASE_URL"] = "http://127.0.0.1:9/no-listener"
    try:
        assert doc_has_answer("q", ["一些文档内容"], default_on_error=True) is True
        assert doc_has_answer("q", ["一些文档内容"], default_on_error=False) is False
    finally:
        if saved is None:
            os.environ.pop("DEEPSEEK_BASE_URL", None)
        else:
            os.environ["DEEPSEEK_BASE_URL"] = saved


# ============================================================
# 运行
# ============================================================

if __name__ == "__main__":
    tests = [
        test_chunk_short_paragraph,
        test_chunk_long_splits_by_sentence,
        test_chunk_multiple_paragraphs,
        test_chunk_empty,
        test_chunk_size_never_exceeds_limit,
        test_chunk_overlap_not_less_than_chunk_size,
        test_chunk_min_size_larger_than_chunk_size,
        test_chunk_overlap_only_applies_in_char_fallback,
        test_read_txt_file,
        test_read_md_file,
        test_read_file_not_found,
        test_read_unsupported_extension,
        test_url_dedup,
        test_markdown_link_pattern,
        test_search_result_format,
        test_source_label_citation,
        test_chinese_detection,
        test_safe_window_start_plain_window,
        test_safe_window_start_orphan_tool,
        test_safe_window_start_aligned_assistant,
        test_safe_window_start_multi_tool_calls,
        test_safe_window_start_dangling_assistant,
        test_safe_window_start_large_input_is_fast,
        test_drop_dangling_tail_dangling_assistant,
        test_drop_dangling_tail_partial_group,
        test_drop_dangling_tail_orphan_tool,
        test_drop_dangling_tail_keeps_complete,
        test_safe_window_start_matches_bruteforce_wellformed,
        test_safe_window_start_matches_bruteforce_with_defects,
        test_drop_dangling_tail_is_idempotent,
        test_drop_dangling_tail_cleans_tail_defects,
        test_drop_dangling_tail_degenerate_inputs,
        test_truncate_context_compressed_pairing_complete,
        test_truncate_context_hard_truncate_pairing_complete,
        test_truncate_context_keeps_summary_on_hard_truncate,
        test_truncate_context_summary_role_is_user,
        test_truncate_context_drops_dangling_tail,
        test_kb_presearch_keeps_task_anchor_at_index_zero,
        test_normalize_queries_string_not_split,
        test_normalize_queries_types_and_edges,
        # agent.py —— Level 4 Supervisor 工具分发配对不变量
        test_level4_supervisor_backfills_every_tool_response,
        # kb._fmt / parse_source_docs —— 检索结果格式契约
        test_fmt_empty_returns_not_found,
        test_fmt_distance_shows_relevance,
        test_fmt_rerank_zero_is_not_falsy,
        test_fmt_rerank_takes_priority_over_distance,
        test_fmt_hybrid_has_no_annotation,
        test_fmt_label_goes_into_header,
        test_parse_source_docs_strips_fullwidth_annotation,
        test_parse_source_docs_strips_rerank_annotation,
        test_parse_source_docs_strips_halfwidth_annotation,
        test_parse_source_docs_without_annotation,
        test_parse_source_docs_multiple_and_order,
        test_parse_source_docs_keeps_filename_with_parens,
        test_parse_source_docs_ignores_header_and_not_found,
        test_fmt_parse_roundtrip,
        # evaluation/ —— 检索指标与命中判定
        test_ir_metrics_precision_recall_mrr,
        test_calc_mrr_is_keyword_in_block,
        test_classify_retrieval_four_kinds,
        # kb —— BM25 索引缓存
        test_bm25_cache_hit_returns_same_index,
        test_bm25_cache_invalidated_on_write,
        test_bm25_cache_empty_kb_returns_none,
        test_bm25_cache_bounded_across_users,
        # retrievers —— BM25 词法相关性闸
        test_bm25_only_returns_positive_score_docs,
        # retrievers —— 查询改写的容错解析
        test_query_rewriter_parse_shapes,
        test_query_rewriter_parse_gives_up_cleanly,
        # answer_check —— 拒答闸
        test_answer_check_empty_context_is_no_answer,
        test_answer_check_failure_posture,
    ]

    passed = 0
    failed = []
    for test in tests:
        try:
            test()
            print(f"  ✅ {test.__name__}")
            passed += 1
        except AssertionError as e:
            failed.append(test.__name__)
            print(f"  ❌ {test.__name__}: {e}")
        except Exception as e:
            # 只捕获 AssertionError 的话，一条测试抛别的异常会让整个 runner 当场中断，
            # 后面的测试一条都不跑 —— 看到的是「第 N 条崩了」而不是「哪几条真的错了」。
            # 异常类型一并打印，用来区分「断言不成立」与「测试自身/被测签名变了」。
            failed.append(test.__name__)
            print(f"  ❌ {test.__name__}: [{type(e).__name__}] {e}")

    print(f"\n  {passed}/{len(tests)} 通过")
    if failed:
        print(f"  失败项: {', '.join(failed)}")
        # 退出码必须反映结果。此前这个 runner 无论失败与否都 exit 0，
        # 于是任何把它挂进 CI / 脚本当门禁的用法都会静默失效。
        sys.exit(1)
