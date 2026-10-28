"""
title: Path-Jailed Local Clinical Library Exact Search (Companion to Open WebUI Knowledge RAG)
author: Stuart Brigham, MD
author_url: https://github.com/skbrig02-coder/openwebui-clinical-tools
description: Strict read-only, path-traversal-proof filename and full-text/PDF search over the local Clinical Library folder to complement Open WebUI built-in Workspace Knowledge Collections.
required_open_webui_version: 0.4.0
requirements: pydantic
version: 1.0.0
license: Copyright (c) 2026 Stuart Brigham, MD (Free to use with attribution to Stuart Brigham, MD)
"""

import os
import re
import subprocess
from pathlib import Path
from typing import List, Tuple
try:
    from pydantic import BaseModel, Field
except ImportError:
    class BaseModel:  # type: ignore
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    def Field(default=None, **kwargs):  # type: ignore
        return default


def _clean_line(text: str, max_len: int = 320) -> str:
    s = " ".join((text or "").replace("\u2014", " - ").replace("\u2013", "-").split())
    if len(s) > max_len:
        return s[:max_len] + "... [truncated]"
    return s


def _extract_pdf_text(pdf_path: Path, max_pages: int = 25) -> str:
    # 1. Try pypdf /pymupdf if installed in the Open WebUI container/venv
    try:
        import pypdf  # type: ignore

        reader = pypdf.PdfReader(str(pdf_path))
        chunks = []
        for idx, page in enumerate(reader.pages[:max_pages], start=1):
            txt = page.extract_text() or ""
            if txt.strip():
                chunks.append(f"[Page {idx}] {txt}")
        return "\n".join(chunks)
    except Exception:
        pass

    # 2. Fallback to poppler pdftotext if present
    try:
        res = subprocess.run(
            ["pdftotext", "-l", str(max_pages), str(pdf_path), "-"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout
    except Exception:
        pass

    return ""


class Tools:
    class Valves(BaseModel):
        CLINICAL_LIBRARY_DIR: str = Field(
            default=r"C:\Users\Stuart\Documents\AI Teams\Clinical Library",
            description="Read-only root directory for the local Clinical Library (use host path on Windows/Linux or mounted Docker volume path such as /app/backend/data/clinical_library).",
        )
        MAX_FILES_SCANNED: int = Field(
            default=200,
            description="Maximum number of files scanned per search.",
            ge=10,
            le=2000,
        )
        MAX_PDF_PAGES: int = Field(
            default=25,
            description="Maximum pages extracted per PDF during full-text search.",
            ge=1,
            le=150,
        )

    def __init__(self):
        self.valves = self.Valves()

    def _resolve_safe_root(self) -> Path:
        root = Path(self.valves.CLINICAL_LIBRARY_DIR).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise FileNotFoundError(
                f"Configured CLINICAL_LIBRARY_DIR does not exist or is not a directory: {root}"
            )
        return root

    def _is_within_root(self, candidate: Path, root: Path) -> bool:
        try:
            candidate.resolve().relative_to(root.resolve())
            return True
        except ValueError:
            return False

    def search_local_clinical_library(
        self,
        query: str,
        filename_filter: str = "",
        max_matches: int = 15,
    ) -> str:
        """
        Perform a strict read-only, path-jailed keyword search across Markdown, text, HTML, and PDF files
        inside the configured CLINICAL_LIBRARY_DIR (`Documents\\AI Teams\\Clinical Library`).
        Use this tool alongside Open WebUI's built-in Workspace -> Knowledge RAG collections when you need
        exact phrase matching, acronym lookup, or a directory inventory of local guidelines.

        :param query: Keyword or phrase to search for (or pass '*' to list files in the Clinical Library).
        :param filename_filter: Optional substring to filter filenames (e.g., 'KDIGO', 'AHA', 'sepsis', '.pdf').
        :param max_matches: Maximum number of matching files/snippets to return (1 to 30).
        :return: Markdown summary of matching local clinical library files and text excerpts.
        """
        try:
            root = self._resolve_safe_root()
        except Exception as e:
            return f"Clinical Library Configuration Notice: {e}\nTip: Update the `CLINICAL_LIBRARY_DIR` Valve in Open WebUI Workspace -> Tools."

        q = (query or "").strip()
        if not q:
            return "Error: query is required (pass '*' to list library files)."

        limit = max(1, min(int(max_matches or 15), 30))
        fn_sub = (filename_filter or "").strip().lower()
        allowed_exts = {".md", ".txt", ".pdf", ".html", ".htm", ".csv", ".json"}

        files_checked = 0
        inventory: List[Tuple[str, int]] = []
        hits: List[Tuple[str, List[str]]] = []

        for dirpath, _, filenames in os.walk(root):
            for fname in sorted(filenames):
                if files_checked >= int(self.valves.MAX_FILES_SCANNED):
                    break
                fpath = Path(dirpath) / fname
                if not self._is_within_root(fpath, root):
                    continue
                ext = fpath.suffix.lower()
                if ext not in allowed_exts:
                    continue
                rel_path = str(fpath.resolve().relative_to(root))
                if fn_sub and fn_sub not in rel_path.lower():
                    continue

                files_checked += 1
                size_kb = max(1, fpath.stat().st_size // 1024)
                inventory.append((rel_path, size_kb))

                if q == "*":
                    continue

                try:
                    if ext == ".pdf":
                        content = _extract_pdf_text(fpath, max_pages=int(self.valves.MAX_PDF_PAGES))
                    else:
                        content = fpath.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    content = ""

                if not content and q.lower() in rel_path.lower():
                    hits.append((rel_path, ["[Matched filename]"]))
                    continue

                pattern = re.compile(re.escape(q), re.IGNORECASE)
                matched_snippets: List[str] = []
                for line in content.splitlines():
                    if pattern.search(line):
                        matched_snippets.append(_clean_line(line))
                        if len(matched_snippets) >= 3:
                            break
                if matched_snippets or q.lower() in rel_path.lower():
                    hits.append(
                        (
                            rel_path,
                            matched_snippets if matched_snippets else ["[Matched filename]"],
                        )
                    )
                    if len(hits) >= limit:
                        break

        if q == "*":
            lines = [
                f"# Local Clinical Library File Inventory ({len(inventory)} files scanned)",
                f"- **Library Root:** `{root}`",
                "",
            ]
            for idx, (rpath, kb) in enumerate(inventory[:limit], start=1):
                lines.append(f"{idx}. `{rpath}` ({kb} KB)")
            return "\n".join(lines)

        if not hits:
            return f"No matches found for '{q}' across {files_checked} files in `{root}`."

        lines = [
            f"# Local Clinical Library Search Results: {q}",
            f"- **Library Root:** `{root}` (scanned {files_checked} files, showing {len(hits)} matching files)",
            "",
        ]
        for idx, (rpath, snippets) in enumerate(hits, start=1):
            lines.append(f"## {idx}. `{rpath}`")
            for s in snippets:
                lines.append(f"- {s}")
            lines.append("")

        return "\n".join(lines)
