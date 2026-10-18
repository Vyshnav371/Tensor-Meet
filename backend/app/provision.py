"""Company setup and company documents.

On registration a company gets its own BigQuery dataset filled from its industry template, plus a starter
handbook indexed in Vertex AI Search. Admins can then add their own documents (PDF, HTML, TXT, DOCX).

  companies/{cid}/knowledge/{doc}   name, mime, size, gcs_uri, status (INDEXING | READY | FAILED), uploaded_by, epoch
"""
from __future__ import annotations

import asyncio
import logging
import re
import time

from . import templates
from .accounts import Accounts
from .config import Settings
from .knowledge import KB_MIME, CompanyKnowledge
from .store import new_id, now_iso

log = logging.getLogger(__name__)
MAX_KB_UPLOAD = 10 * 1024 * 1024


class Provisioner:
    def __init__(self, settings: Settings, accounts: Accounts, knowledge: CompanyKnowledge):
        self.settings, self.accounts, self.knowledge = settings, accounts, knowledge
        self.db = accounts.db
        self.tasks: set[asyncio.Task] = set()

    def background(self, coro) -> None:
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    # ---- new company --------------------------------------------------------------------------------

    async def provision(self, company: dict) -> None:
        started = time.time()
        try:
            await asyncio.to_thread(self._load_tables, company)
            html = templates.handbook_html(company["industry"], company["name"], company["jira_key"])
            await self.add_document(company["id"], f"{company['name']} Company Handbook.html", html.encode(), "Tensor", wait=False)
            await self.accounts.update_company(company["id"], status="READY", ready_at=now_iso(),
                                               provision_seconds=round(time.time() - started, 1))
        except Exception as exc:
            log.exception("provisioning failed for %s", company["id"])
            await self.accounts.update_company(company["id"], status="FAILED", error=f"{type(exc).__name__}: {exc}"[:300])

    def _load_tables(self, company: dict) -> None:
        from google.cloud import bigquery

        bq = self.knowledge.bq
        dataset = bigquery.Dataset(f"{self.settings.project}.{company['dataset']}")
        dataset.location = self.settings.bq_location
        dataset.description = f"Tensor company data: {company['name']} ({company['industry_label']}). Synthetic template data."
        dataset.labels = {"app": "tensor", "company": re.sub(r"[^a-z0-9_-]", "-", company["id"])[:63]}
        bq.create_dataset(dataset, exists_ok=True)
        spec = templates.INDUSTRIES[company["industry"]]["tables"]
        rows = templates.generate(company["industry"], seed=company["id"])
        jobs = []
        for name, table in spec.items():
            schema = [bigquery.SchemaField(f["name"], f["type"], description=f["description"]) for f in table["fields"]]
            ref = f"{self.settings.project}.{company['dataset']}.{name}"
            config = bigquery.LoadJobConfig(schema=schema, write_disposition="WRITE_TRUNCATE",
                                            destination_table_description=table["description"])
            jobs.append(bq.load_table_from_json(rows[name], ref, job_config=config))
        for job in jobs:
            job.result(timeout=180)
        self.knowledge.describe(company["dataset"], refresh=True)

    # ---- company documents ---------------------------------------------------------------------------

    async def add_document(self, cid: str, filename: str, content: bytes, uploaded_by: str, wait: bool = False) -> dict:
        ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        mime = KB_MIME.get(ext)
        if not mime:
            raise ValueError("Upload a PDF, HTML, TXT or DOCX file.")
        if ext == ".pdf" and not content.startswith(b"%PDF"):
            raise ValueError("That file isn't a valid PDF.")
        doc_id = new_id("kb").replace("_", "-")
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", filename)[:120]
        gcs_uri = await asyncio.to_thread(self.knowledge.upload, f"companies/{cid}/kb/{doc_id}/{safe}", content, mime)
        doc = dict(id=doc_id, name=filename[:160], title=re.sub(r"\.[a-z0-9]+$", "", filename, flags=re.I)[:160], mime=mime,
                   size=len(content), gcs_uri=gcs_uri, status="INDEXING", uploaded_by=uploaded_by, created_at=now_iso(), epoch=time.time())
        await self.db.put(f"companies/{cid}/knowledge", doc_id, doc)
        job = self._index(cid, doc, content)
        if wait:
            await job
        else:
            self.background(job)
        return doc

    async def _index(self, cid: str, doc: dict, content: bytes) -> None:
        try:
            await asyncio.to_thread(self.knowledge.index_document, cid, doc["id"], doc["title"], content, doc["mime"])
            doc.update(status="READY", indexed_at=now_iso())
        except Exception as exc:
            log.exception("indexing failed")
            doc.update(status="FAILED", error=f"{type(exc).__name__}: {exc}"[:300])
        if await self.db.get(f"companies/{cid}/knowledge", doc["id"]):  # not deleted meanwhile
            await self.db.put(f"companies/{cid}/knowledge", doc["id"], doc)

    async def documents(self, cid: str) -> list[dict]:
        return sorted(await self.db.list(f"companies/{cid}/knowledge"), key=lambda d: d.get("epoch", 0), reverse=True)

    async def document(self, cid: str, doc_id: str) -> dict | None:
        return await self.db.get(f"companies/{cid}/knowledge", doc_id)

    async def delete_document(self, cid: str, doc_id: str) -> None:
        doc = await self.document(cid, doc_id)
        if not doc:
            raise ValueError("Document not found")
        await self.db.delete(f"companies/{cid}/knowledge", doc_id)
        await asyncio.to_thread(self.knowledge.remove_document, doc_id)
        await asyncio.to_thread(self.knowledge.delete_blob, doc["gcs_uri"])
