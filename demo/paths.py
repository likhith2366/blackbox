"""Filesystem layout and constants for the local (minimal-profile) demo environment."""
from __future__ import annotations

import json
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEMO = REPO / "demo"
TEMPLATE = DEMO / "company_template"
# The demo company's source repo is a separate git repository OUTSIDE this repo
# (default: ../blackbox-demo-shop, remote github.com/likhith2366/blackbox-demo-shop).
WORKSPACE = Path(os.environ.get("BLACKBOX_COMPANY_REPO", str(REPO.parent / "blackbox-demo-shop"))).resolve()
RUNTIME = DEMO / ".runtime"                          # gitignored runtime state
ARTIFACTS = RUNTIME / "artifacts"
BUILDS = RUNTIME / "builds"
DEPLOY_DIR = RUNTIME / "deploy"
DEPLOYMENTS = RUNTIME / "deployments.jsonl"
PROC = RUNTIME / "proc"
DATA = RUNTIME / "data"
CAPTURES = RUNTIME / "captures"
STATE = RUNTIME / "state.json"
BASE_DB = DATA / "payments_base.db"
WORK_DB = DATA / "payments.db"

SERVICES = ("checkout-svc", "payment-svc")
PORTS = {"checkout-svc": 18101, "payment-svc": 18102}
MODULES = {"checkout-svc": "services.checkout.app", "payment-svc": "services.payment.app"}
# Repository paths packaged into each service's artifact.
SOURCE_PATHS = {
    "checkout-svc": ["services/__init__.py", "services/checkout", "libs", "config/checkout-svc.yaml"],
    "payment-svc": ["services/__init__.py", "services/payment", "libs", "config/payment-svc.yaml", "db"],
}
VERSION_FILE = {"checkout-svc": "services/checkout/VERSION", "payment-svc": "services/payment/VERSION"}
METRIC_INTERVAL_S = 5


def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(STATE)


def next_counter(name: str, start: int) -> int:
    st = load_state()
    n = int(st.get("counters", {}).get(name, start - 1)) + 1
    st.setdefault("counters", {})[name] = n
    save_state(st)
    return n
