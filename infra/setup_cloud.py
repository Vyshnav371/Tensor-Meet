"""Idempotent Google Cloud setup for Tensor (run with the backend venv; uses ADC).

Creates:
  - Vertex AI Search data store `tensor-company-kb` (global), imports gs://<bucket>/kb/*.html,
    and an Enterprise search engine `tensor-search` with the LLM add-on (summaries + citations).
  - Document AI Layout Parser processor `tensor-layout-parser` (us).
  - BigQuery tables tensor.product_metrics (120 days of synthetic daily metrics) and tensor.action_audit.
Prints the resource names to put in backend configuration.
"""
from __future__ import annotations

import math
import random
from datetime import date, timedelta

from google.api_core.exceptions import AlreadyExists, Conflict, NotFound
from google.cloud import bigquery
from google.cloud import discoveryengine_v1 as de
from google.cloud import documentai_v1 as dai

PROJECT = "ai-builder-nexusforge"
BUCKET = "ai-builder-nexusforge-tensor"
DATA_STORE = "tensor-company-kb"
ENGINE = "tensor-search"
COLLECTION = f"projects/{PROJECT}/locations/global/collections/default_collection"


def vertex_ai_search() -> None:
    stores = de.DataStoreServiceClient()
    try:
        op = stores.create_data_store(
            parent=COLLECTION,
            data_store_id=DATA_STORE,
            data_store=de.DataStore(
                display_name="Tensor company knowledge base",
                industry_vertical=de.IndustryVertical.GENERIC,
                solution_types=[de.SolutionType.SOLUTION_TYPE_SEARCH],
                content_config=de.DataStore.ContentConfig.CONTENT_REQUIRED,
            ),
        )
        op.result(timeout=300)
        print("created data store", DATA_STORE)
    except AlreadyExists:
        print("data store exists", DATA_STORE)

    docs = de.DocumentServiceClient()
    op = docs.import_documents(request=de.ImportDocumentsRequest(
        parent=f"{COLLECTION}/dataStores/{DATA_STORE}/branches/default_branch",
        gcs_source=de.GcsSource(input_uris=[f"gs://{BUCKET}/kb/*.html"], data_schema="content"),
        reconciliation_mode=de.ImportDocumentsRequest.ReconciliationMode.INCREMENTAL,
    ))
    print("import started:", op.operation.name)

    engines = de.EngineServiceClient()
    try:
        op = engines.create_engine(
            parent=COLLECTION,
            engine_id=ENGINE,
            engine=de.Engine(
                display_name="Tensor search",
                solution_type=de.SolutionType.SOLUTION_TYPE_SEARCH,
                industry_vertical=de.IndustryVertical.GENERIC,
                data_store_ids=[DATA_STORE],
                search_engine_config=de.Engine.SearchEngineConfig(
                    search_tier=de.SearchTier.SEARCH_TIER_ENTERPRISE,
                    search_add_ons=[de.SearchAddOn.SEARCH_ADD_ON_LLM],
                ),
            ),
        )
        op.result(timeout=300)
        print("created engine", ENGINE)
    except AlreadyExists:
        print("engine exists", ENGINE)
    print(f"serving config: {COLLECTION}/engines/{ENGINE}/servingConfigs/default_search")


def document_ai() -> None:
    client = dai.DocumentProcessorServiceClient(client_options={"api_endpoint": "us-documentai.googleapis.com"})
    parent = client.common_location_path(PROJECT, "us")
    for p in client.list_processors(parent=parent):
        if p.display_name == "tensor-layout-parser":
            print("layout parser exists:", p.name)
            return
    p = client.create_processor(parent=parent, processor=dai.Processor(type_="LAYOUT_PARSER_PROCESSOR", display_name="tensor-layout-parser"))
    print("created layout parser:", p.name)


def bigquery_tables() -> None:
    bq = bigquery.Client(project=PROJECT, location="asia-south1")
    metrics = bigquery.Table(f"{PROJECT}.tensor.product_metrics", schema=[
        bigquery.SchemaField("day", "DATE", "REQUIRED"),
        bigquery.SchemaField("dau", "INTEGER"),
        bigquery.SchemaField("wau", "INTEGER"),
        bigquery.SchemaField("payment_success_rate", "FLOAT"),
        bigquery.SchemaField("api_p95_ms", "INTEGER"),
        bigquery.SchemaField("support_tickets", "INTEGER"),
    ])
    metrics.description = "Synthetic daily product metrics for the Tensor demo (Acme Fintech)."
    audit = bigquery.Table(f"{PROJECT}.tensor.action_audit", schema=[
        bigquery.SchemaField("action_id", "STRING", "REQUIRED"),
        bigquery.SchemaField("card_id", "STRING"),
        bigquery.SchemaField("meeting_id", "STRING"),
        bigquery.SchemaField("card_type", "STRING"),
        bigquery.SchemaField("status", "STRING"),
        bigquery.SchemaField("approved_by", "STRING"),
        bigquery.SchemaField("approval_channel", "STRING"),
        bigquery.SchemaField("executor", "STRING"),
        bigquery.SchemaField("external_refs", "STRING", "REPEATED"),
        bigquery.SchemaField("payload_json", "STRING"),
        bigquery.SchemaField("grounding_sources", "STRING", "REPEATED"),
        bigquery.SchemaField("executed_at", "TIMESTAMP"),
    ])
    audit.description = "Append-only log of every human-approved Tensor action (email, Jira)."
    audit.time_partitioning = bigquery.TimePartitioning(field="executed_at")
    for table in (metrics, audit):
        try:
            bq.create_table(table)
            print("created", table.table_id)
        except Conflict:
            print("exists", table.table_id)

    if not list(bq.query(f"SELECT 1 FROM `{PROJECT}.tensor.product_metrics` LIMIT 1").result()):
        rng = random.Random(42)
        rows, start = [], date(2026, 6, 18)
        for i in range(120):
            day = start + timedelta(days=i)
            weekday = day.weekday()
            dau = int(182000 * (1 + 0.0022 * i) * (0.88 if weekday >= 5 else 1.0) * (1 + rng.gauss(0, 0.015)))
            if day == date(2026, 10, 7):  # demo incident: latency spike and lower success rate
                p95, success, tickets = 612, 0.9612, 1890
            else:
                p95 = int(205 + 18 * math.sin(i / 9) + rng.gauss(0, 8))
                success = round(min(0.995, 0.984 + 0.00004 * i + rng.gauss(0, 0.0015)), 4)
                tickets = int(640 + rng.gauss(0, 45))
            rows.append(dict(day=day.isoformat(), dau=dau, wau=int(dau * 3.1), payment_success_rate=success,
                             api_p95_ms=p95, support_tickets=tickets))
        bq.load_table_from_json(rows, f"{PROJECT}.tensor.product_metrics").result()
        print("loaded", len(rows), "metric rows")


if __name__ == "__main__":
    bigquery_tables()
    document_ai()
    vertex_ai_search()
