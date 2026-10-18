"""Idempotent Google Cloud setup for multi-company Tensor (run with the backend venv; uses ADC).

Creates:
  - Vertex AI Search data store `tensor-companies-kb` (global, unstructured documents with metadata) whose
    `company_id` field is filterable, so every company searches only its own documents.
  - Enterprise search engine `tensor-companies` over that data store (extractive answers and segments).
Prints the serving config to put in TENSOR_SEARCH_SERVING_CONFIG.
"""
from __future__ import annotations

import json

from google.api_core.exceptions import AlreadyExists
from google.cloud import discoveryengine_v1 as de

PROJECT = "ai-builder-nexusforge"
DATA_STORE = "tensor-companies-kb"
ENGINE = "tensor-companies"
COLLECTION = f"projects/{PROJECT}/locations/global/collections/default_collection"

SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "company_id": {"type": "string", "indexable": True, "retrievable": True},
        "doc_id": {"type": "string", "retrievable": True},
        "title": {"type": "string", "retrievable": True, "keyPropertyMapping": "title"},
    },
}


def main() -> None:
    stores = de.DataStoreServiceClient()
    try:
        stores.create_data_store(
            parent=COLLECTION, data_store_id=DATA_STORE,
            data_store=de.DataStore(display_name="Tensor company knowledge (multi-company)",
                                    industry_vertical=de.IndustryVertical.GENERIC,
                                    solution_types=[de.SolutionType.SOLUTION_TYPE_SEARCH],
                                    content_config=de.DataStore.ContentConfig.CONTENT_REQUIRED),
        ).result(timeout=600)
        print("created data store", DATA_STORE)
    except AlreadyExists:
        print("data store exists", DATA_STORE)

    schemas = de.SchemaServiceClient()
    name = f"{COLLECTION}/dataStores/{DATA_STORE}/schemas/default_schema"
    schemas.update_schema(request=de.UpdateSchemaRequest(
        schema=de.Schema(name=name, json_schema=json.dumps(SCHEMA)), allow_missing=True)).result(timeout=600)
    print("schema: company_id is filterable")

    engines = de.EngineServiceClient()
    try:
        engines.create_engine(
            parent=COLLECTION, engine_id=ENGINE,
            engine=de.Engine(display_name="Tensor companies search", solution_type=de.SolutionType.SOLUTION_TYPE_SEARCH,
                             industry_vertical=de.IndustryVertical.GENERIC, data_store_ids=[DATA_STORE],
                             search_engine_config=de.Engine.SearchEngineConfig(
                                 search_tier=de.SearchTier.SEARCH_TIER_ENTERPRISE,
                                 search_add_ons=[de.SearchAddOn.SEARCH_ADD_ON_LLM])),
        ).result(timeout=600)
        print("created engine", ENGINE)
    except AlreadyExists:
        print("engine exists", ENGINE)
    print(f"TENSOR_SEARCH_SERVING_CONFIG={COLLECTION}/engines/{ENGINE}/servingConfigs/default_search")
    print(f"TENSOR_SEARCH_BRANCH={COLLECTION}/dataStores/{DATA_STORE}/branches/default_branch")


if __name__ == "__main__":
    main()
