"""Source-wide evidence retrieval with optional local MiniLM embeddings."""
from __future__ import annotations

from bisect import bisect_left
from collections import Counter, defaultdict
from importlib.metadata import version
from math import log1p
from pathlib import Path
import re

from .artifacts import artifact_key, artifact_lock, digest_file, staged_file
from .research_interfaces import validate_evidence

MINILM_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
MINILM_WEIGHTS = "b941bf19f1f1283680f449fa6a7336bb5600bdcd5f84d10ddc5cd72218a0fd21"
MINILM_TOKENIZER = "be50c3628f2bf5bb5e3a7f17b1f74611b2561a3a27eeab05e5aa30f411572037"
STOP = frozenset("a an the it he she we you they i to of in for on or at is was are be and that this with my your me it so had has have but then just as do did what there".split())


def terms(text: str) -> list[str]:
    return [word for word in re.findall(r"[^\W_]+", text.casefold()) if word not in STOP and len(word) > 1]


def reciprocal_ranks(lists: dict[str, list[str]]) -> dict[str, dict[str, int]]:
    ranks = defaultdict(dict)
    for lane, identifiers in lists.items():
        for rank, identifier in enumerate(dict.fromkeys(identifiers), 1):
            ranks[identifier][lane] = rank
    return ranks


def fused_score(ranks: dict[str, int]) -> float:
    return sum(1 / (60 + rank) for rank in ranks.values())


class MiniLM:
    """The upstream quantized CPU graph, using attention-masked mean pooling."""

    def __init__(self, root: Path, batch: int):
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer

        model = root / "onnx/model_quint8_avx2.onnx"
        tokenizer = root / "tokenizer.json"
        tokenizer_hash = digest_file(tokenizer)
        if digest_file(model) != MINILM_WEIGHTS or tokenizer_hash != MINILM_TOKENIZER:
            raise ValueError("MiniLM weights differ from the supported checkpoint")
        self.fingerprint = {"model": "sentence-transformers/all-MiniLM-L6-v2", "revision": MINILM_REVISION,
                            "tokenizer_sha256": tokenizer_hash, "pooling": "masked-mean-l2/1",
                            "onnxruntime": version("onnxruntime"), "tokenizers": version("tokenizers")}
        self.np = np
        self.batch = batch
        self.tokenizer = Tokenizer.from_file(str(tokenizer))
        self.tokenizer.enable_truncation(max_length=256)
        self.tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
        options = ort.SessionOptions()
        options.intra_op_num_threads = min(4, batch)
        try:
            self.session = ort.InferenceSession(str(model), sess_options=options, providers=["CPUExecutionProvider"])
        except Exception as exc:
            raise RuntimeError(f"MiniLM ONNX runtime failed to load: {exc}") from exc

    def encode(self, texts: list[str]):
        np = self.np
        chunks = []
        for start in range(0, len(texts), self.batch):
            enc = self.tokenizer.encode_batch(texts[start:start + self.batch])
            mask = np.asarray([e.attention_mask for e in enc], dtype=np.int64)
            model_inputs = {"input_ids": np.asarray([e.ids for e in enc], dtype=np.int64),
                            "attention_mask": mask,
                            "token_type_ids": np.asarray([e.type_ids for e in enc], dtype=np.int64)}
            feeds = {i.name: model_inputs[i.name] for i in self.session.get_inputs()}
            try:
                tokens = self.session.run(None, feeds)[0]
            except Exception as exc:
                raise RuntimeError(f"MiniLM ONNX inference failed: {exc}") from exc
            pooled = (tokens * mask[:, :, None]).sum(axis=1).astype(np.float32)
            chunks.append(pooled / np.maximum(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12))
        return np.concatenate(chunks, axis=0)


class Memory:
    def __init__(self, rows: list[dict], source_id: str, duration_ms: int):
        self.rows = {row["id"]: validate_evidence(row, source_id, duration_ms) for row in rows}
        self.utterances = sorted((r for r in rows if r["modality"] == "utterance"),
                                 key=lambda r: (r["start_ms"], r["end_ms"], r["id"]))
        self.utterance_indices = {row["id"]: i for i, row in enumerate(self.utterances)}
        self.starts = [r["start_ms"] for r in self.utterances]
        self.max_utterance_ms = max((r["end_ms"] - r["start_ms"] for r in self.utterances), default=0)
        self.postings = defaultdict(list)
        self.lengths = []
        for index, row in enumerate(self.utterances):
            counts = Counter(terms(row["text"]))
            self.lengths.append(sum(counts.values()))
            for term, count in counts.items():
                self.postings[term].append((index, count))
        self.average = max(1, sum(self.lengths) / max(1, len(self.lengths)))
        self.encoder = self.vectors = None
        self.dense_failure = None
        self.history = []
        self._queries = {}

    def enable_dense(self, root: Path, cache: Path, *, batch: int, max_mb: int) -> dict:
        if not self.utterances:
            return {"state": "unavailable", "reason": "no_utterances"}
        if len(self.utterances) * 384 * 4 > max_mb * 1024 * 1024:
            return {"state": "unavailable", "reason": "embedding_memory_limit"}
        encoder = MiniLM(root, batch)
        np = encoder.np
        key = artifact_key([encoder.fingerprint, [r["id"] for r in self.utterances]])
        target = cache / "embeddings" / f"{key}.npy"
        with artifact_lock(cache / "locks" / f"{key}.lock", timeout=600):
            vectors = None
            if target.is_file():
                try:
                    loaded = np.load(target, allow_pickle=False)
                    if (loaded.shape == (len(self.utterances), 384) and loaded.dtype == np.float32
                            and np.isfinite(loaded).all()):
                        vectors = loaded
                except (OSError, ValueError):
                    pass
            hit = vectors is not None
            if not hit:
                vectors = encoder.encode([r["text"] for r in self.utterances])
                with staged_file(target) as pending:
                    np.save(pending, vectors, allow_pickle=False)
                    pending.replace(target)
        self.encoder, self.vectors = encoder, vectors
        return {"state": "available", "cache_hit": hit, "model": encoder.fingerprint, "count": len(vectors)}

    def hydrate(self, identifiers) -> list[dict]:
        return [self.rows[identifier] for identifier in identifiers]

    def around(self, start: int, end: int, *, margin: int = 0) -> list[dict]:
        left = bisect_left(self.starts, start - margin - self.max_utterance_ms)
        right = bisect_left(self.starts, end + margin)
        return [row for row in self.utterances[left:right] if row["end_ms"] > start - margin]

    def search(self, query: str, *, limit: int = 6, exclude=(), speaker: str | None = None,
               scope: str = "all", anchor: int = 0, hybrid: bool = True) -> list[dict]:
        if scope not in {"all", "past", "future"}:
            raise ValueError("Invalid evidence search scope")
        excluded = frozenset(exclude)
        key = (query.strip().casefold(), limit, excluded, speaker, scope, anchor if scope != "all" else 0, hybrid)
        if key in self._queries:
            return self._queries[key]
        n = len(self.utterances)
        scores = defaultdict(float)
        for term in dict.fromkeys(terms(query)):
            postings = self.postings.get(term, ())
            weight = log1p((n - len(postings) + .5) / (len(postings) + .5))
            for index, count in postings:
                scores[index] += weight * count * 2.2 / (count + 1.2 * (.25 + .75 * self.lengths[index] / self.average))

        def allowed(index):
            row = self.utterances[index]
            return (row["id"] not in excluded and (speaker is None or row["speaker_id"] == speaker)
                    and (scope != "past" or row["end_ms"] <= anchor)
                    and (scope != "future" or row["start_ms"] >= anchor))

        lexical = [i for i in sorted(scores, key=lambda i: (-scores[i], i)) if allowed(i)][:limit * 3]
        dense = []
        similarity = None
        if hybrid and self.encoder is not None and query.strip():
            try:
                similarity = self.vectors @ self.encoder.encode([query])[0]
            except RuntimeError as exc:
                self.dense_failure = str(exc)
                self.encoder = None
            for index in (-similarity).argsort(kind="stable") if similarity is not None else ():
                if similarity[index] < .28 or len(dense) >= limit * 3:
                    break
                if allowed(index):
                    dense.append(int(index))
        ranking = reciprocal_ranks({"bm25": lexical, "cosine": dense})
        hits = [{"evidence_id": self.utterances[i]["id"], "start_ms": self.utterances[i]["start_ms"],
                 "end_ms": self.utterances[i]["end_ms"], "ranks": ranking[i], "bm25": round(scores[i], 5),
                 "cosine": round(float(similarity[i]), 5) if similarity is not None else None,
                 "reason": "source-wide text relevance", "score": round(fused_score(ranking[i]), 6)}
                for i in sorted(ranking, key=lambda i: (-fused_score(ranking[i]), i))[:limit]]
        self._queries[key] = hits
        self.history.append({"query": query, "speaker": speaker, "scope": scope, "anchor_ms": anchor, "hits": hits})
        return hits
