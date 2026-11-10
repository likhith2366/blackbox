"""PRIVATE fault and change catalogue for the demo controller.

Investigator-side code (blackbox/) must never import this module. Every entry is a real
change applied to the running demo: a commit to the company repo that is built and
deployed through demo.buildtool / demo.deploy, or an operator action on a process.
Commit messages and code read like ordinary engineering work; the private ids and
`family` labels below never leave demo/ and evaluator_private/.
"""
from __future__ import annotations

POLICY = "libs/httpclient/policy.py"
PAY_APP = "services/payment/app.py"
CHK_APP = "services/checkout/app.py"
CHK_CFG = "config/checkout-svc.yaml"

_FASTPATH = '''            if amount < SMALL_CHARGE_LIMIT:
                # Micro-charges skip risk scoring and velocity checks (DEMO-41).
                payment_id = db.insert_payment(conn, order_id, customer_id, amount, currency, _iso(now))
                LOG.info("charge approved", order_id=order_id, payment_id=payment_id, fast_path=True)
                self.send_json(201, {"payment_id": payment_id, "status": "approved"})
                return 201
'''
_RISK_ANCHOR = '            with TRACER.span("risk.score", attributes={"amount": amount}):\n'

CHANGES: dict[str, dict] = {
    # ------------------------------------------------------------------ faults
    "retry-policy": {
        "family": "retry_amplification", "kind": "commit", "who": "likhith",
        "message": "DEMO-9: retry transient payment failures in shared client\n\n"
                   "Retry timeouts and 5xx responses, tighten the per-attempt timeout so a\n"
                   "slow attempt is abandoned quickly, and drop the fixed backoff.",
        "edits": [(POLICY, "DEFAULT_TIMEOUT_MS = 2000", "DEFAULT_TIMEOUT_MS = 250"),
                  (POLICY, "MAX_RETRIES = 1", "MAX_RETRIES = 4"),
                  (POLICY, 'RETRY_ON = ("connect_error",)', 'RETRY_ON = ("connect_error", "timeout", "5xx")'),
                  (POLICY, "BACKOFF_MS = 100", "BACKOFF_MS = 0"),
                  ("libs/httpclient/VERSION", "3.1.0", "3.2.0"),
                  ("services/checkout/VERSION", "1.8.2", "1.9.0")],
        "deploy": ["checkout-svc"],
    },
    "pool-fastpath": {
        "family": "connection_leak", "kind": "commit", "who": "praneeth",
        "message": "DEMO-41: fast path for micro-charges under $5\n\n"
                   "Small charges skip the risk provider and velocity queries; they are\n"
                   "written straight to the payments table.",
        "edits": [(PAY_APP, "LARGE_AMOUNT = 300.0\n", "LARGE_AMOUNT = 300.0\nSMALL_CHARGE_LIMIT = 5.0\n"),
                  (PAY_APP, _RISK_ANCHOR, _FASTPATH + _RISK_ANCHOR),
                  ("services/payment/VERSION", "2.4.1", "2.5.0")],
        "deploy": ["payment-svc"],
    },
    "index-migration": {
        "family": "missing_index", "kind": "commit", "who": "praneeth",
        "message": "DEMO-52: drop idx_payments_customer_created\n\n"
                   "Index looked unused in the nightly reporting query plan and adds write\n"
                   "overhead on every charge insert.",
        "new_files": {"db/migrations/003_drop_customer_index.sql":
                      "-- Reduce write amplification on payments inserts (DEMO-52).\n"
                      "DROP INDEX IF EXISTS idx_payments_customer_created;\n"},
        "edits": [("services/payment/VERSION", "2.4.1", "2.4.2")],
        "deploy": ["payment-svc"],
    },
    "endpoint-port": {
        "family": "config_mismatch_endpoint", "kind": "commit", "who": "likhith",
        "message": "DEMO-58: route checkout to payment-svc through the new sidecar port",
        "edits": [(CHK_CFG, "base_url: http://127.0.0.1:18102", "base_url: http://127.0.0.1:18112")],
        "deploy": ["checkout-svc"],
    },
    "payment-stop": {
        "family": "stopped_dependency", "kind": "stop", "service": "payment-svc",
        "reason": "operator request",
    },
    "pool-override": {
        "family": "config_override_pool_size", "kind": "restart_env", "service": "payment-svc",
        "env": {"PAYMENT_DB_POOL_SIZE": "1"},
    },
    # ------------------------------------------------------------ harmless changes
    "ops-analyze": {
        "family": "harmless", "kind": "db_maintenance", "sql": "ANALYZE",
        "note": "real operator maintenance: refresh SQLite planner statistics on the payments DB",
    },
    "h-payment-currency-log": {
        "family": "harmless", "kind": "commit", "who": "praneeth",
        "message": "DEMO-61: include currency in charge approval log",
        "edits": [(PAY_APP, "recent_duplicate=dup, orders_30d=count_30d)",
                   "recent_duplicate=dup, orders_30d=count_30d, currency=currency)"),
                  ("services/payment/VERSION", "2.4.1", "2.4.2")],
        "deploy": ["payment-svc"],
    },
    "h-checkout-qty-limit": {
        "family": "harmless", "kind": "commit", "who": "likhith",
        "message": "DEMO-63: reject orders with quantity above 10",
        "edits": [(CHK_APP, '            return 400\n        sp.set("order.id", order["order_id"])\n',
                   '            return 400\n        if int(order["qty"]) > 10:\n'
                   '            LOG.warning("rejected order: quantity above limit", order_id=order["order_id"])\n'
                   '            self.send_json(400, {"error": "quantity above limit"})\n'
                   '            return 400\n        sp.set("order.id", order["order_id"])\n'),
                  ("services/checkout/VERSION", "1.8.2", "1.8.3")],
        "deploy": ["checkout-svc"],
    },
    "h-checkout-item-log": {
        "family": "harmless", "kind": "commit", "who": "likhith",
        "message": "DEMO-66: add item to order completed log line",
        "edits": [(CHK_APP, 'payment_id=result.get("payment_id"), attempts=resp.attempts)',
                   'payment_id=result.get("payment_id"), attempts=resp.attempts,\n'
                   '                 item=order["item"])'),
                  ("services/checkout/VERSION", "1.8.2", "1.8.3")],
        "deploy": ["checkout-svc"],
    },
    "d-checkout-retry-5xx": {
        "family": "decoy_retry_policy", "kind": "commit", "who": "likhith",
        "message": "DEMO-55: retry payment timeouts and 5xx from checkout\n\n"
                   "Payment provider blips should not fail the order on the first attempt.",
        "edits": [(CHK_CFG, "  # timeout / retry policy: defaults from libs/httpclient/policy.py unless set here\n",
                   "  # timeout / retry policy: defaults from libs/httpclient/policy.py unless set here\n"
                   "  max_retries: 2\n  retry_on: [connect_error, timeout, 5xx]\n"),
                  ("services/checkout/VERSION", "1.8.2", "1.8.3")],
        "deploy": ["checkout-svc"],
    },
}

FAULT_IDS = [k for k, v in CHANGES.items() if v["family"] not in ("harmless",) and not k.startswith("d-")]
