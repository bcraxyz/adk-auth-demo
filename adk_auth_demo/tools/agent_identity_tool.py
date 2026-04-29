"""Tool 1: Agent Identity → Cloud Storage."""

import os
import requests
import google.auth
from google.auth.transport.requests import Request
from google.adk.tools import FunctionTool
from google.cloud import storage

def list_gcs_buckets() -> dict:
    """List Cloud Storage buckets the agent identity can see in the project.

    Returns:
        A dict with the project ID, a list of bucket names, and the active identity.
    """
    creds, project = google.auth.default()
    project = project or os.getenv("GOOGLE_CLOUD_PROJECT")

    if not creds.valid:
        creds.refresh(Request())

    actual_identity = "Unknown Identity"
    if creds.token:
        resp = requests.get(f"https://oauth2.googleapis.com/tokeninfo?access_token={creds.token}")
        if resp.status_code == 200:
            payload = resp.json()
            actual_identity = payload.get("email") or payload.get("sub") or str(payload)
    
    if actual_identity == "Unknown Identity" and hasattr(creds, "service_account_email"):
        actual_identity = creds.service_account_email
        
    client = storage.Client(project=project, credentials=creds)
    buckets = [b.name for b in client.list_buckets()]
    
    return {
        "project": project,
        "bucket_count": len(buckets),
        "buckets": buckets,
        "identity_used": actual_identity,
    }

def build() -> FunctionTool:
    return FunctionTool(func=list_gcs_buckets)
