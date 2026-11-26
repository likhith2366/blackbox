"""Final-demo rehearsal on the live stack, driven through the real shop UI and the real BlackBox UI (headless Edge).

    python demo/web/e2e/rehearse_live.py --out runs/rehearsal_<date> endpoint-port payment-stop h-payment-currency-log

Prerequisites (see docs/DEMO_REHEARSAL.md): observability stack, telemetry bridge, load generator, control API with
DEMO_TELEMETRY=stack (:8710), shop UI (:5174), BlackBox API (:8700) and UI (:5173), Ollama.

For each change, every step is a UI click: shop reset -> healthy order -> wait until the previous change is outside
the 5-minute incident lookback -> inject from the operator page -> order during the change -> wait for an alert ->
BlackBox "New investigation" (live stack, improved settings, the alert that fired) -> report tabs -> "Ask BlackBox".
If no alert fires within 4 minutes, the change is recorded as a no-alert control and nothing is investigated.

Saved per change under <out>/<change>/: screenshots, the job's files copied byte-for-byte from runs/ui_jobs/<job>/
(output.json, meta.json, provenance.json, incident.json), the chat session, sha256 of every copied file, and
steps.json. The change id appears only on the operator page and in this script's output, never in BlackBox input.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from run_flow import CLICK_TEXT, CLICK_TID, Browser, place_order  # noqa: E402

SHOP = "http://127.0.0.1:5174/"
BB = "http://127.0.0.1:5173/"
API = "http://127.0.0.1:8700"
CTL = "http://127.0.0.1:8710"
QUESTION = "What changed shortly before this incident, and what is the most likely cause of the failures?"


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-4] + "Z"


def log(msg):
    print(f"{datetime.now(timezone.utc):%H:%M:%S} {msg}", flush=True)


def get(url, timeout=30):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def set_input(b, selector, value):
    return b.js(f"""(() => {{ const el = document.querySelector({json.dumps(selector)}); if (!el) return false;
        const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
        Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, {json.dumps(value)});
        el.dispatchEvent(new Event('input', {{bubbles: true}})); return true; }})()""")


def shop_control(b, testid, timeout=240):
    b.goto(SHOP + "#/controls", settle=3)
    b.wait(CLICK_TID.format(t=testid), 30)
    b.wait("""(() => { const li = document.querySelector('.attempts li');
              return li && !li.innerText.includes('waiting for control API') ? li.innerText : null; })()""", timeout)
    return b.js("document.querySelector('.attempts li').innerText")


def wait_alert(since_iso, timeout=240):
    from blackbox.evidence.live_incident import open_incident
    from live_fault_sweep import EXTENDED_ALERTS
    t = time.time()
    while time.time() - t < timeout:
        time.sleep(15)
        for svc, metric, op, thr, for_s in EXTENDED_ALERTS:
            try:
                open_incident(svc, metric, op, thr, for_s, since=since_iso)
                return f"{svc}:{metric}{op}{thr:g}:{for_s}"
            except LookupError:
                continue
    return None


def gpu_temp() -> int:
    import subprocess
    try:
        return int(subprocess.run(["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
                                  capture_output=True, text=True, timeout=15).stdout.strip())
    except Exception:
        return 0


def copy_verbatim(src: Path, dst: Path, hashes: dict):
    if src.exists():
        shutil.copy2(src, dst)
        hashes[dst.name] = hashlib.sha256(dst.read_bytes()).hexdigest()


def rehearse(b, change, out: Path, settle: int, gpu_max: int, rec: dict):
    d = out / change
    d.mkdir(parents=True, exist_ok=True)
    rec.update({"change": change, "started": now(), "steps": []})
    log(f"{change}: reset from the shop operator page")
    rec["reset"] = shop_control(b, "reset")
    b.shot(d / "01_reset.png")
    rec["steps"].append(place_order(b, d, "02_healthy_order"))
    log(f"{change}: settle {settle}s so the previous change is outside the incident lookback")
    time.sleep(settle)
    # the investigation must start soon after the alert: a hot GPU would delay it until the alert episode moves on
    while gpu_temp() > gpu_max:
        log(f"{change}: GPU {gpu_temp()} C; waiting for <= {gpu_max} C before injecting")
        time.sleep(30)
    b.goto(SHOP + "#/controls", settle=3)
    b.wait(f"""(() => {{ const r = document.querySelector('input[name=fault][value="{change}"]');
              if (!r) return false; r.click(); return true; }})()""", 30)
    time.sleep(0.5)
    injected_at = now()
    b.wait(CLICK_TID.format(t="inject"), 10)
    b.wait("""(() => { const li = document.querySelector('.attempts li');
              return li && !li.innerText.includes('waiting for control API'); })()""", 240)
    rec["inject"] = {"clicked_at": injected_at, "result": b.js("document.querySelector('.attempts li').innerText")}
    b.shot(d / "03_injected.png")
    log(f"{change}: injected; {rec['inject']['result'][:120]!r}")
    time.sleep(40)
    rec["steps"].append(place_order(b, d, "04_order_after_change"))
    alert = wait_alert(injected_at)
    rec["alert"] = alert
    if not alert:
        log(f"{change}: no alert within 4 min -> no-alert control (nothing investigated)")
        rec["outcome"] = "no_alert"
        b.goto(BB + "#/connections", settle=4)
        b.shot(d / "05_no_alert_connections.png")
        return rec
    log(f"{change}: alert {alert}; starting a live-stack investigation from the BlackBox UI")
    b.goto(BB + "#/start", settle=4)
    b.wait("""(() => { const r = [...document.querySelectorAll('input[name=mode]')][2]; if (!r) return false; r.click(); return true; })()""", 30)
    time.sleep(0.5)
    b.wait("""(() => { const r = [...document.querySelectorAll('input[name=settings]')][1]; if (!r) return false; r.click(); return true; })()""", 10)
    time.sleep(0.3)
    b.wait(f"""(() => {{ const el = document.querySelector('input[placeholder^="checkout-svc:error_rate"]'); if (!el) return false;
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, {json.dumps(alert)});
        el.dispatchEvent(new Event('input', {{bubbles: true}})); return true; }})()""", 10)
    time.sleep(0.5)
    b.shot(d / "05_start_form.png")
    b.wait(CLICK_TEXT.format(t=json.dumps("Start investigation")), 10)
    job = b.wait("""(() => { const m = location.hash.match(/#\\/jobs\\/([^/]+)/); return m ? decodeURIComponent(m[1]) : null; })()""", 60)
    rec["job_id"] = job
    log(f"{change}: job {job}")
    t0 = time.time()
    while time.time() - t0 < 1500:
        j = get(f"{API}/api/jobs/{job}")
        if j.get("status") in ("completed", "failed", "cancelled"):
            break
        time.sleep(10)
    rec["job_status"], rec["job_seconds"] = j.get("status"), round(time.time() - t0)
    rec["run_id"], rec["job_error"] = j.get("run_id"), j.get("error")
    time.sleep(3)
    b.shot(d / "06_job.png")
    if rec["run_id"]:
        for tab, label in (("report", "Report"), ("trace", "Trace"), ("evidence", "Evidence"), ("details", "Details")):
            b.goto(BB + "#/runs/" + rec["run_id"], settle=4)
            if tab != "report":
                b.js(f"""(() => {{ const t = [...document.querySelectorAll('[role=tab], .tabs button, .segmented button')]
                         .find(x => x.textContent.trim().startsWith({json.dumps(label)})); if (t) t.click(); return !!t; }})()""")
                time.sleep(1.5)
            b.shot(d / f"07_run_{tab}.png")
    jd = ROOT / "runs" / "ui_jobs" / job
    hashes: dict = {}
    for f in ("output.json", "meta.json", "provenance.json", "incident.json", "request.json", "job.json"):
        copy_verbatim(jd / f, d / f, hashes)
    rec["copied_from"], rec["sha256"] = str(jd.relative_to(ROOT)).replace("\\", "/"), hashes
    try:
        rep = json.loads((d / "output.json").read_text(encoding="utf-8")).get("report") or {}
        rec["report_status"], rec["headline"] = rep.get("status"), rep.get("headline")
    except (OSError, ValueError):
        pass
    log(f"{change}: {rec.get('report_status')} - {rec.get('headline')}")
    # Ask BlackBox (chat) about the same live incident
    b.goto(BB + "#/chat", settle=4)
    b.wait(f"""(() => {{ const el = document.querySelector('input[placeholder^="checkout-svc:error_rate"]'); if (!el) return false;
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, {json.dumps(alert)});
        el.dispatchEvent(new Event('input', {{bubbles: true}})); return true; }})()""", 20)
    before = {s["session_id"] for s in get(f"{API}/api/chat/sessions")}
    b.js("""(() => { const f = document.querySelector('form.form'); const btn = f && f.querySelector('button[type=submit]');
             if (!btn || btn.disabled) return false; btn.click(); return true; })()""")
    b.wait("document.querySelector('form.chat-input textarea, form.chat-input input') !== null", 120)
    b.wait(f"""(() => {{ const el = document.querySelector('form.chat-input textarea, form.chat-input input'); if (!el) return false;
        const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
        Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, {json.dumps(QUESTION)});
        el.dispatchEvent(new Event('input', {{bubbles: true}})); return true; }})()""", 10)
    time.sleep(0.5)
    b.js("(() => { const btn = document.querySelector('form.chat-input button[type=submit]'); if (!btn) return false; btn.click(); return true; })()")
    rec["chat_question"] = QUESTION
    sess = None
    t0 = time.time()
    while time.time() - t0 < 900:
        time.sleep(10)
        new = [s for s in get(f"{API}/api/chat/sessions") if s["session_id"] not in before]
        if new and new[0]["turns"] >= 2:
            sess = get(f"{API}/api/chat/sessions/{new[0]['session_id']}")
            break
    time.sleep(2)
    b.shot(d / "08_chat.png")
    if sess:
        (d / "chat.json").write_text(json.dumps(sess, indent=1, default=str), encoding="utf-8")
        rec["chat_session"] = sess.get("session_id")
    rec["outcome"] = "investigated"
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("changes", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--settle", type=int, default=330)
    ap.add_argument("--gpu-max", type=int, default=85, help="inject only when the GPU is at or below this temperature")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    b = Browser()
    summary = []
    try:
        b.goto(BB + "#/connections", settle=5)
        b.shot(out / "00_connections.png")
        for change in a.changes:
            rec: dict = {}
            try:
                rehearse(b, change, out, a.settle, a.gpu_max, rec)
            except Exception as exc:              # keep what was recorded; never invent a result
                rec.update({"change": change, "outcome": "driver_error", "error": f"{type(exc).__name__}: {exc}"[:500]})
                try:
                    b.shot(out / change / "99_driver_error.png")
                except Exception:
                    pass
            (out / change).mkdir(parents=True, exist_ok=True)
            (out / change / "steps.json").write_text(json.dumps(rec, indent=2), encoding="utf-8")
            summary.append(rec)
        log("final reset from the shop operator page")
        shop_control(b, "reset")
    finally:
        b.close()
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps([{k: r.get(k) for k in ("change", "outcome", "alert", "report_status", "headline")} for r in summary], indent=2))


if __name__ == "__main__":
    main()
