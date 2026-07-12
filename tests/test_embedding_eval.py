from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import numpy as np


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "evaluate_embeddings.py"
SPEC = importlib.util.spec_from_file_location("embedding_eval", SCRIPT_PATH)
assert SPEC and SPEC.loader
embedding_eval = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = embedding_eval
SPEC.loader.exec_module(embedding_eval)


class FakeEncoder:
    def __init__(self, vectors: dict[str, list[float]]):
        self.vectors = vectors
        self.calls: list[list[str]] = []

    def encode(self, texts, **_kwargs):
        self.calls.append(list(texts))
        return np.asarray([self.vectors[text] for text in texts], dtype="float32")


def test_curated_russian_dataset_is_valid():
    dataset = embedding_eval.load_dataset(embedding_eval.DEFAULT_DATASET)

    assert dataset.id == "second-brain-russian-retrieval-v1"
    assert len(dataset.corpus) >= 10
    assert len(dataset.queries) >= 10


def test_evaluator_reports_recall_mrr_and_e5_role_prefixes():
    dataset = embedding_eval.RetrievalDataset(
        id="test",
        corpus=(
            embedding_eval.Document(id="first", text="first document"),
            embedding_eval.Document(id="second", text="second document"),
        ),
        queries=(
            embedding_eval.Query(id="q1", text="first query", relevant_ids=("first",)),
            embedding_eval.Query(id="q2", text="second query", relevant_ids=("second",)),
        ),
    )
    encoder = FakeEncoder(
        {
            "passage: first document": [1, 0],
            "passage: second document": [0, 1],
            "query: first query": [1, 0],
            "query: second query": [1, 0],
        }
    )

    report = embedding_eval.evaluate_embeddings(
        dataset,
        "intfloat/multilingual-e5-small",
        encoder,
        ks=(1, 2),
    )

    assert report["dimension"] == 2
    assert report["metrics"] == {
        "recall_at_1": 0.5,
        "recall_at_2": 1.0,
        "mrr_at_2": 0.75,
    }
    assert encoder.calls == [
        ["passage: first document", "passage: second document"],
        ["query: first query", "query: second query"],
    ]


def test_download_is_opt_in():
    args = embedding_eval.build_parser().parse_args([])

    assert args.allow_download is False
