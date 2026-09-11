import hashlib
import importlib
import json
from datetime import date
from pathlib import Path

import streamlit as st
from freight_ai.config import ModelConfig, load_config
from freight_ai.data.ingest import preprocess
from freight_ai.data.models import Intent
from freight_ai.inference import chat, conversation
from freight_ai.inference.backend import HFBackend
from freight_ai.matching.engine import MatchingConfig
from freight_ai.presentation import intent_description, record_table, render_result
from freight_ai.service import ask, current_records, execute
from freight_ai.training.dataset import demonstrations

ROOT = Path(__file__).resolve().parents[1]


@st.cache_resource(max_entries=1)
def load_chat_api(source_digest):
    """Refresh imported Python modules when their source changes during development."""
    importlib.invalidate_caches()
    importlib.reload(conversation)
    return importlib.reload(chat).respond


chat_digest = hashlib.sha256(
    Path(chat.__file__).read_bytes() + Path(conversation.__file__).read_bytes()
).hexdigest()
respond = load_chat_api(chat_digest)

st.set_page_config(page_title="Freight AI POC", layout="wide")
st.markdown(
    f"<style>{(ROOT / 'app/styles.css').read_text()}</style>", unsafe_allow_html=True
)
st.title("Freight AI")
st.caption(
    "Inspect freight records, screen vessels and compare language-model experiments."
)
config = load_config(ROOT / "configs/default.yaml")

with st.sidebar:
    as_of = st.date_input("As-of date", value=date(2026, 9, 1))
    st.caption("Source timestamps have no timezone. Screening uses calendar dates.")
    if st.button("Reload workbooks"):
        preprocess(config["sources"], config["processed"])
        st.success("The freight records have been refreshed.")
    st.subheader("Provisional matching assumptions")
    use_allowance = st.checkbox("Assume a cargo fraction of DWT", value=False)
    if use_allowance:
        config["matching"]["cargo_fraction"] = st.slider(
            "Cargo fraction", 0.5, 1.0, 0.95, 0.01
        )
    config["matching"]["require_same_zone"] = st.checkbox(
        "Require exact zone equality", value=False
    )
    st.caption(
        "DWT includes fuel and stores. Neither these allowances nor the scores are validated business rules."
    )

try:
    records = current_records(config)
except (OSError, ValueError) as exc:
    st.error(str(exc))
    st.info("Use Reload workbooks to preprocess the two configured Excel files.")
    st.stop()

data_tab, match_tab, chat_tab, language_tab, eval_tab = st.tabs(
    [
        "Source data",
        "Freight screening",
        "Shipping chatbot",
        "Search and questions",
        "Evaluation",
    ]
)
with data_tab:
    for kind in ("orders", "tonnage"):
        st.subheader(kind.title())
        st.dataframe(
            record_table([r.model_dump(mode="json") for r in records[kind]]),
            hide_index=True,
        )
    with st.expander("Technical details: source inspection"):
        st.json(json.loads((Path(config["processed"]) / "inspection.json").read_text()))

with match_tab:
    order_map = {r.record_id: r for r in records["orders"]}
    selected = st.selectbox(
        "Cargo order snapshot",
        list(order_map),
        format_func=lambda key: (
            f"{order_map[key].load_port} → {order_map[key].discharge_port} | {order_map[key].cargo_type or 'Unknown cargo'}"
        ),
    )
    st.caption(
        "Choose an order, then screen vessels against its cargo and loading dates."
    )
    if st.button("Screen vessels"):
        try:
            result = execute(
                Intent(action="match", record_id=selected),
                records,
                MatchingConfig(**config["matching"]),
                as_of,
            )
            st.session_state["screening"] = result
        except ValueError as exc:
            st.error(str(exc))
            st.session_state.pop("screening", None)
    if "screening" in st.session_state:
        result = st.session_state["screening"]
        saved_order = order_map.get(result["order_record_id"])
        if saved_order:
            st.write(
                f"**Cargo:** {saved_order.load_port} → {saved_order.discharge_port}; {saved_order.cargo_type or 'cargo type not provided'}"
            )
        st.caption(
            "Saved result. Run screening again after changing the order or assumptions."
        )
        render_result(result)
        with st.expander("Technical details: screening trace"):
            st.json(result)


@st.cache_resource(max_entries=1)
def load_backend(config_json):
    return HFBackend(ModelConfig.model_validate_json(config_json))


class LazyChatBackend:
    """Only load model weights when an answer needs generation."""

    def __init__(self, config_json):
        self.config_json = config_json

    def generate(self, messages):
        return load_backend(self.config_json).generate(messages)


with chat_tab:
    st.subheader("Ask Freight AI")
    st.write(
        "Ask about shipping concepts in plain English, or ask about the cargo orders and vessels in the supplied workbooks. The assistant keeps the conversation in context and explains its answer in business language."
    )
    st.caption(
        "General shipping explanations come from the language model. Questions about your actual cargoes, vessels or screening results are checked against the source workbooks."
    )
    model_labels = {
        "0.5b": "Qwen 0.5B · Lightweight baseline",
        "1.5b": "Qwen 1.5B · Balanced experiment",
        "3b": "Qwen 3B · Higher memory use",
    }

    def reset_chat():
        st.session_state["chat_messages"] = []
        st.session_state.pop("chat_previous_intent", None)
        st.session_state.pop("chat_conversation_state", None)
        load_backend.clear()

    selected_model = st.selectbox(
        "Conversation model",
        list(model_labels),
        format_func=model_labels.get,
        key="selected_chat_model",
        on_change=reset_chat,
        help="Switching models starts a fresh conversation for a fair comparison.",
    )
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
    if not model_ready:
        st.warning(
            "This model is not available locally. Complete its download and verification first."
        )
    else:
        st.caption(
            "Local inference · Base model · Switching models clears the conversation"
        )
    st.button("New conversation", on_click=reset_chat, key="reset_shipping_chat")
    with st.expander("Experiment settings"):
        chat_strategy = st.selectbox(
            "Intent examples",
            ["zero", "one", "few"],
            index=2,
            key="chat_strategy",
            format_func=lambda x: {
                "zero": "Zero-shot",
                "one": "One-shot",
                "few": "Few-shot (three examples)",
            }[x],
        )
        st.caption(f"Pinned revision: {mc['revision']}")
        st.caption(
            "The selector controls this Shipping chatbot tab. Structured searches use the freight engine; other experiment tabs have their own settings."
        )
    if "chat_messages" not in st.session_state:
        st.session_state["chat_messages"] = []
    for message in st.session_state["chat_messages"]:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if message.get("basis"):
                st.caption(message["basis"])
            if message.get("diagnostics"):
                with st.expander("Technical details"):
                    st.json(message["diagnostics"])
    chat_question = st.chat_input(
        "Ask about cargoes, vessels or shipping…",
        key="shipping_chat_input",
        disabled=not model_ready,
    )
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
            st.rerun()
        except (OSError, ValueError, RuntimeError, ImportError) as exc:
            st.error(
                "The chatbot encountered a runtime error. Your question has not been answered; check the technical error details below."
            )
            with st.expander("Technical error details"):
                st.text(str(exc))

with language_tab:
    mode = st.radio(
        "How would you like to search?",
        ["Search with filters", "Ask AI", "Advanced JSON"],
        horizontal=True,
    )
    intent_text = ""
    question = ""
    strategy = "few"
    model_id = config["model"]["model_id"]
    adapter = ""
    narrative = False
    if mode == "Search with filters":
        dataset_label = st.selectbox("Search for", ["Cargo orders", "Vessels"])
        dataset = "orders" if dataset_label == "Cargo orders" else "tonnage"
        location = st.text_input(
            "Load port" if dataset == "orders" else "Open location",
            "Tubarao" if dataset == "orders" else "",
        )
        st.caption(
            "Leave the location blank to show all records. Location names must match the source data; capitalisation does not matter."
        )
        output = st.selectbox(
            "Show", ["Matching records", "Record count", "Quantity totals"]
        )
        aggregation = {
            "Matching records": "none",
            "Record count": "count",
            "Quantity totals": "sum_tonnes",
        }[output]
    elif mode == "Ask AI":
        question = st.text_area("Your question", "List orders loading at Tubarao.")
        st.caption(
            "The AI is experimental. Check the interpreted request below before using its results."
        )
        with st.expander("Advanced model settings"):
            strategy = st.selectbox(
                "Prompt strategy",
                ["zero", "one", "few"],
                index=2,
                format_func=lambda x: {
                    "zero": "No examples",
                    "one": "One example",
                    "few": "Three examples",
                }[x],
            )
            model_id = st.text_input("Model ID or local model path", model_id)
            adapter = st.text_input("Adapter directory (blank = base model)", "")
    else:
        st.caption("For technical users: enter a structured query.")
        intent_text = st.text_area(
            "Structured query",
            '{"action":"query","dataset":"orders","text_filters":{"load_port":"Tubarao"}}',
        )
    if st.button("Run search"):
        try:
            if mode != "Ask AI":
                if mode == "Advanced JSON":
                    intent = Intent.model_validate_json(intent_text)
                else:
                    field = "load_port" if dataset == "orders" else "open_area"
                    intent = Intent(
                        action="query",
                        dataset=dataset,
                        text_filters={field: location.strip()}
                        if location.strip()
                        else {},
                        aggregation=aggregation,
                    )
                response = {
                    "mode": "deterministic_no_model",
                    "intent": intent.model_dump(mode="json"),
                    "tool_result": execute(
                        intent, records, MatchingConfig(**config["matching"]), as_of
                    ),
                }
            else:
                mc = {
                    **config["model"],
                    "model_id": model_id,
                    "adapter_path": adapter or None,
                }
                with st.spinner(
                    "Reading your question and checking the freight records…"
                ):
                    backend = LazyChatBackend(json.dumps(mc, sort_keys=True))
                    response = ask(
                        backend,
                        question,
                        config,
                        as_of,
                        strategy,
                        demonstrations(config["training_data"]),
                        narrative,
                    )
                response["model"] = backend.metadata
            st.session_state["response"] = response
        except (OSError, ValueError, RuntimeError, ImportError) as exc:
            st.error(
                "We could not complete this search. Check the criteria or try a simpler question. You can also use Search with filters."
            )
            with st.expander("Technical error details"):
                st.text(str(exc))
            st.session_state.pop("response", None)
    if "response" in st.session_state:
        response = st.session_state["response"]
        st.write("**Request used:** " + intent_description(response["intent"]))
        st.caption(
            "Saved result. Run the search again after changing your question or filters."
        )
        render_result(response["tool_result"])
        with st.expander("Technical details: query and results"):
            st.json(response)

with eval_tab:
    st.write(
        "The full comparison uses the same held-out examples for all six model/strategy combinations."
    )
    st.caption(
        "Training requires a CUDA GPU. An adapter must exist before adapted-model evaluation."
    )
    reports = sorted((ROOT / "artifacts/evaluation").glob("*.json"))
    if reports:
        report_path = st.selectbox(
            "Saved evaluation report", reports, format_func=lambda p: p.name
        )
        report = json.loads(report_path.read_text())
        metrics = []
        for name, run in report.get("runs", {}).items():
            if "metrics" not in run:
                continue
            m = run["metrics"]
            metrics.append(
                {
                    "Experiment": name.replace("base", "Original model")
                    .replace("qlora", "Adapted model")
                    .replace("_zero", " · no examples")
                    .replace("_one", " · one example")
                    .replace("_few", " · three examples"),
                    "Questions tested": m["examples"],
                    "Requests understood exactly": f"{m['exact_match']:.0%}",
                    "Usable structured requests": f"{m['schema_valid']:.0%}",
                    "Average response time": f"{m['mean_latency_seconds']:.1f} seconds",
                    "Correct answers after data lookup": f"{m['tool_answer_correct']:.0%}"
                    if "tool_answer_correct" in m
                    else "Not measured",
                }
            )
        if metrics:
            st.dataframe(metrics, hide_index=True)
            st.caption(
                "These are small prototype tests. A usable request can still misunderstand the question; exact understanding and correct answers are the more meaningful measures."
            )
        elif report.get("diagnostic"):
            st.warning(
                "This report records an earlier failed explanation check. Its original output contains incorrect facts and is retained only for technical review."
            )
        elif "tool_result" in report:
            render_result(report["tool_result"])
        elif "matches" in report:
            for result in report["matches"]:
                order = next(
                    (
                        r
                        for r in records["orders"]
                        if r.record_id == result["order_record_id"]
                    ),
                    None,
                )
                if order:
                    st.write(f"**Cargo: {order.load_port} → {order.discharge_port}**")
                render_result(result)
        with st.expander("Technical details: full evaluation report"):
            st.json(report)
    else:
        st.info(
            "No model evaluation reports yet. Run the documented evaluation command."
        )
