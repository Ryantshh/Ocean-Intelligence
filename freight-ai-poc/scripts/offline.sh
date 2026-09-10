#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 DO_NOT_TRACK=1
export HF_HOME="$PWD/artifacts/hf-cache"
exec .venv/bin/python -m streamlit run app/streamlit_app.py --browser.gatherUsageStats false --server.address 127.0.0.1
