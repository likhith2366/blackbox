# BlackBox repeat-run agreement

Diagnosable primary cases: 4. Passes found: [0, 1, 2]. Correctness is (provisional, automated); agreement compares normalised top-1 component/mechanism text and report status.

| incident_id | passes | statuses | status_agree | top1_agree | top1_correct_per_pass |
|---|---|---|---|---|---|
| INC-2691 | 3 | diagnosis, insufficient_evidence, diagnosis | no | no | no, no, no |
| INC-3172 | 3 | insufficient_evidence, diagnosis, diagnosis | no | no | no, no, no |
| INC-5408 | 3 | insufficient_evidence, insufficient_evidence, insufficient_evidence | yes | yes | no, no, no |
| INC-7735 | 3 | insufficient_evidence, insufficient_evidence, insufficient_evidence | yes | yes | no, no, no |


Top-1 accuracy per pass (provisional, automated): pass 0: 0.00, pass 1: 0.00, pass 2: 0.00
Min / max across passes: 0.00 / 0.00

Note: all passes use identical decoding settings (temperature and seed from the frozen RunConfig), so disagreement measures serving-stack nondeterminism, not sampling variance. Three passes on four cases is exploratory.
