"""End-to-end demo flow driven through the real shop UI in headless Edge (Chrome DevTools Protocol).

    python demo/web/e2e/run_flow.py <fault_id> [--traffic-s 60 --rps 6]

Prerequisites: control API on 127.0.0.1:8710 and the Vite dev server on 127.0.0.1:5174.
Steps (every one is a real UI click; screenshots go to demo/web/screenshots/verified/<fault_id>/):
  reset -> healthy order -> inject fault -> generate traffic -> failing order -> create demo
  incident snapshot -> reset -> healthy order again.
Prints a JSON summary (incident id, order results). The fault id only appears in the
demo-control area and this script's output.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websocket

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
UI = "http://127.0.0.1:5174/"
OUT_ROOT = Path(__file__).resolve().parents[1] / "screenshots" / "verified"
PORT = 9333


class Browser:
    def __init__(self):
        self.profile = tempfile.mkdtemp(prefix="edge-e2e-")
        self.proc = subprocess.Popen([EDGE, "--headless=new", "--disable-gpu", "--disable-extensions",
                                      "--disable-background-networking", "--disable-component-update",
                                      "--disable-features=msEdgeStartupBoost,Translate", f"--remote-debugging-port={PORT}",
                                      f"--user-data-dir={self.profile}", "--window-size=1400,1100",
                                      "--force-color-profile=srgb", "--no-first-run", "about:blank"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))
                page = next(t for t in tabs if t["type"] == "page")
                break
            except Exception:
                time.sleep(0.2)
        self.ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=60)
        self.n = 0
        self.cmd("Page.enable")
        self.cmd("Runtime.enable")
        self.cmd("Emulation.setDeviceMetricsOverride", width=1400, height=1100, deviceScaleFactor=1, mobile=False)

    def cmd(self, method, **params):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def js(self, expr):
        r = self.cmd("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
        if "exceptionDetails" in r:
            raise RuntimeError(f"js error: {r['exceptionDetails']}")
        return r.get("result", {}).get("value")

    def goto(self, url, settle=2.5):
        self.cmd("Page.navigate", url=url)
        time.sleep(settle)
        self.js("window.confirm = () => true; true")

    def wait(self, expr, timeout=120, every=1.0):
        t = time.time()
        while time.time() - t < timeout:
            v = self.js(expr)
            if v:
                return v
            time.sleep(every)
        raise TimeoutError(expr)

    def shot(self, path: Path):
        full = self.js("Math.ceil(document.documentElement.scrollHeight)") or 1100
        self.cmd("Emulation.setDeviceMetricsOverride", width=1400, height=min(int(full), 4000),
                 deviceScaleFactor=1, mobile=False)
        time.sleep(0.4)
        data = self.cmd("Page.captureScreenshot", format="png")["data"]
        path.write_bytes(base64.b64decode(data))
        self.cmd("Emulation.setDeviceMetricsOverride", width=1400, height=1100, deviceScaleFactor=1, mobile=False)

    def close(self):
        try:
            self.ws.close()
        finally:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            shutil.rmtree(self.profile, ignore_errors=True)


CLICK_TEXT = """(() => {{ const b = [...document.querySelectorAll('button')].find(x => x.textContent.trim().startsWith({t}) && !x.disabled);
  if (!b) return false; b.click(); return true; }})()"""
CLICK_TID = """(() => {{ const b = document.querySelector('[data-testid="{t}"]'); if (!b || b.disabled) return false; b.click(); return true; }})()"""


def place_order(b: Browser, out: Path, name: str) -> dict:
    b.goto(UI + "#/")
    b.wait("document.querySelectorAll('.products button').length > 0", 30)
    # add one unit of the first product (its "+" stepper button)
    b.wait("""(() => { const card = document.querySelector('.products > *'); const plus = [...card.querySelectorAll('button')]
              .find(x => x.textContent.trim() === '+' || /add/i.test(x.textContent) || /increase/i.test(x.getAttribute('aria-label')||''));
              if (!plus) return false; plus.click(); return true; })()""", 15)
    b.wait(CLICK_TEXT.format(t=json.dumps("Place order")), 15)
    # wait for the first result row to have an outcome (status badge)
    res = b.wait("""(() => { const li = document.querySelector('.attempts li'); if (!li) return null;
              const t = li.innerText; return /HTTP \\d{3}|upstream unreachable|timed out|no answer/i.test(t) ? t : null; })()""", 40)
    b.shot(out / f"{name}.png")
    return {"step": name, "result_text": res[:300]}


def control(b: Browser, testid: str, timeout=180):
    b.goto(UI + "#/controls", settle=3)
    b.wait(CLICK_TID.format(t=testid), 30)
    # control actions list: wait until the newest action has an outcome
    b.wait("""(() => { const li = document.querySelector('.attempts li'); return li && !li.innerText.includes('waiting for control API'); })()""",
           timeout)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("fault_id")
    ap.add_argument("--traffic-s", type=int, default=60)
    ap.add_argument("--rps", type=int, default=6)
    args = ap.parse_args()
    out = OUT_ROOT / args.fault_id
    out.mkdir(parents=True, exist_ok=True)
    summary = {"fault_id": args.fault_id, "steps": []}
    b = Browser()
    try:
        control(b, "reset")
        b.shot(out / "01_reset_healthy_baseline.png")
        summary["steps"].append(place_order(b, out, "02_healthy_order"))
        # select the fault and inject
        b.goto(UI + "#/controls", settle=3)
        b.wait(f"""(() => {{ const r = document.querySelector('input[name=fault][value="{args.fault_id}"]');
                  if (!r) return false; r.click(); return true; }})()""", 30)
        time.sleep(0.5)
        b.wait(CLICK_TID.format(t="inject"), 10)
        b.wait("""(() => { const li = document.querySelector('.attempts li'); return li && !li.innerText.includes('waiting for control API'); })()""", 180)
        b.shot(out / "03_fault_injected.png")
        # traffic: set rps/duration through the form, then start
        b.js(f"""(() => {{ const ins = document.querySelectorAll('[data-testid=traffic-form] input');
                 const set = (el, v) => {{ const s = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                   s.call(el, String(v)); el.dispatchEvent(new Event('input', {{bubbles: true}})); }};
                 set(ins[0], {args.rps}); set(ins[1], {args.traffic_s}); return true; }})()""")
        time.sleep(0.5)
        b.wait(CLICK_TID.format(t="traffic-start"), 10)
        time.sleep(args.traffic_s * 0.6)
        summary["steps"].append(place_order(b, out, "04_order_during_fault"))
        b.goto(UI + "#/controls", settle=3)
        b.wait("""(() => { const s = document.querySelector('[data-testid=traffic-status]'); return s && s.innerText.startsWith('finished') ? s.innerText : null; })()""",
               args.traffic_s + 60, every=3)
        summary["traffic"] = b.js("document.querySelector('[data-testid=traffic-status]').innerText")
        b.shot(out / "05_traffic_finished.png")
        b.wait(CLICK_TID.format(t="snapshot-create"), 10)
        res = b.wait("""(() => { const id = document.querySelector('[data-testid=snapshot-id]'); if (id) return 'ID ' + id.innerText;
                  const w = [...document.querySelectorAll('.notice')].find(n => n.innerText.includes('Snapshot not created'));
                  return w ? 'ERR ' + w.innerText : null; })()""", 240, every=2)
        summary["snapshot"] = res
        b.shot(out / "06_demo_incident_created.png")
        control(b, "reset")
        b.shot(out / "07_reset_again.png")
        summary["steps"].append(place_order(b, out, "08_healthy_after_reset"))
    finally:
        b.close()
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    sys.exit(main())
