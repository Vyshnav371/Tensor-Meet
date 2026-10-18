"""Chat attachments: store in Cloud Storage, parse with Document AI Layout Parser into Markdown."""
from __future__ import annotations

import asyncio
import re
import time

from .config import Settings
from .store import MeetingStore, new_id

MAX_MARKDOWN_CHARS = 60_000


def layout_to_markdown(document) -> tuple[str, int]:
    """Convert a Document AI layout (document_layout.blocks) into Markdown; returns (markdown, table_count)."""
    lines: list[str] = []
    tables = 0

    def text_of(blocks) -> str:
        parts = []
        for b in blocks:
            if b.text_block and b.text_block.text:
                parts.append(b.text_block.text)
                parts.append(text_of(b.text_block.blocks))
        return " ".join(p for p in parts if p).strip()

    def walk(blocks, depth=0):
        nonlocal tables
        for b in blocks:
            if b.text_block and (b.text_block.text or b.text_block.blocks):
                kind = (b.text_block.type_ or "paragraph").lower()
                text = (b.text_block.text or "").strip()
                if text:
                    if kind.startswith("heading"):
                        level = int(re.sub(r"\D", "", kind) or 2)
                        lines.append(f"{'#' * min(level + 1, 4)} {text}")
                    elif kind == "header" or kind == "footer":
                        pass
                    else:
                        lines.append(text)
                walk(b.text_block.blocks, depth + 1)
            elif b.table_block and (b.table_block.header_rows or b.table_block.body_rows):
                tables += 1
                rows = list(b.table_block.header_rows) + list(b.table_block.body_rows)
                width = max((len(r.cells) for r in rows), default=0)
                for i, row in enumerate(rows):
                    cells = [text_of(c.blocks).replace("|", "/").replace("\n", " ") for c in row.cells]
                    cells += [""] * (width - len(cells))
                    lines.append("| " + " | ".join(cells) + " |")
                    if i == max(len(b.table_block.header_rows), 1) - 1:
                        lines.append("|" + "---|" * width)
            elif b.list_block and b.list_block.list_entries:
                for entry in b.list_block.list_entries:
                    lines.append(f"{'  ' * depth}- {text_of(entry.blocks)}")
            lines.append("")

    walk(document.document_layout.blocks)
    markdown = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return markdown, tables


class DocumentIntelligence:
    def __init__(self, settings: Settings, store: MeetingStore, docai_client=None, storage_client=None):
        self.settings = settings
        self.store = store
        self._docai = docai_client
        self._storage = storage_client

    def _clients(self):
        if self._docai is None:
            from google.cloud import documentai_v1 as dai

            location = self.settings.docai_processor.split("/locations/")[1].split("/")[0]
            self._docai = dai.DocumentProcessorServiceClient(client_options={"api_endpoint": f"{location}-documentai.googleapis.com"})
        if self._storage is None:
            from google.cloud import storage

            self._storage = storage.Client(project=self.settings.project)
        return self._docai, self._storage

    async def ingest(self, meeting_id: str, filename: str, content: bytes, uploaded_by: str) -> dict:
        """Store the PDF, announce it in chat, then parse it in the background (FR-4.1 / FR-4.2)."""
        doc_id = new_id("doc")
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", filename)[:120] or "document.pdf"
        gcs_path = f"uploads/{meeting_id}/{doc_id}/{safe_name}"
        _, storage = self._clients()
        await asyncio.to_thread(lambda: storage.bucket(self.settings.bucket).blob(gcs_path).upload_from_string(content, content_type="application/pdf"))
        doc = dict(id=doc_id, name=filename, uploaded_by=uploaded_by, gcs_uri=f"gs://{self.settings.bucket}/{gcs_path}",
                   status="PARSING", epoch=time.time(), size=len(content))
        await self.store.put_document(meeting_id, doc)
        await self.store.post_chat(meeting_id, uploaded_by, f"Shared {filename}", kind="file", attachment=dict(doc_id=doc_id, name=filename))
        asyncio.create_task(self._parse(meeting_id, doc, content))
        return doc

    async def _parse(self, meeting_id: str, doc: dict, content: bytes) -> None:
        from google.cloud import documentai_v1 as dai

        docai, _ = self._clients()
        started = time.time()
        try:
            request = dai.ProcessRequest(
                name=self.settings.docai_processor,
                raw_document=dai.RawDocument(content=content, mime_type="application/pdf"),
                process_options=dai.ProcessOptions(layout_config=dai.ProcessOptions.LayoutConfig(
                    chunking_config=dai.ProcessOptions.LayoutConfig.ChunkingConfig(chunk_size=500, include_ancestor_headings=True))),
            )
            result = await asyncio.to_thread(docai.process_document, request=request, timeout=120)
            markdown, tables = layout_to_markdown(result.document)
            pages = len(result.document.pages) or max((c.page_span.page_end for c in result.document.chunked_document.chunks), default=0)
            doc.update(status="READY", markdown=markdown[:MAX_MARKDOWN_CHARS], tables=tables, pages=pages,
                       parse_seconds=round(time.time() - started, 1), parser="Document AI Layout Parser")
            await self.store.put_document(meeting_id, doc)
            await self.store.post_chat(meeting_id, "Tensor", f"Parsed **{doc['name']}** with Document AI: {pages} pages, {tables} tables. "
                                       "Ask me about it with \"Hey Tensor\" or @Tensor.", kind="system")
        except Exception as exc:  # report, never crash the meeting
            doc.update(status="FAILED", error=f"{type(exc).__name__}: {exc}"[:300])
            await self.store.put_document(meeting_id, doc)
            await self.store.post_chat(meeting_id, "Tensor", f"I couldn't parse {doc['name']}: {doc['error']}", kind="system")
