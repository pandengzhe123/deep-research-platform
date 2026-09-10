"""评测体系：RAGAS 四指标 + LLM-as-Judge + A/B 对照 + 消融实验 + 回归测试。

入口脚本：run_eval.py（报告打分）/ ab_compare.py（A/B）/ ablation.py（消融）
/ run_regression.py（回归）/ e2e_diagnostic.py（端到端诊断矩阵）。
保留本文件（而非依赖 PEP 420 隐式命名空间包）：显式普通包不依赖 setuptools
的 namespaces 发现开关，跨版本、跨打包工具都更稳。
"""
