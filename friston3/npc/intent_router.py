"""Advisory semantic signal for the NPC Agent's evidence-demand decision.

This module does NOT search the lore database and must not be treated as the
final authority for CHAT/RETRIEVE/LIVE decisions. Its editable labelled examples
are encoded with the same FastEmbed model as the lore retriever. The AgenticRAG
model makes the final routing choice when deterministic entity/context handling
does not already require a bounded clarification or retrieval. Similarity and
margin are ranking signals, NOT calibrated probabilities.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import tempfile
from time import perf_counter
import warnings

from friston3.config import PROJECT_ROOT


LABELS = ("CHAT", "KNOWLEDGE", "LIVE", "FOLLOWUP")
EXAMPLES_PATH = PROJECT_ROOT / "data" / "knowledge" / "intent_examples.json"
CACHE_PATH = PROJECT_ROOT / "data" / "runtime" / "intent_semantic.npz"


def _float_setting(name: str, default: float, low: float, high: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
        if math.isfinite(value) and low <= value <= high:
            return value
    except (TypeError, ValueError, OverflowError):
        pass
    warnings.warn(f"{name} 无效，使用默认值 {default}", RuntimeWarning, stacklevel=2)
    return default


@dataclass(frozen=True)
class SemanticGuess:
    label: str = "UNCERTAIN"
    candidate: str = "UNCERTAIN"
    similarity: float = 0.0
    margin: float = 0.0
    agreement: float = 0.0
    elapsed_ms: float = 0.0

    def trace(self):
        return {"label": self.label, "candidate": self.candidate,
                "similarity": round(self.similarity, 3),
                "margin": round(self.margin, 3),
                "agreement": round(self.agreement, 2),
                "elapsed_ms": round(self.elapsed_ms, 1)}


class SemanticIntentRouter:
    """Selective kNN semantic router; abstains when neighbors disagree.

    Works without sklearn or a second resident language model. The small
    exemplar index lives separately from terra_semantic.npz and never mixes
    dialogue utterances with evidence passages. Index update is atomic.
    """
    def __init__(self):
        self.enabled = os.getenv("FRISTON_INTENT_ROUTER", "1").strip().lower() not in (
            "0", "false", "off", "no")
        self.min_similarity = _float_setting("FRISTON_INTENT_SIM_MIN", 0.78, 0.0, 1.0)
        self.min_margin = _float_setting("FRISTON_INTENT_MARGIN_MIN", 0.045, 0.0, 0.5)
        self.min_agreement = _float_setting("FRISTON_INTENT_AGREE_MIN", 0.8, 0.5, 1.0)
        self._vectors = None
        self._labels = None
        self._model = None
        self.error = ""

    @staticmethod
    def _examples():
        payload = EXAMPLES_PATH.read_bytes()
        examples = json.loads(payload.decode("utf-8-sig"))
        if not isinstance(examples, dict) or set(examples) != set(LABELS):
            raise ValueError("意图语料必须包含 CHAT/KNOWLEDGE/LIVE/FOLLOWUP 四类")
        records, labels, seen = [], [], set()
        for label in LABELS:
            if not isinstance(examples[label], list) or len(examples[label]) < 12:
                raise ValueError(f"意图语料 {label} 条目不足")
            for sentence in examples[label]:
                if not isinstance(sentence, str) or not sentence.strip() or len(sentence) > 500:
                    raise ValueError(f"意图语料 {label} 包含非法文本")
                sentence = sentence.strip()
                if sentence in seen:
                    raise ValueError(f"意图语料重复：{sentence}")
                seen.add(sentence)
                records.append(sentence)
                labels.append(LABELS.index(label))
        return records, labels, sha256(payload).hexdigest()

    def prepare(self):
        """Warm at program startup, not halfway through someone's first chat."""
        if not self.enabled or self._vectors is not None:
            return
        # Defer numpy/FastEmbed imports until actually using this optional path.
        import numpy as np
        from friston3.knowledge.semantic_index import MODEL_NAME, _encoder, _unit_vectors
        examples, labels, digest = self._examples()
        version = f"{MODEL_NAME}:{digest}:v2"
        matrix = None
        if CACHE_PATH.is_file():
            try:
                with np.load(CACHE_PATH, allow_pickle=False) as cache:
                    if (str(cache["version"].item()) == version
                            and len(cache["vectors"]) == len(examples)
                            and np.array_equal(cache["labels"], np.asarray(labels, dtype=np.uint8))):
                        matrix = cache["vectors"].astype(np.float32)
            except (OSError, KeyError, ValueError, TypeError):
                pass
        model = _encoder()  # shared with lore retriever; never a second embedding model
        if matrix is None:
            matrix = np.asarray(list(model.embed(examples, batch_size=32)), dtype=np.float32)
            if len(matrix) != len(examples):
                raise RuntimeError("意图 Embedding 条数不匹配")
            matrix = _unit_vectors(matrix)
            CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            temp = None
            try:
                with tempfile.NamedTemporaryFile(mode="wb", dir=CACHE_PATH.parent,
                         suffix=".npz", prefix="intent-", delete=False) as stream:
                    temp = Path(stream.name)
                    np.savez_compressed(stream, vectors=matrix,
                        labels=np.asarray(labels, dtype=np.uint8), version=version)
                os.replace(temp, CACHE_PATH)
            except OSError as error:
                # A read-only working tree must not prevent classification.
                warnings.warn(f"无法持久化意图索引，内存索引仍可用：{error}", RuntimeWarning)
            finally:
                if temp is not None:
                    temp.unlink(missing_ok=True)
        self._model = model
        self._vectors = matrix
        self._labels = np.asarray(labels, dtype=np.uint8)

    def classify(self, text: str) -> SemanticGuess:
        start = perf_counter()
        if not self.enabled:
            return SemanticGuess()
        if self._vectors is None:
            self.prepare()
        import numpy as np
        from friston3.knowledge.semantic_index import _unit_vectors
        value = str(text or "").strip()
        if not value:
            return SemanticGuess()
        vector = np.asarray(list(self._model.embed([value]))[0], dtype=np.float32)
        similarities = self._vectors @ _unit_vectors(vector)
        neighbor_count = min(5, len(similarities))
        positions = np.argpartition(similarities, -neighbor_count)[-neighbor_count:]
        positions = positions[np.argsort(similarities[positions])[::-1]]
        local_labels = self._labels[positions]
        # The top semantic matches vote, instead of interpreting an isolated
        # similar lore passage or an uncalibrated softmax as evidence demand.
        per_label = []
        for code in range(len(LABELS)):
            matching = np.sort(similarities[self._labels == code])[-3:]
            per_label.append(float(matching.mean()))
        ranking = np.argsort(per_label)[::-1]
        candidate = LABELS[int(ranking[0])]
        margin = per_label[int(ranking[0])] - per_label[int(ranking[1])]
        agreement = float(np.count_nonzero(local_labels == int(ranking[0])) / neighbor_count)
        best_similarity = float(similarities[positions[0]])
        confident = (best_similarity >= self.min_similarity
                     and margin >= self.min_margin and agreement >= self.min_agreement)
        # A two-turn reference only has meaning to the LLM with history.
        # FOLLOWUP is never a direct CHAT/RETRIEVE instruction.
        label = candidate if confident else "UNCERTAIN"
        return SemanticGuess(label=label, candidate=candidate,
            similarity=best_similarity, margin=margin, agreement=agreement,
            elapsed_ms=(perf_counter() - start) * 1000)
