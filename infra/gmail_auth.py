"""Authorize Tensor to send email from your Gmail account (run once on your own machine; uses ADC for Secret Manager).

1. Google Cloud console -> Google Auth Platform: set up the app (External; add your Gmail as a test user, then
   "Publish app" so the authorization doesn't expire after 7 days), and create an OAuth client of type "Desktop app".
   Download its JSON (keep it outside the repo; client_secret*.json is git-ignored).
2. python infra/gmail_auth.py <path-to-client_secret.json>
   A browser opens: sign in with the Gmail account Tensor should send from and allow "Send email on your behalf".
3. The client id, client secret, refresh token and sender address go straight into Secret Manager
   (tensor-gmail-client-id, -client-secret, -refresh-token, -sender). Nothing is printed or written to disk.
4. Run deploy.cmd: it grants the Cloud Run service account access and switches Gmail from sandbox to live.

Needs: pip install google-auth-oauthlib (dev only).
"""
from __future__ import annotations

import json
import sys

import httpx
from google.api_core.exceptions import AlreadyExists
from google.cloud import secretmanager
from google_auth_oauthlib.flow import InstalledAppFlow

PROJECT = "ai-builder-nexusforge"
SCOPES = ["https://www.googleapis.com/auth/gmail.send", "openid", "https://www.googleapis.com/auth/userinfo.email"]


def store(client: secretmanager.SecretManagerServiceClient, name: str, value: str) -> None:
    parent = f"projects/{PROJECT}"
    try:
        client.create_secret(parent=parent, secret_id=name, secret={"replication": {"automatic": {}}})
    except AlreadyExists:
        pass
    client.add_secret_version(parent=f"{parent}/secrets/{name}", payload={"data": value.encode()})


def main(path: str) -> None:
    config = json.load(open(path, encoding="utf-8"))
    app = config.get("installed") or config.get("web") or {}
    flow = InstalledAppFlow.from_client_config(config, SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline",
                                  success_message="Tensor can now send email from this account. You can close this tab.")
    if not creds.refresh_token:
        raise SystemExit("Google returned no refresh token. Remove Tensor's access at myaccount.google.com/permissions and run again.")
    sender = httpx.get("https://openidconnect.googleapis.com/v1/userinfo", headers={"Authorization": f"Bearer {creds.token}"}).json()["email"]
    client = secretmanager.SecretManagerServiceClient()
    for name, value in (("tensor-gmail-client-id", app["client_id"]), ("tensor-gmail-client-secret", app["client_secret"]),
                        ("tensor-gmail-refresh-token", creds.refresh_token), ("tensor-gmail-sender", sender)):
        store(client, name, value)
    print(f"Stored Gmail authorization for {sender} in Secret Manager. Now run deploy.cmd to switch Gmail to live.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
