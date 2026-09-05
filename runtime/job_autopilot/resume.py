from __future__ import annotations

import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from xml.etree import ElementTree


SUPPORTED_SUFFIXES = {".pdf", ".docx", ".txt", ".md"}


def _clean_text(value: str, max_chars: int) -> str:
    value = value.replace("\x00", "")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines()]
    compact = "\n".join(line for line in lines if line)
    return compact[:max_chars]


def extract_resume_text(raw_path: str | Path, max_chars: int = 60_000) -> dict[str, object]:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        raise ValueError("resume path must be absolute")
    if not path.is_file():
        raise ValueError("resume file does not exist")
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError("supported resume formats: PDF, DOCX, TXT, MD")

    if suffix in {".txt", ".md"}:
        text = path.read_text(encoding="utf-8", errors="replace")
        engine = "plain_text"
    elif suffix == ".docx":
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml")
        root = ElementTree.fromstring(xml)
        chunks = [node.text or "" for node in root.iter() if node.tag.endswith("}t")]
        text = "\n".join(chunks)
        engine = "docx_xml"
    else:
        binary = shutil.which("pdftotext")
        if not binary:
            raise RuntimeError("pdftotext is required to extract PDF resumes")
        result = subprocess.run(
            [binary, "-layout", str(path), "-"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "PDF text extraction failed")
        text = result.stdout
        engine = "pdftotext"

    cleaned = _clean_text(text, max_chars)
    if not cleaned:
        raise ValueError("resume contains no extractable text")
    return {
        "path": str(path),
        "format": suffix.lstrip("."),
        "engine": engine,
        "text": cleaned,
        "truncated": len(cleaned) >= max_chars,
    }
