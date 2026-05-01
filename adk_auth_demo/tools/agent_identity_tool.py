"""Tool 1: Agent Identity → Cloud Storage."""

import os
import google.auth
import requests
from google.adk.tools import FunctionTool
from google.auth.transport.requests import Request
from google.cloud import storage

def _resolve_identity(creds) -> str:
    """Best-effort identity resolution. Local SA exposes service_account_email
    directly; agent identity on Runtime needs a tokeninfo lookup."""
    email = getattr(creds, "service_account_email", None)
    if email:
        return email
    if creds.token:
        try:
            r = requests.get(
                "https://oauth2.googleapis.com/tokeninfo",
                params={"access_token": creds.token},
                timeout=5,
            )
            if r.status_code == 200:
                p = r.json()
                return p.get("email") or p.get("sub") or "unknown"
        except requests.RequestException:
            pass
    return "unknown"

def list_gcs_buckets() -> dict:
    """List Cloud Storage buckets the agent identity can see in the project.
    Returns:
        A dict with the project ID, list of bucket names, and active identity.
    """
    creds, project = google.auth.default()
    project = project or os.getenv("GOOGLE_CLOUD_PROJECT")
    if not creds.valid:
        creds.refresh(Request())

    client = storage.Client(project=project, credentials=creds)
    buckets = [b.name for b in client.list_buckets()]
    return {
        "project": project,
        "bucket_count": len(buckets),
        "buckets": buckets,
        "identity_used": _resolve_identity(creds),
    }

def build() -> FunctionTool:
    return FunctionTool(func=list_gcs_buckets)
