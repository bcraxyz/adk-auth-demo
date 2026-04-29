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
    agent_id = os.environ.get("AGENT_ENGINE_RESOURCE_NAME")

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

    agent_config={
        "display_name": root_agent.name,
        "identity_type": types.IdentityType.AGENT_IDENTITY,
        "requirements": [
            "google-adk[agent-identity]",
            "google-cloud-aiplatform[agent_engines]",
            "google-genai",
            "google-cloud-storage",
            "google-auth",
            "resend",
            "pydantic",
            "cloudpickle",
        ],
        "extra_packages": [f"./{root_agent.name}"],
        "staging_bucket": f"gs://{bucket}",
        "env_vars": runtime_env,
    }

    if agent_id:
        print(f"🔄 Updating existing agent: {agent_id}...")
        remote_agent = client.agent_engines.update(
            name=agent_id,
            agent=root_agent,
            config=agent_config,
        )
    else:
        print("🚀 Creating a new agent...")
        remote_agent = client.agent_engines.create(
            agent=root_agent,
            config=agent_config,
        )

    print(f"✅ Deployed agent: {remote_agent.api_resource.name}")

if __name__ == "__main__":
    main()
