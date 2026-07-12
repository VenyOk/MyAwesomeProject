"""Compare locally available sentence-transformers models on Russian retrieval.

The script deliberately keeps Hugging Face in offline mode unless the caller
passes ``--allow-download``.  It is intended for a small, repeatable quality
gate before changing the embedding model used by the application.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
from typing import Any, Iterator, Sequence

import numpy as np


DEFAULT_DATASET = (
    Path(__file__).resolve().parents[1] / "data" / "eval" / "russian_retrieval.json"
)
DEFAULT_KS = (1, 3, 5)


@dataclass(frozen=True)
class Document:
    id: str
    text: str


@dataclass(frozen=True)
class Query:
    id: str
    text: str
    relevant_ids: tuple[str, ...]


@dataclass(frozen=True)
class RetrievalDataset:
    id: str
    corpus: tuple[Document, ...]
    queries: tuple[Query, ...]


def _nonempty_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def load_dataset(path: Path | str) -> RetrievalDataset:
    """Load and validate the deliberately small JSON retrieval dataset."""
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read evaluation dataset {source}: {error}") from error

    if not isinstance(payload, dict):
        raise ValueError("Evaluation dataset root must be an object")
    dataset_id = _nonempty_text(payload.get("id"), "dataset id")
    raw_corpus = payload.get("corpus")
    raw_queries = payload.get("queries")
    if not isinstance(raw_corpus, list) or not raw_corpus:
        raise ValueError("Evaluation dataset corpus must be a non-empty list")
    if not isinstance(raw_queries, list) or not raw_queries:
        raise ValueError("Evaluation dataset queries must be a non-empty list")

    corpus: list[Document] = []
    corpus_ids: set[str] = set()
    for item in raw_corpus:
        if not isinstance(item, dict):
            raise ValueError("Each corpus item must be an object")
        document_id = _nonempty_text(item.get("id"), "corpus item id")
        if document_id in corpus_ids:
            raise ValueError(f"Duplicate corpus id: {document_id}")
        corpus_ids.add(document_id)
        corpus.append(Document(id=document_id, text=_nonempty_text(item.get("text"), "corpus text")))

    queries: list[Query] = []
    query_ids: set[str] = set()
    for item in raw_queries:
        if not isinstance(item, dict):
            raise ValueError("Each query item must be an object")
        query_id = _nonempty_text(item.get("id"), "query id")
        if query_id in query_ids:
            raise ValueError(f"Duplicate query id: {query_id}")
        query_ids.add(query_id)
        raw_relevant_ids = item.get("relevant_ids")
        if not isinstance(raw_relevant_ids, list) or not raw_relevant_ids:
            raise ValueError(f"Query {query_id} must have at least one relevant id")
        relevant_ids = tuple(
            _nonempty_text(relevant_id, f"relevant id for {query_id}")
            for relevant_id in raw_relevant_ids
        )
        unknown_ids = set(relevant_ids) - corpus_ids
        if unknown_ids:
            raise ValueError(f"Query {query_id} references unknown ids: {sorted(unknown_ids)}")
        queries.append(
            Query(
                id=query_id,
                text=_nonempty_text(item.get("text"), "query text"),
                relevant_ids=relevant_ids,
            )
        )

    return RetrievalDataset(id=dataset_id, corpus=tuple(corpus), queries=tuple(queries))


def uses_e5_prefixes(model_name: str) -> bool:
    return "e5" in model_name.lower()


def retrieval_texts(texts: Sequence[str], model_name: str, role: str) -> list[str]:
    """Apply the query/passage convention required by E5 retrieval models."""
    if role not in {"query", "document"}:
        raise ValueError(f"Unsupported embedding role: {role}")
    if not uses_e5_prefixes(model_name):
        return list(texts)
    prefix = "query: " if role == "query" else "passage: "
    return [text if text.startswith(prefix) else f"{prefix}{text}" for text in texts]


def _normalize_rows(vectors: np.ndarray, expected_rows: int) -> np.ndarray:
    values = np.asarray(vectors, dtype="float32")
    if values.ndim == 1 and expected_rows == 1:
        values = values.reshape(1, -1)
    if values.ndim != 2 or values.shape[0] != expected_rows or values.shape[1] == 0:
        raise ValueError(f"Expected {expected_rows} embedding rows, got shape {values.shape}")
    norms = np.linalg.norm(values, axis=1, keepdims=True)
    return np.divide(values, norms, out=np.zeros_like(values), where=norms != 0)


def _encode(encoder: Any, texts: Sequence[str]) -> np.ndarray:
    values = encoder.encode(
        list(texts), convert_to_numpy=True, normalize_embeddings=True
    )
    return _normalize_rows(np.asarray(values), len(texts))


def evaluate_embeddings(
    dataset: RetrievalDataset,
    model_name: str,
    encoder: Any,
    *,
    ks: Sequence[int] = DEFAULT_KS,
) -> dict[str, Any]:
    """Calculate Recall@k and MRR@max(k) for a loaded encoder."""
    normalized_ks = tuple(sorted({int(k) for k in ks}))
    if not normalized_ks or normalized_ks[0] <= 0:
        raise ValueError("At least one positive k is required")

    document_ids = [document.id for document in dataset.corpus]
    document_vectors = _encode(
        encoder,
        retrieval_texts([document.text for document in dataset.corpus], model_name, "document"),
    )
    query_vectors = _encode(
        encoder,
        retrieval_texts([query.text for query in dataset.queries], model_name, "query"),
    )
    if query_vectors.shape[1] != document_vectors.shape[1]:
        raise ValueError(
            "Query and document encodings have different dimensions: "
            f"{query_vectors.shape[1]} and {document_vectors.shape[1]}"
        )

    recall_hits = {k: 0 for k in normalized_ks}
    reciprocal_rank_total = 0.0
    max_k = max(normalized_ks)
    for query, vector in zip(dataset.queries, query_vectors):
        scores = document_vectors @ vector
        ranking = np.argsort(-scores, kind="mergesort")
        relevant = set(query.relevant_ids)
        for k in normalized_ks:
            top_ids = (document_ids[index] for index in ranking[:k])
            if any(document_id in relevant for document_id in top_ids):
                recall_hits[k] += 1
        for position, index in enumerate(ranking[:max_k], start=1):
            if document_ids[index] in relevant:
                reciprocal_rank_total += 1 / position
                break

    query_count = len(dataset.queries)
    metrics = {f"recall_at_{k}": recall_hits[k] / query_count for k in normalized_ks}
    metrics[f"mrr_at_{max_k}"] = reciprocal_rank_total / query_count
    return {
        "dataset": dataset.id,
        "model": model_name,
        "dimension": int(document_vectors.shape[1]),
        "documents": len(dataset.corpus),
        "queries": query_count,
        "metrics": metrics,
    }


@contextmanager
def _offline_hub() -> Iterator[None]:
    """Prevent an uncached model lookup from silently becoming a network request."""
    keys = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")
    previous = {key: os.environ.get(key) for key in keys}
    os.environ.update({key: "1" for key in keys})
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def load_encoder(model_name: str, *, allow_download: bool) -> Any:
    """Load an encoder, refusing network access by default."""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as error:
        raise RuntimeError("Install sentence-transformers before running this evaluation") from error

    if allow_download:
        return SentenceTransformer(model_name)
    try:
        with _offline_hub():
            return SentenceTransformer(
                model_name,
                model_kwargs={"local_files_only": True},
                tokenizer_kwargs={"local_files_only": True},
            )
    except Exception as error:  # Model-specific loaders raise different exception classes.
        raise RuntimeError(
            f"Model {model_name!r} is not available in the local cache. "
            "Download it explicitly with --allow-download, then rerun without that flag."
        ) from error


def format_report(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    metric_lines = [
        f"  {name.replace('recall_at_', 'Recall@').replace('mrr_at_', 'MRR@')}: {value:.3f}"
        for name, value in metrics.items()
    ]
    return "\n".join(
        [
            f"Model: {report['model']}",
            f"Dataset: {report['dataset']} ({report['queries']} queries, {report['documents']} documents)",
            f"Embedding dimension: {report['dimension']}",
            *metric_lines,
        ]
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument(
        "--model",
        action="append",
        help="Sentence-transformers model to evaluate; repeat to compare models.",
    )
    parser.add_argument("--k", type=int, nargs="+", default=list(DEFAULT_KS))
    parser.add_argument(
        "--allow-download",
        action="store_true",
        help="Permit downloading a model missing from the local Hugging Face cache.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable reports.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        dataset = load_dataset(args.dataset)
        reports = []
        model_names = args.model or [
            os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
        ]
        for model_name in model_names:
            encoder = load_encoder(model_name, allow_download=args.allow_download)
            reports.append(evaluate_embeddings(dataset, model_name, encoder, ks=args.k))
    except ValueError as error:
        print(f"Evaluation failed: {error}", file=sys.stderr)
        return 2
    except RuntimeError as error:
        print(f"Evaluation failed: {error}", file=sys.stderr)
        return 3

    if args.json:
        print(json.dumps(reports, ensure_ascii=False, indent=2))
    else:
        print("\n\n".join(format_report(report) for report in reports))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
