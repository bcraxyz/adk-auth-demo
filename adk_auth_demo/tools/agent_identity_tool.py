"""Tool 1: Agent Identity → Cloud Storage.

When deployed to Agent Runtime with identity_type=AGENT_IDENTITY, the
agent gets a SPIFFE-bound token via ADC. The principal is
`principal://agents.global.org-<ORG>.system.id.goog/...` — grant
roles/storage.objectViewer to that principal at the project level.
"""

import os

import google.auth
from google.adk.tools import FunctionTool
from google.cloud import storage


def list_gcs_buckets() -> dict:
    """List Cloud Storage buckets the agent identity can see in the project.

    Returns:
        A dict with the project ID and a list of bucket names.
    """
    creds, project = google.auth.default()
    project = project or os.getenv("GOOGLE_CLOUD_PROJECT")
    client = storage.Client(project=project, credentials=creds)
    buckets = [b.name for b in client.list_buckets()]
    return {
        "project": project,
        "bucket_count": len(buckets),
        "buckets": buckets,
        "identity_used": "agent identity (SPIFFE-bound on Runtime)",
    }


def build() -> FunctionTool:
    return FunctionTool(func=list_gcs_buckets)
