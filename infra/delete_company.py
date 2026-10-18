"""Delete a company and everything it owns (run with the backend venv; uses ADC). Irreversible.

Removes: its users, sessions and email reservations; invites; knowledge documents (Vertex AI Search + Cloud Storage);
meetings (Firestore); its BigQuery dataset; the company record.
Usage: python infra/delete_company.py <company_id> --yes
"""
from __future__ import annotations

import asyncio
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.config import Settings  # noqa: E402
from app.knowledge import CompanyKnowledge  # noqa: E402
from app.store import FirestoreBackend  # noqa: E402


async def main(cid: str) -> None:
    settings = Settings()
    db = FirestoreBackend(settings.project)
    knowledge = CompanyKnowledge(settings)
    company = await db.get("companies", cid)
    if not company:
        raise SystemExit(f"No company {cid}")
    for doc in await db.list(f"companies/{cid}/knowledge"):
        knowledge.remove_document(doc["id"])
        knowledge.delete_blob(doc["gcs_uri"])
        await db.delete(f"companies/{cid}/knowledge", doc["id"])
    users = await db.query("users", "company_id", cid)
    uids = {u["uid"] for u in users}
    for session in [s async for s in db.db.collection("sessions").stream()]:
        if session.to_dict().get("uid") in uids:
            await session.reference.delete()
    for u in users:
        await db.delete("user_emails", hashlib.sha256(u["email"].encode()).hexdigest())
        await db.delete("users", u["uid"])
    if company.get("invite_code"):
        await db.delete("invites", company["invite_code"])
    async for meeting in db.db.collection("meetings").list_documents():
        if meeting.id.startswith(f"{cid}__"):
            await db.db.recursive_delete(meeting)
    knowledge.bq.delete_dataset(f"{settings.project}.{company['dataset']}", delete_contents=True, not_found_ok=True)
    await db.delete("companies", cid)
    print(f"deleted {cid}: {len(users)} users, dataset {company['dataset']}")


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[2] != "--yes":
        raise SystemExit(__doc__)
    asyncio.run(main(sys.argv[1]))
