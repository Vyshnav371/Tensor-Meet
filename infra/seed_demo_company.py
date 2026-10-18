"""Seed the original demo as a company: Acme Fintech (run once with the backend venv; uses ADC; idempotent).

  - companies/acme-fintech with the demo team (plus-addresses of the operator) and Jira key SEC
  - BigQuery dataset tensor_co_acme_fintech with product_metrics copied from tensor.product_metrics
  - the 8 knowledge documents in ../knowledge indexed for this company in Vertex AI Search
Prints the invite link. The first person to join through it becomes the company's admin.

Usage: python infra/seed_demo_company.py [--base https://<service-url>]
"""
from __future__ import annotations

import argparse
import asyncio
import re
import secrets
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import templates  # noqa: E402
from app.config import Settings, directory  # noqa: E402
from app.knowledge import CompanyKnowledge  # noqa: E402
from app.store import FirestoreBackend, now_iso  # noqa: E402

CID, NAME = "acme-fintech", "Acme Fintech"
DESCRIPTIONS = {
    "day": "Calendar day (UTC)", "dau": "Daily active users", "wau": "Weekly active users (7-day rolling)",
    "payment_success_rate": "Successful / attempted payments, 0-1", "api_p95_ms": "Payments API 95th percentile latency in milliseconds",
    "support_tickets": "New support tickets",
}


async def main(base: str) -> None:
    settings = Settings()
    db = FirestoreBackend(settings.project)
    knowledge = CompanyKnowledge(settings)
    dataset = "tensor_co_" + CID.replace("-", "_")

    company = await db.get("companies", CID)
    code = (company or {}).get("invite_code") or secrets.token_urlsafe(9)
    team = [dict(key=p["key"], name=p["name"], role=p["role"], email=p["email"], project=p.get("project"), source="template") for p in directory(settings)]
    if company:  # keep members who already joined
        team += [p for p in company.get("team", []) if p.get("source") == "member"]
    await db.put("companies", CID, {**(company or {}), **dict(
        id=CID, name=NAME, industry="demo", industry_label="Fintech / payments (demo)", jira_key="SEC", dataset=dataset,
        invite_code=code, status="READY", created_by=(company or {}).get("created_by"), created_at=(company or {}).get("created_at", now_iso()),
        team=team, examples=templates.examples("demo"))})
    await db.put("invites", code, dict(company_id=CID))
    print("company", CID, "ready; invite code", code)

    from google.cloud import bigquery

    bq = knowledge.bq
    ds = bigquery.Dataset(f"{settings.project}.{dataset}")
    ds.location = settings.bq_location
    ds.description = "Tensor company data: Acme Fintech (demo). Synthetic."
    bq.create_dataset(ds, exists_ok=True)
    bq.query(f"CREATE OR REPLACE TABLE `{settings.project}.{dataset}.product_metrics` "
             f"OPTIONS(description='One row per day of product metrics (synthetic demo data).') AS "
             f"SELECT * FROM `{settings.project}.{settings.bq_dataset}.product_metrics`").result()
    table = bq.get_table(f"{settings.project}.{dataset}.product_metrics")
    table.schema = [bigquery.SchemaField(f.name, f.field_type, mode=f.mode, description=DESCRIPTIONS.get(f.name, f.description or ""))
                    for f in table.schema]
    bq.update_table(table, ["schema"])
    print("bigquery", dataset, "product_metrics", table.num_rows, "rows")

    existing = {d["id"] for d in await db.list(f"companies/{CID}/knowledge")}
    for path in sorted((ROOT / "knowledge").glob("*.html")):
        doc_id = "kb-acme-" + path.stem
        if doc_id in existing:
            continue
        content = path.read_bytes()
        title = re.search(r"<title>(.*?)</title>", content.decode("utf-8"), re.S).group(1).strip()
        gcs_uri = knowledge.upload(f"companies/{CID}/kb/{doc_id}/{path.name}", content, "text/html")
        knowledge.index_document(CID, doc_id, title, content, "text/html")
        await db.put(f"companies/{CID}/knowledge", doc_id, dict(
            id=doc_id, name=path.name, title=title, mime="text/html", size=len(content), gcs_uri=gcs_uri, status="READY",
            uploaded_by="Tensor", created_at=now_iso(), indexed_at=now_iso(), epoch=time.time()))
        print("indexed", title)
    print(f"\nInvite link: {base.rstrip('/')}/invite/{code}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:8000")
    asyncio.run(main(parser.parse_args().base))
