"""STEP 6 (development only): the STEP 5 packet rule, rebuilt after the get_config provenance fix.

Identical to scripts/reasoning_probe_v2.py (same rule, prompt, schema, models and settings); only the output
directory differs, and the config records now come from the corrected get_config (comparison with the previous
deployment of the same service). runs/reasoning_probe_v2 is left untouched.

    python scripts/reasoning_probe_v3.py build
    python scripts/reasoning_probe_v3.py run --model qwen3:4b --one
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import reasoning_probe_v2 as v2  # noqa: E402

v2.OUT = ROOT / "runs" / "reasoning_probe_v3"

if __name__ == "__main__":
    if sys.argv[1:2] == ["build"]:
        v2.build()
    else:
        v2.run(sys.argv[sys.argv.index("--model") + 1], one="--one" in sys.argv)
