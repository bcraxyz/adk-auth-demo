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
DEFAULTS = {
    "mode": MODES[0],
    "messages": [],
    "session_id": None,
    "user_id": "demo-user",
    "consent_auth_uri": None,
    "auth_request_function_call_id": None,
    "auth_config": None,
    "auth_resume_pending": False,
}
for k, v in DEFAULTS.items():
    st.session_state.setdefault(k, v)

def reset_chat() -> None:
    st.session_state.messages = []
    st.session_state.session_id = None
    st.session_state.consent_auth_uri = None
    st.session_state.auth_request_function_call_id = None
    st.session_state.auth_config = None
    st.session_state.auth_resume_pending = False

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
def get_nonce_store() -> dict:
    """A global dictionary shared across all browser tabs to survive redirects."""
    return {}

def _gcp_token() -> str:
    creds, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    if not creds.valid:
        creds.refresh(Request())
    return creds.token

def maybe_finalize_3lo() -> None:
    qp = st.query_params
    state = qp.get("user_id_validation_state")
    provider = qp.get("auth_provider_name")

    nonce = get_nonce_store().get(st.session_state.user_id)
    if not (state and provider and nonce):
        return

    finalize_url = (
        f"https://iamconnectorcredentials.googleapis.com/v1alpha/"
        f"{provider}/credentials:finalize"
    )
    payload = {
        "userId": st.session_state.user_id,
        "userIdValidationState": state,
        "consentNonce": nonce,
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

    st.query_params.clear()
    st.session_state.consent_auth_uri = None
    st.session_state.auth_resume_pending = True
    get_nonce_store().pop(st.session_state.user_id, None)
    st.success("✓ Consent granted. Send your prompt again to continue.")

maybe_finalize_3lo()


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

    if st.session_state.consent_auth_uri:
        st.markdown("---")
        st.caption("Click the button to provide your consent. Once you're redirected here, send your prompt again to continue.")
        st.link_button("→ Authorize", st.session_state.consent_auth_uri)

for role, text in st.session_state.messages:
    with st.chat_message(role):
        st.markdown(text)

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
    if st.session_state.session_id:
        return st.session_state.session_id
    session = await remote_agent.async_create_session(user_id=st.session_state.user_id)
    sid = session["id"] if isinstance(session, dict) else session.id
    st.session_state.session_id = sid
    return sid

async def run_turn(user_prompt: str) -> str:
    session_id = await ensure_session()
    if st.session_state.auth_resume_pending and st.session_state.auth_config:
        message = genai_types.Content(
            role="user",
            parts=[
                genai_types.Part(
                    function_response=genai_types.FunctionResponse(
                        id=st.session_state.auth_request_function_call_id,
                        name="adk_request_credential",
                        response=st.session_state.auth_config,
                    )
                )
            ],
        )
        st.session_state.auth_resume_pending = False
        st.session_state.auth_request_function_call_id = None
        st.session_state.auth_config = None
    else:
        message = f"[Mode: {st.session_state.mode}] {user_prompt}"

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
                st.session_state.consent_auth_uri = auth_uri
                get_nonce_store()[st.session_state.user_id] = nonce
                st.session_state.auth_request_function_call_id = fc.get("id")
                st.session_state.auth_config = auth_config
                return ("I need your consent to act on your behalf. Please click the **Authorize** button.")

        content = ev.get("content") or {}
        if content.get("role") == "model":
            for part in content.get("parts", []) or []:
                if part.get("text"):
                    final_text.append(part["text"])

    return "".join(final_text) or "(no response)"

prompt = st.chat_input(
    "Try: 'list my storage buckets' / 'send a test email' / 'list users in Entra'"
)
if prompt:
    st.session_state.messages.append(("user", prompt))
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Thinking…"):
            reply = asyncio.run(run_turn(prompt))
        st.markdown(reply)
    st.session_state.messages.append(("assistant", reply))

    if st.session_state.consent_auth_uri:
        st.rerun()
