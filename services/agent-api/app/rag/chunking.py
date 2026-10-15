"""Split policies into one chunk per section.

Each chunk repeats the document and section titles, so a short section ("Plazos",
i.e. deadlines) still carries its own context when retrieved on its own.
"""
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Chunk:
    id: str
    doc: str        # file name without extension, e.g. "01_cobro_duplicado"
    title: str      # document title (# ...)
    section: str    # section title (## ...)
    text: str       # text that gets embedded and passed to the model

    def as_dict(self) -> dict:
        return self.__dict__.copy()


_H1 = re.compile(r"^#\s+(.+)$", re.MULTILINE)
_H2 = re.compile(r"^##\s+(.+)$", re.MULTILINE)


def chunk_markdown(doc: str, markdown: str) -> list[Chunk]:
    h1 = _H1.search(markdown)
    title = h1.group(1).strip() if h1 else doc
    sections = list(_H2.finditer(markdown))
    if not sections:
        body = _H1.sub("", markdown).strip()
        return [Chunk(f"{doc}#0", doc, title, title, f"{title}\n{body}")]

    chunks = []
    for i, m in enumerate(sections):
        end = sections[i + 1].start() if i + 1 < len(sections) else len(markdown)
        body = markdown[m.end():end].strip()
        section = m.group(1).strip()
        chunks.append(Chunk(
            id=f"{doc}#{i}", doc=doc, title=title, section=section,
            text=f"{title} — {section}\n{body}",
        ))
    return chunks


def load_policies(folder: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path in sorted(folder.glob("*.md")):
        chunks.extend(chunk_markdown(path.stem, path.read_text(encoding="utf-8")))
    return chunks
