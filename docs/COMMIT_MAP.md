# Recorded code revisions and published commits

Run records (`output.json` → `config.code_revision`, `provenance.json` → `blackbox_commit`, UI `meta.json`)
name the commit in the working repository where the run was made. The published branch `review/live-stack`
carries the same changes under different commit ids (the published history leaves out internal coordination
notes). This table maps one to the other, in order.

| Recorded in runs | Published | Change |
|---|---|---|
| `4997036` | `bfb4a9d` | Read logs from Grafana Loki: connector, shipper, config and tests |
| `eed75ca` | `9d0c800` | Live stack: Loki, Prometheus, Tempo, deployment records, GitHub, Jenkins and Jira connecto |
| `3dc11aa` | `7800ca9` | Live stack: widen Prometheus step for long windows, verification evidence, two live invest |
| `d5a7905` | `ba21ba3` | Ask BlackBox chat with answer verification; tabbed run page; live fault sweep script |
| `a443170` | `a863b9c` | Chat: answer-only step after the tool budget; live incident uses the latest alert episode; |
| `af895db` | `4941dff` | Chat verification: quote-insensitive matching, flag absence claims about sources never que |
| `68cf890` | `f23ca29` | Chat docs, example screenshot and saved conversations; live sweep scorer |
| `3d3775f` | `bfb8e17` | Live fault sweep with v1 settings: 6 faults, outputs and ground truth |
| `e1dff01` | `c7ae7c5` | Reasoning probe v3: 24 outputs on corrected config packets, blinded copies |
| `626b472` | `c14a5e2` | Step 6 results: config provenance fix removes the INC-9001 distractor; reviews and human s |
| `82f7326` | `2382569` | Local git code source for live mode (explicit, read-only), tests |
| `88f09e9` | `da99186` | Live fault sweep with v2 settings |
| `77877e1` | `8fd3790` | Live fault sweep with local git code source; sweep summary |
| `af1c0d5` | `e389556` | Dev options: change-chain expansion and from-scratch synthesis (off by default); dev_v3 co |
| `57b3f22` | `9c24903` | Synthesis: 1600 output tokens and recovery of an output cut after the hypotheses; keep the |
| `2d62a31` | `95ec232` | Live fault sweep with dev_v3 (change expansion + synthesis) and local git |
| `08d4cb2` | `73c5b06` | Live sweep write-up: dev_v3 results |
| `c41b6c6` | `13d1a0e` | dev_v3c config: dev_v3 with the causal-evidence gate |
| `491268d` | `828a84f` | Fix: a richer copy of an evidence record replaces a shorter one (commit diff after listing |
| `0fad2dc` | `45a1098` | Local git: attribute commits to services from their changed paths |
| `7a2063d` | `d951e05` | Causal gate: onset is the latest error episode; sweep settles longer than the incident loo |
| `819830a` | `7e7eeb3` | Live sweep dev_v3c: 2 correct, 0 wrong, 2 abstained |
| `5cd8902` | `8a1ebd7` | Synthesis option: gate-checked ranking (first synthesized hypothesis the gate accepts); de |
| `ed2382d` | `b7e2bfb` | Sweep: require healthy traffic before injecting |
| `007eda1` | `2ecff6b` | Causal gate: a change-based claim needs a change inside the incident window |
| `d069dac` | `4ca5d85` | Live sweep dev_v3d (before the in-window rule) |
| `140b5bc` | `2c54317` | Live sweep v3e: 2 correct, 1 partially correct, 1 abstained, 0 wrong |
| `72094c5` | `db1c560` | Chat follows in-window deployments to their build and commit; UI marks automatic calls |
| `9748dfe` | `f4a1666` | dev_v3d on the snapshot development incidents |
| `0b67600` | `595659d` | Chat: catch 'no evidence of recent deployments'-style claims; ask to check changes before  |
| `6433d80` | `63e1192` | Chat verification: v-prefixed versions and alert details; code source configurable in .env |
| `dba0478` | `6393edb` | Chat: verified live answer on the retry fault; docs and screenshot |
| `3157be9` | `6c5ec84` | Dev option runtime_context: error and lifecycle logs before synthesis; dev_v3e config |
| `4bedd9d` | `a5dd21c` | Live sweep dev_v3e: 3 correct, 1 partially correct, 0 wrong |
| `1300f9f` | `cef1c72` | Local git: function-context diffs; synthesis shows code changes up to 5000 chars |
| `f1cc034` | `370be3d` | Live sweep with function-context diffs: leak still not inferred; retry direction misread o |
| `fdd6f3c` | `a32a739` | UI and API: choose standard (evaluated) or improved (development) investigator settings |
| `fd55ec1` | `6d0e4ab` | Sweep: optional extended latency/error alerts for both services |
| `75215f6` | `4a3c33b` | Sweep: database health alerts in the extended set; record the unalerted run |
| `087fc66` | `f529233` | Rename the run without database alerts |
| `e0fdbcc` | `3e511ba` | Live sweep with database alerts: index-migration found, pool-override abstained; summary o |
| `d742132` | `33bddb9` | Local git: shared-library commits name no service (deployments say who received them) |
| `a85f043` | `de0dc0d` | Live sweep repeat: results and write-up |
| `de670a0` | `9f7c577` | Live session logs and controller record |
| `fe332f4` | `3b77d58` | Local git: tests for shared-library attribution (checkout version bump -> checkout-svc onl |
| `950b073` | `bc2e6da` | Live runs record the code revision at process start, settings-file and effective-config ha |
| `1c65f87` | `9c76bc2` | Demo control: reset can write telemetry where the live stack reads (DEMO_TELEMETRY=stack); |
| `4c1d6d4` | `c5d7d6a` | Retry-policy rerun after the attribution fix (dev_v3e, extended alerts): insufficient evid |
| `991d537` | `1263663` | Live demo rehearsal driver: shop and BlackBox UIs end to end, verbatim job copies, no-aler |
| `8248665` | `ff95571` | Live stack: GitHub Actions stays a CI source when code is read from local git (it crashed  |
| `c4891ca` | `44ba0ef` | Docs: retry rerun result, recorded-revision correction for the repeat sweep, defects D18-D |
| `7be4111` | `8ebeb3b` | Rehearsal driver: configurable GPU temperature ceiling before injecting |
| `6fd4452` | `3c3c21f` | Chat view: effect must not return scrollIntoView's result (blank page in current Edge) |
| `019bd42` | `3a32378` | Rehearsal driver: keep the steps already recorded when a later step fails |
| `9256cf1` | `6272152` | Live demo rehearsal 2026-11-26: endpoint-port and payment-stop investigated through the UI |
| `0b6bbd8` | `62801fb` | Rehearsal write-up and service logs |
