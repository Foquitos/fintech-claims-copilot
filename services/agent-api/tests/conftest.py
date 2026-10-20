from pathlib import Path

import pytest

from app.rag.chunking import load_policies
from app.rag.embeddings import HashEmbedder
from app.rag.index import PolicyIndex

from .fakes import CLAIM

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def index():
    return PolicyIndex.build(load_policies(ROOT / "policies"), HashEmbedder())


@pytest.fixture(scope="session")
def cited(index):
    """The id of the top policy excerpt retrieved for CLAIM, i.e. a valid citation."""
    return index.search(CLAIM, k=3)[0].chunk.id
