"""检索器组件：BM25、RRF 融合、CrossEncoder 精排、LLM 查询改写。

由 kb.py 的 hybrid / rerank / full 三种检索模式按需调用。
保留本文件（而非依赖 PEP 420 隐式命名空间包）：显式普通包不依赖 setuptools
的 namespaces 发现开关，跨版本、跨打包工具都更稳。
"""
