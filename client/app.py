import asyncio
import os
import uuid
import google.auth
import httpx
import streamlit as st
import vertexai
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.genai import types as genai_types

load_dotenv()

st.set_page_config(page_title="ADK Auth Demo", page_icon="🔐", layout="wide")

MODES = ["Agent Identity", "OAuth 2LO", "OAuth 3LO", "API Key"]

# Initialize local session state (UI elements only)
if "mode" not in st.session_state:
    st.session_state.mode = MODES[0]
if "messages" not in st.session_state:
    st.session_state.messages = []
if "user_id" not in st.session_state:
    # Use a fixed user ID for the demo, or pull from headers if using IAP
    st.session_state.user_id = "demo-user"

def reset_chat() -> None:
    st.session_state.messages = []
    # Clear the global state for this user when resetting the chat
    if st.session_state.user_id in get_global_state():
        del get_global_state()[st.session_state.user_id]

@st.cache_resource(show_spinner=False)
def get_remote_agent():
    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    location = os.environ["GOOGLE_CLOUD_LOCATION"]
    resource_name = os.environ["AGENT_ENGINE_RESOURCE_NAME"]

    vertexai.init(project=project, location=location)
    client = vertexai.Client(
        project=project,
        location=location,
        http_options=dict(api_version="v1beta1"),
    )
    return client.agent_engines.get(name=resource_name)

remote_agent = get_remote_agent()

@st.cache_resource
def get_global_state() -> dict:
    """A global dictionary shared across all browser tabs to survive redirects.
    Structure: { user_id: { "nonce": str, "auth_config": dict, "fc_id": str, "session_id": str } }
    """
    return {}

def _gcp_token() -> str:
    creds, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    if not creds.valid:
        creds.refresh(Request())
    return creds.token

def maybe_finalize_3lo() -> None:
    """Checks URL params for callback and finalizes consent if needed."""
    qp = st.query_params
    state = qp.get("user_id_validation_state")
    provider = qp.get("auth_provider_name")

    # Bail out early if this isn't a callback
    if not (state and provider):
        return

    user_state = get_global_state().get(st.session_state.user_id)
    if not user_state or not user_state.get("nonce"):
        st.error("Callback received, but no pending authorization found for this user.")
        return

    finalize_url = (
        f"https://iamconnectorcredentials.googleapis.com/v1alpha/"
        f"{provider}/credentials:finalize"
    )
    payload = {
        "userId": st.session_state.user_id,
        "userIdValidationState": state,
        "consentNonce": user_state["nonce"],
    }
    
    try:
        resp = httpx.post(
            finalize_url,
            json=payload,
            headers={
                "Authorization": f"Bearer {_gcp_token()}",
                "Content-Type": "application/json",
            },
            timeout=15.0,
        )
        resp.raise_for_status()
    except httpx.HTTPError as e:
        st.error(f"Failed to finalize consent: {e}")
        return

    # Clear URL params so we don't finalize again on refresh
    st.query_params.clear()
    
    # Mark the global state as ready to resume
    user_state["resume_pending"] = True
    st.success("✓ Consent granted. The agent will now resume.")
    
    # Optional: Automatically trigger the resume so the user doesn't have to ask again.
    # We simulate the user clicking 'send' again.
    if user_state.get("last_prompt"):
        st.session_state.messages.append(("user", "(Resuming previous request...)"))
        st.rerun()

with st.sidebar:
    st.title("🔐 ADK Auth Demo")
    new_mode = st.radio(
        "Pick an authentication mode:",
        options=MODES,
        index=MODES.index(st.session_state.mode),
        key="mode_radio",
    )
    if new_mode != st.session_state.mode:
        st.session_state.mode = new_mode
        reset_chat()
        st.rerun()

    st.markdown("---")
    st.caption("**Agent Identity** — Lists GCS buckets using the agent's SPIFFE-bound identity.")
    st.caption("**OAuth 2LO** — Lists users in Entra using the agent's app-only token.")
    st.caption("**OAuth 3LO** — Lists users in Entra using delegation, with the agent acting as the signed-in user.")
    st.caption("**API Key** — Sends an email via Resend. API key fetched from Auth Manager at call time.")

    # Show Authorize button if global state indicates a pending auth URI
    user_state = get_global_state().get(st.session_state.user_id, {})
    if user_state.get("auth_uri"):
        st.caption("Click the button to provide your consent. Once you're redirected here, send your prompt again to continue.")
        #st.link_button("→ Authorize", st.session_state.consent_auth_uri)
        st.markdown(
            f'<a href="{user_state["auth_uri"]}" target="_self" style="display: inline-block; padding: 0.5rem 1rem; background-color: #2e66f5; color: white; text-decoration: none; border-radius: 0.5rem; text-align: center; width: 100%;">→ Authorize</a>', 
            unsafe_allow_html=True
        )

# Render chat history
for role, text in st.session_state.messages:
    with st.chat_message(role):
        st.markdown(text)

# --- Helper Functions ---
def _find_auth_request(event_dict: dict) -> dict | None:
    content = event_dict.get("content") or {}
    for part in content.get("parts", []) or []:
        fc = part.get("function_call")
        if fc and fc.get("name") == "adk_request_credential":
            return fc
    return None

def _extract_consent(fc: dict) -> tuple[str | None, str | None, dict | None]:
    args = fc.get("args") or {}
    auth_config = args.get("auth_config") or args.get("authConfig") or {}
    exchanged = auth_config.get("exchanged_auth_credential") or auth_config.get("exchangedAuthCredential") or {}
    oauth2 = exchanged.get("oauth2") or {}
    auth_uri = oauth2.get("auth_uri") or oauth2.get("authUri")
    nonce = oauth2.get("nonce")
    return auth_uri, nonce, auth_config

async def ensure_session() -> str:
    user_state = get_global_state().setdefault(st.session_state.user_id, {})
    
    if user_state.get("session_id"):
        return user_state["session_id"]
        
    session = await remote_agent.async_create_session(user_id=st.session_state.user_id)
    sid = session["id"] if isinstance(session, dict) else session.id
    user_state["session_id"] = sid
    return sid

async def run_turn(user_prompt: str) -> str:
    session_id = await ensure_session()
    user_state = get_global_state().get(st.session_state.user_id, {})

    # Check if we are resuming after a successful callback
    if user_state.get("resume_pending") and user_state.get("auth_config"):
        message = genai_types.Content(
            role="user",
            parts=[
                genai_types.Part(
                    function_response=genai_types.FunctionResponse(
                        id=user_state.get("fc_id"),
                        name="adk_request_credential",
                        response=user_state.get("auth_config"),
                    )
                )
            ],
        )
        # Clear the auth state so we don't get stuck in a loop
        user_state["resume_pending"] = False
        user_state["auth_uri"] = None
        user_state["nonce"] = None
        user_state["auth_config"] = None
        user_state["fc_id"] = None
    else:
        message = f"[Mode: {st.session_state.mode}] {user_prompt}"
        # Save the prompt in case we need to resume later
        user_state["last_prompt"] = user_prompt

    final_text: list[str] = []
    async for event in remote_agent.async_stream_query(
        user_id=st.session_state.user_id,
        session_id=session_id,
        message=message,
    ):
        ev = event if isinstance(event, dict) else event.model_dump()

        fc = _find_auth_request(ev)
        if fc:
            auth_uri, nonce, auth_config = _extract_consent(fc)
            if auth_uri and nonce:
                # Save ALL pending state to the global store
                user_state["auth_uri"] = auth_uri
                user_state["nonce"] = nonce
                user_state["auth_config"] = auth_config
                user_state["fc_id"] = fc.get("id")
                return "I need your consent to act on your behalf. Please click the **Authorize** button in the sidebar."

        content = ev.get("content") or {}
        if content.get("role") == "model":
            for part in content.get("parts", []) or []:
                if part.get("text"):
                    final_text.append(part["text"])

    return "".join(final_text) or "(no response)"

# Call this BEFORE rendering the chat input so the resume logic works
maybe_finalize_3lo()

# If we are resuming automatically, grab the last prompt
user_state = get_global_state().get(st.session_state.user_id, {})
if user_state.get("resume_pending") and user_state.get("last_prompt"):
    prompt = user_state["last_prompt"]
else:
    prompt = st.chat_input("Try: 'list my storage buckets' / 'send a test email' / 'list users in Entra'")

if prompt:
    if not user_state.get("resume_pending"):
        st.session_state.messages.append(("user", prompt))
        with st.chat_message("user"):
            st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Thinking…"):
            reply = asyncio.run(run_turn(prompt))
        st.markdown(reply)
    st.session_state.messages.append(("assistant", reply))

    # If an auth URI was set during this turn, rerun to show the button
    if get_global_state().get(st.session_state.user_id, {}).get("auth_uri"):
        st.rerun()
