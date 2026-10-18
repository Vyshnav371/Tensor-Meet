"""Company knowledge, per company: Vertex AI Search (RAG over the company's documents) and BigQuery (its data).

Isolation:
  - Search: one data store; every document carries `company_id`, and every search filters on it.
  - Data: each company has its own BigQuery dataset. Model-written SQL is accepted only when a dry run shows a
    single SELECT whose referenced tables all live in that dataset, within a byte budget; it then runs with
    maximum_bytes_billed. INFORMATION_SCHEMA and multi-statement scripts are refused outright.
"""
from __future__ import annotations

import re
import time

from .config import Settings

MAX_BYTES = 100 * 1024**2
MAX_ROWS = 200
KB_MIME = {".pdf": "application/pdf", ".html": "text/html", ".htm": "text/html", ".txt": "text/plain",
           ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
_FORBIDDEN_SQL = re.compile(r"information_schema|\bexternal_query\b|\bsession_user\b", re.I)


def doc_link(doc_id: str) -> str:
    return f"/api/company/knowledge/{doc_id}/file"


class SqlRejected(ValueError):
    pass


class CompanyKnowledge:
    def __init__(self, settings: Settings, search_client=None, bq_client=None, docs_client=None, storage_client=None):
        self.settings = settings
        self._search = search_client
        self._bq = bq_client
        self._docs = docs_client
        self._storage = storage_client
        self._schemas: dict[str, tuple[float, dict]] = {}

    # ---- clients ---------------------------------------------------------------------------------

    @property
    def search_client(self):
        if self._search is None:
            from google.cloud import discoveryengine_v1 as de

            self._search = de.SearchServiceClient()
        return self._search

    @property
    def docs_client(self):
        if self._docs is None:
            from google.cloud import discoveryengine_v1 as de

            self._docs = de.DocumentServiceClient()
        return self._docs

    @property
    def storage(self):
        if self._storage is None:
            from google.cloud import storage

            self._storage = storage.Client(project=self.settings.project)
        return self._storage

    @property
    def bq(self):
        if self._bq is None:
            from google.cloud import bigquery

            # Short-query mode: small queries return in ~0.3 s instead of ~1.4 s (no job to create and poll).
            self._bq = bigquery.Client(project=self.settings.project, location=self.settings.bq_location,
                                       default_job_creation_mode="JOB_CREATION_OPTIONAL")
        return self._bq

    # ---- Vertex AI Search ------------------------------------------------------------------------------

    def search(self, query: str, company_id: str) -> dict:
        from google.cloud import discoveryengine_v1 as de

        if not re.fullmatch(r"[a-z0-9-]{2,40}", company_id or ""):
            raise ValueError("No company in context")
        spec = de.SearchRequest.ContentSearchSpec
        response = self.search_client.search(de.SearchRequest(
            serving_config=self.settings.search_serving_config, query=query, page_size=5,
            filter=f'company_id: ANY("{company_id}")',
            content_search_spec=spec(
                snippet_spec=spec.SnippetSpec(return_snippet=True),
                extractive_content_spec=spec.ExtractiveContentSpec(max_extractive_answer_count=1, max_extractive_segment_count=2),
            ),
        ))
        results = []
        for i, r in enumerate(response.results, start=1):
            data = de.Document.to_dict(r.document)
            derived = data.get("derived_struct_data", {}) or {}
            meta = data.get("struct_data", {}) or {}
            if meta.get("company_id") != company_id:  # defence in depth: never surface another company's document
                continue
            segments = [s.get("content", "") for s in derived.get("extractive_segments", [])]
            answers = [a.get("content", "") for a in derived.get("extractive_answers", [])]
            snippets = [s.get("snippet", "") for s in derived.get("snippets", [])]
            results.append(dict(source_id=i, title=meta.get("title") or derived.get("title") or r.document.id, link=doc_link(r.document.id),
                                extracts=[t for t in answers + segments if t][:3], snippet=re.sub(r"</?b>", "", (snippets or [""])[0])))
        return dict(query=query, results=results,
                    note="Cite sources as [title](link). Extracts are verbatim from company documents."
                         + ("" if results else " No matching company documents were found."))

    def index_document(self, company_id: str, doc_id: str, title: str, content: bytes, mime: str) -> None:
        """Add one document to the company's searchable knowledge (blocks until imported, ~1 minute).
        Inline bytes become searchable within minutes; a gs:// link waits for a slower crawl."""
        from google.cloud import discoveryengine_v1 as de

        op = self.docs_client.import_documents(request=de.ImportDocumentsRequest(
            parent=self.settings.search_branch,
            inline_source=de.ImportDocumentsRequest.InlineSource(documents=[de.Document(
                id=doc_id, struct_data=dict(company_id=company_id, doc_id=doc_id, title=title),
                content=de.Document.Content(raw_bytes=content, mime_type=mime))]),
            reconciliation_mode=de.ImportDocumentsRequest.ReconciliationMode.INCREMENTAL))
        result = op.result(timeout=900)
        if result.error_samples:
            raise RuntimeError(result.error_samples[0].message)

    def remove_document(self, doc_id: str) -> None:
        from google.api_core.exceptions import NotFound

        try:
            self.docs_client.delete_document(name=f"{self.settings.search_branch}/documents/{doc_id}")
        except NotFound:
            pass

    def upload(self, path: str, content: bytes, mime: str) -> str:
        self.storage.bucket(self.settings.bucket).blob(path).upload_from_string(content, content_type=mime)
        return f"gs://{self.settings.bucket}/{path}"

    def download(self, gcs_uri: str) -> bytes:
        bucket, _, path = gcs_uri.removeprefix("gs://").partition("/")
        return self.storage.bucket(bucket).blob(path).download_as_bytes()

    def delete_blob(self, gcs_uri: str) -> None:
        from google.api_core.exceptions import NotFound

        bucket, _, path = gcs_uri.removeprefix("gs://").partition("/")
        try:
            self.storage.bucket(bucket).blob(path).delete()
        except NotFound:
            pass

    # ---- BigQuery: the company's own dataset ------------------------------------------------------------

    def describe(self, dataset: str, refresh: bool = False) -> dict:
        """Tables, columns (with descriptions), row counts and date ranges; cached for 10 minutes."""
        cached = self._schemas.get(dataset)
        if cached and not refresh and time.time() - cached[0] < 600:
            return cached[1]
        tables = []
        for item in self.bq.list_tables(f"{self.settings.project}.{dataset}"):
            table = self.bq.get_table(item.reference)
            info = dict(name=table.table_id, description=table.description or "", rows=table.num_rows,
                        columns=[dict(name=f.name, type=f.field_type, description=f.description or "") for f in table.schema])
            if any(f.name == "day" and f.field_type == "DATE" for f in table.schema):
                row = next(iter(self.bq.query(f"SELECT MIN(day) AS first_day, MAX(day) AS last_day FROM `{table.full_table_id.replace(':', '.')}`").result()))
                info.update(first_day=row["first_day"].isoformat() if row["first_day"] else None,
                            last_day=row["last_day"].isoformat() if row["last_day"] else None)
            tables.append(info)
        result = dict(dataset=dataset, tables=tables)
        self._schemas[dataset] = (time.time(), result)
        return result

    def schema_text(self, dataset: str) -> str:
        """Compact schema for the data agent's instructions."""
        try:
            info = self.describe(dataset)
        except Exception as exc:  # dataset still provisioning, or missing
            return f"(company data unavailable: {type(exc).__name__})"
        lines = []
        for t in info["tables"]:
            span = f"; days {t['first_day']} to {t['last_day']}" if t.get("last_day") else ""
            lines.append(f"- {t['name']} ({t['rows']} rows{span}): {t['description']}")
            lines += [f"    {c['name']} {c['type']}: {c['description']}" for c in t["columns"]]
        return "\n".join(lines) or "(no tables yet)"

    def check_sql(self, dataset: str, sql: str):
        """Dry-run guard; returns the job config to execute with, or raises SqlRejected."""
        from google.cloud import bigquery

        sql = sql.strip().rstrip(";").strip()
        if ";" in sql or not re.match(r"(?is)^\s*(select|with)\b", sql):
            raise SqlRejected("Only a single SELECT query is allowed.")
        if _FORBIDDEN_SQL.search(sql):
            raise SqlRejected("System tables are not available; query the company tables listed in your instructions.")
        default = bigquery.DatasetReference(self.settings.project, dataset)
        dry = self.bq.query(sql, job_config=bigquery.QueryJobConfig(dry_run=True, use_query_cache=False, default_dataset=default))
        if dry.statement_type != "SELECT":
            raise SqlRejected("Only SELECT statements are allowed.")
        outside = [f"{t.project}.{t.dataset_id}.{t.table_id}" for t in dry.referenced_tables
                   if t.project != self.settings.project or t.dataset_id != dataset]
        if outside or not dry.referenced_tables:
            raise SqlRejected("Queries may only read this company's tables.")
        if (dry.total_bytes_processed or 0) > MAX_BYTES:
            raise SqlRejected("Query would scan too much data; add filters.")
        return sql, bigquery.QueryJobConfig(default_dataset=default, maximum_bytes_billed=MAX_BYTES, use_query_cache=True,
                                            labels={"app": "tensor"})

    def query(self, dataset: str, sql: str) -> dict:
        try:
            sql, config = self.check_sql(dataset, sql)
            result = self.bq.query_and_wait(sql, job_config=config, wait_timeout=30, max_results=MAX_ROWS)
            rows = [{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in dict(r).items()} for r in result][:MAX_ROWS]
        except SqlRejected as exc:
            return dict(error=str(exc), sql=sql)
        except Exception as exc:  # BigQuery syntax/semantic errors go back to the model to fix
            message = getattr(exc, "message", None) or str(exc)
            return dict(error=f"BigQuery error: {message[:400]}", sql=sql)
        return dict(sql=sql, rows=rows, row_count=len(rows), truncated=len(rows) >= MAX_ROWS,
                    source=f"BigQuery {dataset} (company data)")
