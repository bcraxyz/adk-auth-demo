import asyncio
import os
import google.auth
import httpx
import streamlit as st
import vertexai
from dotenv import load_dotenv
from google.auth.transport.requests import Request

load_dotenv()

st.set_page_config(page_title="ADK Auth Demo", page_icon="🔐", layout="wide")

MODES = ["Agent Identity", "OAuth 2LO", "OAuth 3LO", "API Key"]

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

@st.cache_resource
def get_global_state() -> dict:
    """Process-wide state that survives the OAuth redirect (which spawns
    a new Streamlit session). Keyed by user_id."""
    return {}

remote_agent = get_remote_agent()

if "user_id" not in st.session_state:
    st.session_state.user_id = "demo-user"
if "messages" not in st.session_state:
    st.session_state.messages = []
if "mode" not in st.session_state:
    # Restore mode from global state if it survived an OAuth redirect.
    st.session_state.mode = (
        get_global_state().get(st.session_state.user_id, {}).get("mode", MODES[0])
    )

def _user_state() -> dict:
    return get_global_state().setdefault(st.session_state.user_id, {})

def reset_chat() -> None:
    """Switch modes: clear messages but keep any pending auth state alive."""
    st.session_state.messages = []
    us = _user_state()
    us.pop("session_id", None)

def _gcp_token() -> str:
    creds, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    if not creds.valid:
        creds.refresh(Request())
    return creds.token

def maybe_finalize_3lo() -> None:
    """Auth Manager redirects back with `user_id_validation_state` and
    `connector_name`. Finalize the consent and clear the pending UI state."""
    qp = st.query_params
    state = qp.get("user_id_validation_state")
    provider = qp.get("connector_name")
    if not (state and provider):
        return

    us = _user_state()
    nonce = us.get("nonce")
    if not nonce:
        st.error("Callback received, but no pending authorization for this user.")
        st.query_params.clear()
        return

    finalize_url = (
        f"https://iamconnectorcredentials.googleapis.com/v1alpha/"
        f"{provider}/credentials:finalize"
    )
    try:
        resp = httpx.post(
            finalize_url,
            json={
                "userId": st.session_state.user_id,
                "userIdValidationState": state,
                "consentNonce": nonce,
            },
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

    # Consent stored in Auth Manager vault. The ADK will pick it up on the
    # next agent call without re-prompting. Drop all pending UI state.
    for k in ("auth_uri", "nonce", "fc_id", "auth_config"):
        us.pop(k, None)
    st.query_params.clear()
    st.success("✓ Consent granted. Re-send your prompt to continue.")

maybe_finalize_3lo()

with st.sidebar:
    st.title("🔐 ADK Auth Demo")
    new_mode = st.radio(
        "Authentication mode:",
        options=MODES,
        index=MODES.index(st.session_state.mode),
        key="mode_radio",
    )
    if new_mode != st.session_state.mode:
        st.session_state.mode = new_mode
        _user_state()["mode"] = new_mode
        reset_chat()
        st.rerun()
    else:
        _user_state()["mode"] = new_mode  # persist on every render

    st.markdown("---")
    st.caption("**Agent Identity** — Lists GCS buckets using the agent's SPIFFE-bound identity.")
    st.caption("**OAuth 2LO** — Lists users in Entra using the agent's app-only token.")
    st.caption("**OAuth 3LO** — Same query as 2LO, but as the signed-in user. Graph constrains the result.")
    st.caption("**API Key** — Sends an email via Resend. Key fetched from Auth Manager at call time.")

    if _user_state().get("auth_uri"):
        st.markdown("---")
        st.caption("Click to authorize. After redirect, send your prompt again.")
        st.link_button("→ Authorize", _user_state()["auth_uri"], use_container_width=True)

    # ── DIAGNOSTIC ────────────────────────────────────────
    with st.expander("debug: user_state", expanded=False):
        st.write({k: (v if k != "auth_uri" else f"{str(v)[:60]}...") for k, v in _user_state().items()})
        if "_last_consent_extract" in st.session_state:
            st.write("last extract:", st.session_state["_last_consent_extract"])
    # ── END DIAGNOSTIC ────────────────────────────────────────

for role, text in st.session_state.messages:
    with st.chat_message(role):
        st.markdown(text)

def _find_auth_request(event_dict: dict) -> dict | None:
    content = event_dict.get("content") or {}
    for part in content.get("parts") or []:
        fc = part.get("function_call")
        if fc and fc.get("name") == "adk_request_credential":
            return fc
    return None

def _extract_consent(fc: dict) -> tuple[str | None, str | None, dict | None]:
    args = fc.get("args") or {}
    auth_config = args.get("auth_config") or {}
    oauth2 = (auth_config.get("exchanged_auth_credential") or {}).get("oauth2") or {}
    return oauth2.get("auth_uri"), oauth2.get("nonce"), auth_config

async def ensure_session() -> str:
    us = _user_state()
    if us.get("session_id"):
        return us["session_id"]
    session = await remote_agent.async_create_session(user_id=st.session_state.user_id)
    sid = session["id"] if isinstance(session, dict) else session.id
    us["session_id"] = sid
    return sid

async def run_turn(user_prompt: str) -> str:
    session_id = await ensure_session()
    us = _user_state()
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
            # ── DIAGNOSTIC ────────────────────────────────────────
            st.session_state["_last_consent_extract"] = {
                "auth_uri_present": bool(auth_uri),
                "nonce_present": bool(nonce),
                "fc_keys": list(fc.keys()),
                "args_keys": list((fc.get("args") or {}).keys()),
                "auth_config_keys": list((auth_config or {}).keys()),
            }
            # ── END DIAGNOSTIC ────────────────────────────────────────
            if auth_uri and nonce:
                us["auth_uri"] = auth_uri
                us["nonce"] = nonce
                us["auth_config"] = auth_config
                us["fc_id"] = fc.get("id")
                return "I need your consent to act on your behalf. Click **Authorize** in the sidebar."

        content = ev.get("content") or {}
        if content.get("role") == "model":
            for part in content.get("parts") or []:
                if part.get("text"):
                    final_text.append(part["text"])

    return "".join(final_text) or "(no response)"

prompt = st.chat_input("Try: 'list my storage buckets' / 'send a test email' / 'list users in Entra'")
if prompt:
    st.session_state.messages.append(("user", prompt))
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        with st.spinner("Thinking…"):
            reply = asyncio.run(run_turn(prompt))
        st.markdown(reply)
    st.session_state.messages.append(("assistant", reply))
    if _user_state().get("auth_uri"):
        st.rerun()
