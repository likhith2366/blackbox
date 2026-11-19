# Demo company repo provenance (blackbox-demo-shop)

Recorded 2026-11-20 ~04:05Z by Agent 2. Commands used: `git rev-parse` (local) and
`git ls-remote origin` (read-only; nothing pushed, no remote tag changed).

- Local repo: `A:\subjects\genAI\final_project\blackbox-demo-shop`
- Remote: https://github.com/likhith2366/blackbox-demo-shop

## Branch and tags

| ref | local | remote (ls-remote) | same? |
|---|---|---|---|
| main | b7ae74f40b4cbabbd9f731663eb42bf8b238b75f | b7ae74f40b4cbabbd9f731663eb42bf8b238b75f | yes |
| baseline (tag) | b7ae74f40b4cbabbd9f731663eb42bf8b238b75f | not on remote | local only (it is the same commit as remote main) |
| capture/INC-0000 | e7a0cc0f0e8492abe652cb31eec201d653cffb1d | e7a0cc0f0e8492abe652cb31eec201d653cffb1d | yes |
| capture/INC-1846 | b7ae74f40b4cbabbd9f731663eb42bf8b238b75f | – | local only (tag = baseline; s5's fault is an operator restart, not a commit) |
| capture/INC-2264 | 0b16cdf0385e7860768988e09954f96b0b34e157 | – | local only |
| capture/INC-2691 | 0791ee4ab5caa4836a4d3c7a112e271570e3f009 | – | local only |
| capture/INC-3172 | 44c654c3cd9c582aa5350e0528552e79edbce370 | – | local only |
| capture/INC-4417 | 65fbf2beb59563261678d9cdb8ba530363888e23 | – | local only |
| capture/INC-5408 | 5d2e089b505ea6721ba84e1739269eee1a8d846e | – | local only |
| capture/INC-6053 | ea3a3b6a3f643a3110c5963fd9fbdf9ca559a501 | – | local only |
| capture/INC-7735 | 14644684fbaed7480cabf4996f21d06aece677ec | – | local only |
| capture/INC-9001 | d142601829cee858851b38811fb27a5ef4621880 | – | local only |
| capture/INC-9002 | 0d6ba14efa876f117f0951c0916da6b9fa7e1887 | – | local only |

Each `capture/<id>` tag points at the HEAD of `main` at the end of that capture, so it is the
last change commit of that capture.
The remote tag capture/INC-0000 was earlier 063397d, the rewritten copy of the first pilot capture.
It now equals the local e7a0cc0, the commit of the pilot recapture.

## Commits behind each incident (from the answer keys' `injected_fault.all_changes`)

| incident | change (private id) | commit | on GitHub? |
|---|---|---|---|
| INC-0000 pilot | retry-policy | e7a0cc0 | yes (tag capture/INC-0000) |
| INC-3172 s1 | pool-fastpath | 44c654c | no (local only) |
| INC-5408 s2 | h-checkout-qty-limit / index-migration | e17143b / 5d2e089 | no |
| INC-2691 s3 | endpoint-port / h-payment-currency-log | 7595162 / 0791ee4 | no |
| INC-7735 s4 | payment-stop (operator action, no commit) / h-checkout-item-log | – / 1464468 | no |
| INC-1846 s5 | pool-override (operator restart with env override, no commit) | – | n/a |
| INC-6053 s6 | index-migration | ea3a3b6 | no |
| INC-4417 s7 | h-checkout-item-log | 65fbf2b | no |
| INC-8290 s8 | replay of the INC-3172 capture | 44c654c | no |
| INC-2264 s9 | h-checkout-qty-limit / index-migration | 33ebea9 / 0b16cdf | no |
| INC-9001 demo | pool-fastpath | d142601 | no |
| INC-9002 demo | retry-policy | 0d6ba14 | no |

Every snapshot carries its own export of the repo history (`commits.jsonl`, `configs.jsonl`),
so the evidence stays complete without GitHub. The only incident whose fault commit can be
checked on GitHub today is INC-0000. Pushing the other capture tags is the user's decision.
All commits on these refs are authored by the two team members.
