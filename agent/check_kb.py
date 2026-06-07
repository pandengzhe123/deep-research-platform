"""查看 Chroma 知识库内容。"""
from researcher.kb import kb

for user in ["default"]:
    try:
        coll = kb._get_collection(user)
        data = coll.get()
        docs = data["documents"] or []
        metas = data["metadatas"] or []
        print(f"用户: {user}")
        print(f"  总片段数: {len(docs)}")
        from collections import Counter

        doc_counts = Counter(m["doc_id"] for m in metas)
        for doc_id, count in doc_counts.items():
            print(f"  📄 {doc_id}: {count} 个片段")
        seen = set()
        for doc, meta in zip(docs, metas):
            did = meta["doc_id"]
            if did not in seen:
                seen.add(did)
                print(f"      示例: {doc[:80]}...")
        print()
    except Exception as e:
        print(f"用户 {user}: 无数据 ({e})")
