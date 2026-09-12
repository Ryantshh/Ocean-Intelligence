import hashlib
import importlib
import json
import os
from datetime import date
from pathlib import Path

import streamlit as st
import sys

ROOT = Path(__file__).resolve().parents[1]
# Reload the whole package together: partial reloads mix old schemas with new callers.
source_digest = hashlib.sha256(b"".join(
    p.read_bytes() for p in sorted((ROOT / "src/freight_ai").rglob("*.py"))
)).hexdigest()
loaded_package = sys.modules.get("freight_ai")
if loaded_package is not None and getattr(loaded_package, "_app_digest", source_digest) != source_digest:
    for name in list(sys.modules):
        if name == "freight_ai" or name.startswith("freight_ai."):
            del sys.modules[name]
    st.cache_resource.clear()
    st.session_state.clear()
    importlib.invalidate_caches()

import freight_ai
freight_ai._app_digest = source_digest
from freight_ai.config import ModelConfig, load_config
from freight_ai.data.ingest import preprocess
from freight_ai.inference.chat import respond
from freight_ai.inference.backend import HFBackend
from freight_ai.service import current_records
from freight_ai.training.dataset import demonstrations

from freight_ai.inference.history_store import HistoryStore

st.set_page_config(page_title="Freight AI", layout="centered")
st.markdown(f"<style>{(ROOT / 'app/styles.css').read_text()}</style>", unsafe_allow_html=True)
config = load_config(ROOT / "configs/default.yaml")
store = HistoryStore(Path(os.environ.get("FREIGHT_CHAT_HISTORY", ROOT / "artifacts/conversations.sqlite3")))

@st.cache_resource(max_entries=1)
def load_backend(config_json):
    return HFBackend(ModelConfig.model_validate_json(config_json))


class LazyChatBackend:
    """Only load model weights when an answer needs generation."""

    def __init__(self, config_json):
        self.config_json = config_json

    def generate(self, messages):
        return load_backend(self.config_json).generate(messages)



def reset_chat():
    for key in ("chat_previous_intent", "chat_conversation_state", "conversation_id"):
        st.session_state.pop(key, None)
    st.session_state["chat_messages"] = []


def restore_chat(key):
    payload = store.load(key)
    st.session_state["data_source_selector_v2"] = payload["source"]
    st.session_state["selected_chat_model"] = payload["model"]
    st.session_state["as_of"] = date.fromisoformat(payload["as_of"])
    st.session_state["chat_messages"] = payload["messages"]
    st.session_state["chat_conversation_state"] = payload.get("state")
    st.session_state["chat_previous_intent"] = payload.get("intent")
    st.session_state["conversation_id"] = key
    st.session_state.pop("supabase_snapshot", None)
    for setting, value in payload.get("settings", {}).items():
        if setting in SETTING_KEYS:
            st.session_state[setting] = value


SETTING_KEYS = ("chat_strategy", "summarize_history", "enable_semantic", "semantic_path",
                "query_execution", "text_match", "allowance", "fraction", "same_zone")
model_labels = {"0.5b": "Qwen 0.5B", "1.5b": "Qwen 1.5B", "3b": "Qwen 3B"}
with st.sidebar:
    st.title("Freight AI")
    with st.expander("Settings"):
        source = st.selectbox("Data source", ["Excel test workbooks", "Supabase (read-only)"],
            index=0 if os.environ.get("FREIGHT_POC_SOURCE") == "excel" else 1,
            key="data_source_selector_v2", on_change=reset_chat)
        selected_model = st.selectbox("Conversation model", list(model_labels), index=1,
            format_func=model_labels.get, key="selected_chat_model", on_change=reset_chat)
        as_of = st.date_input("As-of date", value=date(2026,9,1), key="as_of", on_change=reset_chat)
        if st.button("Refresh data"):
            st.session_state.pop("supabase_snapshot", None)
            reset_chat()
            if source.startswith("Excel"):
                preprocess(config["sources"], config["processed"])
        chat_strategy = "few"
        config.update(summarize_history=True, text_match="normalized",
                      query_execution="database" if source.startswith("Supabase") else "snapshot")
        embedding_path = ROOT / "artifacts/embedding-model"
        if embedding_path.is_dir():
            config["automatic_semantic_path"] = str(embedding_path)
        show_details = False
        st.caption("Conversation memory and search are managed automatically. Ask in chat to change a cargo-capacity assumption.")
        if os.environ.get("FREIGHT_POC_DEVELOPER") == "1":
            chat_strategy = st.selectbox("Prompt examples", ["zero", "one", "few"], index=2, key="chat_strategy")
            config["summarize_history"] = st.checkbox("Summarise older messages locally", value=True, key="summarize_history")
            if st.checkbox("Semantic suggestions", key="enable_semantic"):
                config["semantic_model_path"] = st.text_input("Local embedding model", str(embedding_path), key="semantic_path")
            config["query_execution"] = st.selectbox("Query execution", ["snapshot", "database"], key="query_execution")
            config["text_match"] = st.selectbox("Text matching", ["exact", "normalized"], key="text_match")
            show_details = st.checkbox("Show technical details", value=False)
    st.button("＋ New chat", key="reset_shipping_chat", on_click=reset_chat, use_container_width=True)
    st.caption("Your conversations")
    saved = store.list()
    for key, updated in saved:
        payload = store.load(key)
        title = next((m["content"] for m in payload["messages"] if m["role"] == "user"), "Untitled chat")
        cols = st.columns([5, 1])
        with cols[0]:
            st.button(title[:55], key="history_" + key, on_click=restore_chat, args=(key,),
                      use_container_width=True, help=title)
        with cols[1]:
            if st.button("×", key="delete_" + key, help="Delete this saved conversation"):
                store.delete(key)
                if st.session_state.get("conversation_id") == key:
                    reset_chat()
                st.rerun()
    if not saved:
        st.caption("Chats appear here automatically.")

config["data_source"] = "supabase" if source.startswith("Supabase") else "excel"
st.session_state["active_source"] = source
manifest_path = ROOT / "artifacts/verification" / f"qwen-{selected_model}.json"
model_ready = False
mc = dict(config["model"])
try:
    manifest = json.loads(manifest_path.read_text())
    model_ready = (
        bool(manifest.get("passed")) and Path(manifest["local_path"]).is_dir()
    )
    mc.update(
        model_id=manifest["local_path"],
        revision=manifest["revision"],
        local_files_only=True,
        adapter_path=None,
        device="cpu",
    )
except (OSError, ValueError, KeyError):
    pass
st.session_state["active_chat_model_config"] = mc

def save_chat():
    payload = {"messages": st.session_state["chat_messages"],
               "state": st.session_state.get("chat_conversation_state"),
               "intent": st.session_state.get("chat_previous_intent"),
               "source": source, "model": selected_model, "as_of": as_of.isoformat(),
               "settings": {k: st.session_state[k] for k in SETTING_KEYS if k in st.session_state}}
    st.session_state["conversation_id"] = store.save(payload, st.session_state.get("conversation_id"))


st.session_state.setdefault("chat_messages", [])
st.caption(f"{model_labels[selected_model]} · {source} · As of {as_of:%d %b %Y}")
if not st.session_state["chat_messages"]:
    st.title("How can I help with shipping?")
    st.write("Ask about cargoes, vessels, or shipping. Your conversations are saved automatically.")
for message in st.session_state["chat_messages"]:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if show_details and message.get("diagnostics"):
            with st.expander("Technical details"):
                st.json(message["diagnostics"])
if not model_ready:
    st.warning("The selected model is not available locally. Choose another model in Settings.")
chat_question = st.chat_input("Message Freight AI…", key="shipping_chat_input", disabled=not model_ready)
if chat_question:
    try:
        if config["data_source"] == "supabase":
            if "supabase_snapshot" not in st.session_state:
                with st.spinner("Reading freight records…"):
                    st.session_state["supabase_snapshot"] = current_records(config)
            config["_snapshot"] = st.session_state["supabase_snapshot"]
    except (ValueError, OSError) as exc:
        st.error(str(exc))
        st.stop()
if chat_question:
    st.session_state["chat_messages"].append(
        {"role": "user", "content": chat_question}
    )
    try:
        with st.spinner("Checking the question and preparing an answer…"):
            backend = LazyChatBackend(json.dumps(mc, sort_keys=True))
            prior = st.session_state["chat_messages"][:-1]
            previous_intent = st.session_state.get("chat_previous_intent")
            chat_response = respond(
                backend,
                chat_question,
                config,
                as_of,
                prior,
                previous_intent,
                chat_strategy,
                demonstrations(config["training_data"]),
                conversation_state=st.session_state.get("chat_conversation_state"),
            )
        assistant_message = {
            "role": "assistant",
            "content": chat_response["content"],
            "basis": " · ".join(
                filter(
                    None, [model_labels[selected_model], chat_response.get("basis")]
                )
            ),
            "diagnostics": chat_response.get("diagnostics"),
        }
        st.session_state["chat_messages"].append(assistant_message)
        if chat_response.get("conversation_state"):
            st.session_state["chat_conversation_state"] = chat_response[
                "conversation_state"
            ]
        if chat_response.get("intent"):
            st.session_state["chat_previous_intent"] = chat_response["intent"]
        save_chat()
        st.rerun()
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        st.error(
            "The chatbot encountered a runtime error. Your question has not been answered; check the technical error details below."
        )
        with st.expander("Technical error details"):
            st.text(str(exc))
