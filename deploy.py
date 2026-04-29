import os
import vertexai

from dotenv import load_dotenv
load_dotenv()

from vertexai import types
from adk_auth_demo.agent import root_agent

def main() -> None:
    project_id = os.environ["GOOGLE_CLOUD_PROJECT"]
    location = os.environ["GOOGLE_CLOUD_LOCATION"]
    bucket = os.environ["GOOGLE_CLOUD_STORAGE_BUCKET"]

    # The deployed agent reads these from os.environ at runtime, so we
    # pass them through verbatim.
    runtime_env = {
        k: os.environ[k]
        for k in (
            "AUTH_PROVIDER_RESEND",
            "AUTH_PROVIDER_MSGRAPH_2LO",
            "AUTH_PROVIDER_MSGRAPH_3LO",
            "RESEND_FROM_EMAIL",
            "REDIRECT_URI",
        )
        if k in os.environ
    }

    client = vertexai.Client(
        project=project_id,
        location=location,
        http_options=dict(api_version="v1beta1"),
    )

    remote_agent = client.agent_engines.create(
        agent=root_agent,
        config={
            "display_name": root_agent.name,
            "identity_type": types.IdentityType.AGENT_IDENTITY,
            "requirements": [
                "google-adk[agent-identity]",
                "google-cloud-aiplatform[agent_engines]",
                "google-genai",
                "google-cloud-storage",
                "google-auth",
                "resend",
            ],
            "extra_packages": [f"./{root_agent.name}"],
            "staging_bucket": f"gs://{bucket}",
            "env_vars": runtime_env,
        },
    )

    print(f"✅ Deployed agent: {remote_agent.resource_name}")

if __name__ == "__main__":
    main()
