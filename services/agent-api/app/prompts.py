"""Versioned prompts.

Each prompt is a file prompts/<name>.v<N>.md. A new wording is a new file, never an edit in
place, so eval results stay tied to the exact text that produced them. The short content hash
goes into every trace (PRD 9.1.8).
"""
import hashlib
from dataclasses import dataclass
from functools import cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"


@dataclass(frozen=True)
class Prompt:
    id: str  # e.g. "classify.v1"
    text: str
    sha: str  # first 12 hex chars of sha256(text)


@cache
def load(prompt_id: str) -> Prompt:
    text = (PROMPTS_DIR / f"{prompt_id}.md").read_text(encoding="utf-8").strip()
    return Prompt(prompt_id, text, hashlib.sha256(text.encode()).hexdigest()[:12])
