"""Friston-2 第 3 课：独立 Cross-Encoder 重排对照，不接入 NPC。

用法（在项目根目录，已激活 photorobot 环境）：
    python -m friston2.knowledge.rerank_lore "科研理想和伦理争议之间的冲突"
    python -m friston2.knowledge.rerank_lore "赫默为什么反思科研伦理" --candidates 8 --top-k 5

依赖本项目第 2 课已有的 hybrid_ranked_search()、实体解析器和 SQLite 知识库。
首次加载会由 FastEmbed 下载 BAAI/bge-reranker-base 模型（约 1 GB）。
只做检索排序实验，不修改数据库、向量索引、NPC、Agent、设备或会话记忆。
"""
from __future__ import annotations

import argparse
from functools import lru_cache
from time import perf_counter

from friston2.knowledge.entity_resolver import resolve_entity_resolution
from friston2.knowledge.hybrid_search import hybrid_ranked_search
from friston2.knowledge.retrieve_lore import detect_intent
from friston2.knowledge.terra_kb import connect

MODEL_NAME = "BAAI/bge-reranker-base"
DOCUMENT_MAX_CHARS = 650
MAX_CANDIDATES = 20  # CPU 推理成本较高：先用 8 条比较，再看有无必要扩大。


@lru_cache(maxsize=1)
def _reranker():
    """复用一次进程内加载的 ONNX 模型；不影响跨进程命令行运行。"""
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    return TextCrossEncoder(model_name=MODEL_NAME)


def _owners(db, rows):
    owner_ids = list(dict.fromkeys(str(row.get("ref_id", "")) for row in rows if row.get("ref_id")))
    if not owner_ids:
        return {}
    placeholders = ",".join("?" for _ in owner_ids)
    return {
        item["id"]: item["name"]
        for item in db.execute(
            f"SELECT id, name FROM entities WHERE id IN ({placeholders})", owner_ids
        )
    }


def _documents(rows, owners):
    """送给重排器的是带主体的短文本，不把 RRF 分数当成文本。"""
    return [
        f"主题：{owners.get(row.get('ref_id'), '未标明')}。"
        f"栏目：{row.get('section') or '正文'}。"
        f"内容：{str(row.get('content') or '')[:DOCUMENT_MAX_CHARS]}"
        for row in rows
    ]

def rerank_passages(db, question, rows, max_candidates=8):
    """对 Hybrid RAG 已过滤的候选进行重排。"""

    rows = list(rows)
    if len(rows) < 2:
        return rows

    count = max(1, min(MAX_CANDIDATES, max_candidates))
    candidates = rows[:count]

    # 查询每条证据所属的人物或事件
    owners = _owners(db, candidates)

    # 构造“主体 + 栏目 + 正文”的输入
    documents = _documents(candidates, owners)

    # Cross-Encoder 推理
    start = perf_counter()
    scores = list(_reranker().rerank(question, documents))
    elapsed = perf_counter() - start

    if len(scores) != len(candidates):
        raise RuntimeError("Reranker 返回的分数数量不一致")

    # 不改变原有实体约束、出处与检索通道
    reranked = []
    for row, score in zip(candidates, scores):
        item = dict(row)
        item["rerank_score"] = float(score)
        reranked.append(item)

    reranked.sort(
        key=lambda item: item["rerank_score"],
        reverse=True,
    )

    print(f"[Reranker] 已重排 {len(candidates)} 条，耗时 {elapsed:.2f}s")

    return reranked + rows[count:]


def _show(label, rows, *, top_k, score_name):
    print(f"\n===== {label} (前 {top_k} 条) =====")
    for rank, row in enumerate(rows[:top_k], 1):
        score = row.get(score_name)
        score_text = f"{float(score):.4f}" if score is not None else "--"
        channels = "+".join(row.get("retrieval_channels", ["lexical"]))
        path = row.get("source_path") or "未知来源"
        start = row.get("line_start", "?")
        end = row.get("line_end", "?")
        print(f"[{rank}] {score_name}={score_text} | {channels} | {path}:{start}-{end}")
        print("    " + str(row.get("content") or "").replace("\n", " ")[:155])


def compare(question: str, *, candidates: int = 8, top_k: int = 5):
    if not question.strip():
        raise ValueError("检索问题不能为空")
    candidates = max(1, min(MAX_CANDIDATES, int(candidates)))
    top_k = max(1, min(candidates, int(top_k)))

    with connect() as db:
        resolution = resolve_entity_resolution(db, question)
        if resolution["status"] == "ambiguous":
            print("人物称呼存在歧义，请先用完整名称消歧：", resolution["ambiguities"])
            return
        ids = resolution["entity_ids"]
        intent = detect_intent(question)
        if intent in ("relationship", "leadership"):
            print("提示：人物关系应优先由图谱的明确关系边验证；重排分数不能证明人际关系。")

        retrieval_start = perf_counter()
        rows = hybrid_ranked_search(
            db,
            question,
            ids=ids,
            limit=candidates,
            kinds=("profile", "character", "event", "project"),
        )
        retrieval_seconds = perf_counter() - retrieval_start
        owners = _owners(db, rows)

    if not rows:
        print("混合检索没有候选资料：没有执行 Reranker。")
        return

    # 显示严格相同的候选集合，便于观察二阶段排序的作用。
    _show("Hybrid RAG / RRF 原排序", rows, top_k=top_k, score_name="fusion_score")

    documents = _documents(rows, owners)
    print(f"\n将使用 {len(documents)} 条候选进行重排。首次加载会下载约 1 GB 模型。", flush=True)
    model_start = perf_counter()
    model = _reranker()
    model_seconds = perf_counter() - model_start

    rank_start = perf_counter()
    raw_scores = list(model.rerank(question, documents))
    rank_seconds = perf_counter() - rank_start
    if len(raw_scores) != len(rows):
        raise RuntimeError(f"重排分数数量不匹配：预期 {len(rows)}，得到 {len(raw_scores)}")

    ranked = []
    for row, value in zip(rows, raw_scores):
        item = dict(row)
        item["rerank_score"] = float(value)
        ranked.append(item)
    ranked.sort(key=lambda item: item["rerank_score"], reverse=True)

    _show("Cross-Encoder 重排后", ranked, top_k=top_k, score_name="rerank_score")
    print("\n耗时（仅本次运行）：")
    print(f"Hybrid RAG 检索：{retrieval_seconds:.2f}s")
    print(f"Reranker 加载/首次下载：{model_seconds:.2f}s")
    print(f"Cross-Encoder {len(rows)} 条重排：{rank_seconds:.2f}s")
    print("注意：Cross-Encoder 分数只用于同一次问题中的相对排序，不是事实正确率。")
    print("注意：这些 Wiki 二次总结未经原作逐条核验；重排不会自动提高资料可信度。")
    print("提示：本模块尚未接入 NPC 对话，不会改变 Friston-3 当前的回答。")


def main():
    parser = argparse.ArgumentParser(description="Friston-2 第 3 课：RRF 与 Cross-Encoder 排序对照")
    parser.add_argument("question", nargs="+", help="待检索的中文问题")
    parser.add_argument("--candidates", type=int, default=8, help="送入重排器的候选数，1～20；默认 8")
    parser.add_argument("--top-k", type=int, default=5, help="打印对照结果数量，默认 5")
    args = parser.parse_args()
    compare(" ".join(args.question), candidates=args.candidates, top_k=args.top_k)


if __name__ == "__main__":
    main()
