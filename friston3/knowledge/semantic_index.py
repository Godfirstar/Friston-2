"""为 NPC 混合检索提供中文语义索引，同时保留独立构建与查询 CLI。

用法：
    python -m friston3.knowledge.semantic_index build
    python -m friston3.knowledge.semantic_index search "为什么凯尔希那么执着" --top-k 5

索引从已构建的 terra_lore.db 读取，不修改原知识库和人物关系。
仅索引非关系性文本；检索命中本身不证明人物交情或剧情事实。
"""
from __future__ import annotations

import argparse
from hashlib import sha256
from functools import lru_cache
import os
from pathlib import Path
import sqlite3
import tempfile

import numpy as np

from friston3.config import KNOWLEDGE_DB, PROJECT_ROOT

MODEL_NAME = "BAAI/bge-small-zh-v1.5"
QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："
INDEX_PATH = PROJECT_ROOT / "data" / "runtime" / "terra_semantic.npz"
BATCH_SIZE = 32


def _signature(path: Path) -> str:
    """数据库内容指纹：防止旧向量错配新的 passage_id。"""
    if not path.is_file():
        raise FileNotFoundError(f"知识库不存在：{path}，请先构建 terra_kb。")
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _connect(db_file: Path):
    db = sqlite3.connect(db_file.resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


@lru_cache(maxsize=1)
def _encoder():
    # 延迟加载：未执行 build/search 时，不占用模型资源。
    try:
        from fastembed import TextEmbedding
    except ImportError as exc:
        raise RuntimeError("未安装 FastEmbed，请运行 python -m pip install fastembed") from exc
    return TextEmbedding(model_name=MODEL_NAME, threads=2)


def _unit_vectors(vectors: np.ndarray) -> np.ndarray:
    """单位化后，点积即余弦相似度。"""
    norms = np.linalg.norm(vectors, axis=-1, keepdims=True)
    return vectors / np.maximum(norms, 1e-12)


def build(db_file: Path = KNOWLEDGE_DB, index_path: Path = INDEX_PATH):
    db_file, index_path = Path(db_file), Path(index_path)
    before = _signature(db_file)
    with _connect(db_file) as db:
        rows = db.execute("""
            SELECT p.id, e.name, p.section, p.content
            FROM passages AS p
            JOIN entities AS e ON e.id = p.ref_id
            WHERE p.kind != 'relationship' AND trim(p.content) != ''
            ORDER BY p.id
        """).fetchall()
    if not rows:
        raise RuntimeError("没有可索引的文本，请先检查 terra_lore.db 的 passages 表。")

    model = _encoder()
    passage_ids = np.asarray([row["id"] for row in rows], dtype=np.int64)
    batches = []
    print(f"开始为 {len(rows)} 条非关系性文本建立中文向量索引……", flush=True)
    for start in range(0, len(rows), BATCH_SIZE):
        group = rows[start:start + BATCH_SIZE]
        texts = [f'{row["name"]}｜{row["section"]}：{row["content"]}' for row in group]
        vectors = np.asarray(list(model.embed(texts, batch_size=16)), dtype=np.float32)
        if len(vectors) != len(group):
            raise RuntimeError("Embedding 返回条目数量与文本不一致。")
        batches.append(_unit_vectors(vectors))
        if (start // BATCH_SIZE + 1) % 20 == 0 or start + len(group) == len(rows):
            print(f"已向量化 {start + len(group)}/{len(rows)} 条", flush=True)

    if _signature(db_file) != before:
        raise RuntimeError("向量生成时原知识库发生变化，拒绝保存过期索引。")
    matrix = np.concatenate(batches, axis=0)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".npz", prefix="semantic-",
                                         dir=index_path.parent, delete=False) as stream:
            temp_path = Path(stream.name)
            np.savez_compressed(stream, ids=passage_ids, vectors=matrix,
                                db_sha256=before, model=MODEL_NAME)
        os.replace(temp_path, index_path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    print(f"索引完成：{index_path}\n文本数：{len(rows)}；向量维度：{matrix.shape[1]}")

@lru_cache(maxsize=2)
def _read_index(path_string: str, mtime_ns: int, file_size: int):
    """缓存向量矩阵；索引文件更新后自动重新加载。"""
    with np.load(path_string, allow_pickle=False) as saved:
        return (
            saved["ids"],
            saved["vectors"],
            str(saved["db_sha256"].item()),
            str(saved["model"].item()),
        )


@lru_cache(maxsize=2)
def _snapshot_signature(path_string: str, mtime_ns: int, file_size: int):
    """Hash each immutable database snapshot once, not once per conversation."""
    return _signature(Path(path_string))


def search(question: str, top_k: int = 5, db_file: Path = KNOWLEDGE_DB,
           index_path: Path = INDEX_PATH):
    db_file, index_path = Path(db_file), Path(index_path)
    if not index_path.is_file():
        raise FileNotFoundError("语义索引不存在，请先运行 python -m friston3.knowledge.semantic_index build")
    db_stat = db_file.stat()
    current = _snapshot_signature(str(db_file.resolve()), db_stat.st_mtime_ns, db_stat.st_size)
    stat = index_path.stat()

    passage_ids, matrix, db_sha256, model_name = _read_index(
        str(index_path.resolve()),
        stat.st_mtime_ns,
        stat.st_size,
    )

    if db_sha256 != current:
        raise RuntimeError(
            "知识库已更新、语义索引过期，请重新运行 semantic_index build。"
        )

    if model_name != MODEL_NAME:
        raise RuntimeError(
            "Embedding 模型与索引不一致，请重新运行 semantic_index build。"
        )
    model = _encoder()
    query = QUERY_PREFIX + question.strip()
    query_vector = np.asarray(list(model.embed([query]))[0], dtype=np.float32)
    scores = matrix @ _unit_vectors(query_vector)
    top_k = min(max(1, int(top_k)), len(passage_ids))
    positions = np.argsort(scores)[-top_k:][::-1]
    chosen_ids = [int(passage_ids[i]) for i in positions]

    placeholders = ",".join("?" for _ in chosen_ids)
    with _connect(db_file) as db:
        rows = db.execute(f"""
            SELECT p.id, e.name, p.section, p.content, s.path, p.line_start, p.line_end
            FROM passages AS p
            JOIN entities AS e ON e.id=p.ref_id
            JOIN sources AS s ON s.id=p.source_id
            WHERE p.id IN ({placeholders})
        """, chosen_ids).fetchall()
    found = {row["id"]: row for row in rows}
    results = []
    for position, passage_id in zip(positions, chosen_ids):
        row = found.get(passage_id)
        if row is None:
            continue
        item = dict(row)
        item["score"] = float(scores[position])
        results.append(item)
    return results


def main():
    parser = argparse.ArgumentParser(description="Friston-3 中文语义索引（独立验收）")
    parser.add_argument("operation", choices=("build", "search"))
    parser.add_argument("question", nargs="*")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()
    if args.operation == "build":
        build()
        return
    if not args.question:
        parser.error("search 后面需要写检索问题。")
    results = search(" ".join(args.question), top_k=args.top_k)
    for rank, row in enumerate(results, 1):
        print(f'[{rank}] 语义相似度={row["score"]:.3f} | '
              f'{row["name"]} / {row["section"]} | '
              f'{row["path"]}:{row["line_start"]}-{row["line_end"]}')
        print(row["content"][:160].replace("\n", " "))
    print("提示：以上来自 Wiki 二次总结，语义命中不代表内容已被原作核验。")


if __name__ == "__main__":
    main()
