"""单元测试 —— 只测确定性的纯函数，不调 LLM，不联网。

运行: cd D:\deep_research\agent && .venv\Scripts\python tests\test_units.py
"""

import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# ============================================================
# kb.py
# ============================================================

from researcher.kb import chunk_text, read_file


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
    text = "短段落。\n\n" + "长段落。" * 50
    chunks = chunk_text(text, chunk_size=100)
    assert len(chunks) >= 2
    assert chunks[0] == "短段落。"


def test_chunk_empty():
    assert chunk_text("") == []
    assert chunk_text("\n\n\n") == []


def test_chunk_size_never_exceeds_limit():
    text = "A" * 2000
    chunks = chunk_text(text, chunk_size=500, overlap=100)
    for c in chunks:
        assert len(c) <= 600, f"chunk 过长: {len(c)} > 600"


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

from researcher.agent import _safe_window_start, _truncate_context


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
        test_truncate_context_compressed_pairing_complete,
        test_truncate_context_hard_truncate_pairing_complete,
        test_truncate_context_keeps_summary_on_hard_truncate,
    ]

    passed = 0
    for test in tests:
        try:
            test()
            print(f"  ✅ {test.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {test.__name__}: {e}")

    print(f"\n  {passed}/{len(tests)} 通过")
