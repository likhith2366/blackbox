"""Live-capture format: verifier and replay (tmp dirs only; no network).

`write_capture` below is a minimal REFERENCE writer for the format in eval/live_capture.py,
for whoever implements the real writer in blackbox/connectors/.
"""
import base64
import hashlib
import json

import pytest

from eval.live_capture import Replay, verify_capture

TOKEN = "ghp_TESTSECRET123"
CID = "LC-20261120T020000Z-a1b2c3"


def write_capture(root, exchanges, cid=CID, **meta_over):
    d = root / cid
    d.mkdir(parents=True)
    lines = []
    for i, (url, params, code, body) in enumerate(exchanges, 1):
        raw = body.encode()
        lines.append(json.dumps({
            "seq": i, "retrieved_at": "2026-11-20T02:00:01Z", "method": "GET", "url": url,
            "params": params, "accept": "application/vnd.github+json", "status_code": code,
            "response_headers": {"content-type": "application/json", "x-ratelimit-remaining": "4999"},
            "body_encoding": "utf-8", "body": body, "body_bytes": len(raw),
            "body_sha256": hashlib.sha256(raw).hexdigest(), "truncated": False,
            "elapsed_ms": 12.5, "error": None}))
    data = ("\n".join(lines) + "\n").encode()
    (d / "exchanges.jsonl").write_bytes(data)
    meta = {"capture_id": cid, "format_version": "1", "created_at": "2026-11-20T02:00:00Z",
            "finalized_at": "2026-11-20T02:00:05Z", "connector": "github_api",
            "target": {"repo": "owner/demo-shop"}, "auth_mode": "token (env GITHUB_TOKEN)",
            "purpose": "pilot", "incident_id": "INC-0000", "code_revision": "abc",
            "n_exchanges": len(lines), "files": {"exchanges.jsonl": hashlib.sha256(data).hexdigest()}}
    meta.update(meta_over)
    (d / "capture.json").write_text(json.dumps(meta))
    return d


EX = [("https://api.github.com/repos/owner/demo-shop/commits", {"per_page": 5}, 200, '[{"sha": "7addfee"}]'),
      ("https://api.github.com/repos/owner/demo-shop/commits", {"per_page": 5}, 200, '[{"sha": "second"}]'),
      ("https://api.github.com/repos/owner/demo-shop/contents/a.py", {"ref": "7addfee"}, 200,
       '{"content": "headers = {\\"Authorization\\": f\\"Bearer {tok}\\"}"}')]


def test_valid_capture_and_replay(tmp_path):
    d = write_capture(tmp_path, EX)
    assert verify_capture(d, token=TOKEN) == []        # code text in a body is not a leak
    r = Replay(d)
    url = EX[0][0]
    assert r.lookup("GET", url, {"per_page": 5}, "application/vnd.github+json")["body"] == EX[0][3]
    assert r.lookup("GET", url, {"per_page": 5}, "application/vnd.github+json")["body"] == EX[1][3]
    assert r.lookup("GET", url, {"per_page": 5}, "application/vnd.github+json")["body"] == EX[1][3]
    assert r.lookup("GET", url, {"per_page": 9}, "application/vnd.github+json") is None


def test_token_anywhere_is_rejected(tmp_path):
    d = write_capture(tmp_path, [(EX[0][0], {}, 200, f'{{"leak": "{TOKEN}"}}')])
    probs = verify_capture(d, token=TOKEN)
    assert any("credential-like" in p or "GITHUB_TOKEN" in p for p in probs)
    with pytest.raises(ValueError):
        Replay(d)


def test_auth_in_request_fields_and_tampering(tmp_path):
    d = write_capture(tmp_path, [(EX[0][0] + "?access_token=x", {}, 200, "{}")])
    assert any("request-side" in p for p in verify_capture(d, token=TOKEN))
    d2 = write_capture(tmp_path / "b", EX)
    (d2 / "exchanges.jsonl").write_text((d2 / "exchanges.jsonl").read_text().replace("7addfee", "0000000"))
    probs = verify_capture(d2, token=TOKEN)
    assert any("sha256 does not match" in p for p in probs)
    assert any("body_sha256 mismatch" in p for p in probs)


def test_eval_purpose_and_bad_id_rejected(tmp_path):
    d = write_capture(tmp_path, EX, purpose="eval")
    assert any("purpose" in p for p in verify_capture(d, token=TOKEN))
    d2 = write_capture(tmp_path / "c", EX, cid="capture1")
    assert any("not a capture id" in p for p in verify_capture(d2, token=TOKEN))


def test_base64_body(tmp_path):
    d = write_capture(tmp_path, EX[:1])
    lines = [json.loads(l) for l in (d / "exchanges.jsonl").read_text().splitlines()]
    raw = b"\x00\x01binary"
    lines[0].update(body_encoding="base64", body=base64.b64encode(raw).decode(),
                    body_bytes=len(raw), body_sha256=hashlib.sha256(raw).hexdigest())
    data = ("\n".join(json.dumps(x) for x in lines) + "\n").encode()
    (d / "exchanges.jsonl").write_bytes(data)
    meta = json.loads((d / "capture.json").read_text())
    meta["files"]["exchanges.jsonl"] = hashlib.sha256(data).hexdigest()
    (d / "capture.json").write_text(json.dumps(meta))
    assert verify_capture(d, token=TOKEN) == []
    assert Replay.body_bytes(lines[0]) == raw
