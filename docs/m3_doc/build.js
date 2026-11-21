// Builds the Milestone 3 Word document. Formal-evaluation numbers come from data.json (gen_data.py),
// which reads the frozen scorer's output (results/eval_v1/per_run.csv).
const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell, WidthType, ShadingType,
  AlignmentType, LevelFormat, BorderStyle, HeadingLevel, Footer, PageNumber, ExternalHyperlink,
} = require("docx");

const data = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const OUT = process.argv[3];

const FONT = "Calibri";
const BODY = 19;      // 9.5 pt
const SMALL = 16;     // 8 pt in tables
const W = 12240 - 2 * 936;

function runs(text, size = BODY) {
  return text.split(/(\[\[[^\]]+\]\]|\*\*[^*]+\*\*|\*[^*]+\*)/).filter(Boolean).map(t =>
    t.startsWith("[[") ? new ExternalHyperlink({ link: "https://" + t.slice(2, -2),
      children: [new TextRun({ text: t.slice(2, -2), size, font: FONT, color: "1F4E9A", underline: {} })] })
    : t.startsWith("**") ? new TextRun({ text: t.slice(2, -2), bold: true, size, font: FONT })
    : t.startsWith("*") && t.endsWith("*") && t.length > 2 ? new TextRun({ text: t.slice(1, -1), italics: true, size, font: FONT })
    : new TextRun({ text: t, size, font: FONT }));
}
const P = (text, opts = {}) => new Paragraph({ children: runs(text, opts.size), spacing: { after: opts.after ?? 50, line: 240 } });
const B = (text) => new Paragraph({ children: runs(text), numbering: { reference: "bul", level: 0 }, spacing: { after: 15, line: 238 } });
let NUM = 0;
const N = (text, start = false) => { if (start) NUM += 1; return new Paragraph({ children: runs(text), numbering: { reference: "num", level: 0, instance: NUM }, spacing: { after: 15, line: 238 } }); };
const H = (text) => new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun({ text, font: FONT })] });

const border = { style: BorderStyle.SINGLE, size: 4, color: "B7B7B7" };
const borders = { top: border, bottom: border, left: border, right: border };
const PASS = "E2F0D9", FAIL = "FBE4E4", PART = "FFF2CC";
function table(widthsPct, rows, shade = null) {
  const widths = widthsPct.map(p => Math.floor(W * p / 100));
  widths[widths.length - 1] += W - widths.reduce((a, b) => a + b, 0);
  return new Table({
    width: { size: W, type: WidthType.DXA }, columnWidths: widths,
    rows: rows.map((r, i) => new TableRow({
      tableHeader: i === 0, cantSplit: true,
      children: r.map((c, j) => {
        const fill = i === 0 ? "E8EEF4" : (shade ? shade(r, j) : null);
        return new TableCell({
          width: { size: widths[j], type: WidthType.DXA }, borders,
          shading: fill ? { type: ShadingType.CLEAR, fill, color: "auto" } : undefined,
          margins: { top: 20, bottom: 20, left: 60, right: 60 },
          children: [new Paragraph({ spacing: { after: 0, line: 222 },
            children: i === 0 ? [new TextRun({ text: c, bold: true, size: SMALL, font: FONT })] : runs(c, SMALL) })],
        });
      }),
    })),
  });
}
const gap = () => new Paragraph({ spacing: { after: 40 }, children: [] });

// ---------------------------------------------------------------- data-driven text
const S = data.stats, bb = S.blackbox, C = data.checks;
const rng = a => a.length === 0 ? "none" : a.length === 1 ? `s${a[0]}` : `s${a[0]}–s${a[a.length - 1]}`;
const trioTxt = `all three systems on ${rng(data.trios)}` + (data.partial.length ? `; ${data.partial.map(n => "s" + n).join(", ")} partly run` : "");
const solv = s => `${S[s].correct} of ${S[s].solvable_run}`;
const pct = Math.round(100 * data.bb_redundant / Math.max(1, data.bb_calls));
const cell = n => data.table[n - 1];
const NAME = { blackbox: "BlackBox", b1: "B1", b0: "B0" };
const outcomeOf = (n) => {
  const t = cell(n);
  if (t.blackbox === "pending") return "not run yet";
  return ["blackbox", "b1", "b0"].map(k => `${NAME[k]} ${t[k].includes("(correct)") ? "PASS" : t[k] === "pending" ? "pending" : "FAIL"}`).join(", ");
};
const evalRows = [["Case (custom, frozen)", "Expected", "BlackBox", "B1", "B0"]];
for (const t of data.table) evalRows.push([`**${t.case}.** ${t.desc}`, t.expected, t.blackbox, t.b1, t.b0]);
const evalShade = (r, j) => j < 2 ? null : r[j] === "pending" ? null : r[j].includes("correct") ? PASS : FAIL;

const s9run = cell(9).blackbox !== "pending";
const abst = [5, 6].filter(n => cell(n).blackbox !== "pending");
const bbAbstOk = abst.filter(n => cell(n).blackbox.includes("(correct)"));
const abstTxt = abst.length === 0 ? "" : ` On the insufficient-evidence cases (${abst.map(n => "s" + n).join(", ")}), **BlackBox correctly abstained in ${bbAbstOk.length} of ${abst.length}**, while B1 said “no fault” and B0 gave a wrong diagnosis in each: the first place where checking evidence before answering clearly helped.`;
const checks = [
  ["Check", "Result"],
  ["Read-only safety: tool calls outside the allowlist, or write actions", `PASS: 0 attempted, 0 executed in all ${data.done} formal runs and all demo runs`],
  ["Tool budget (6 rounds, 12 calls, 3 per round) respected", `PASS: 0 violations`],
  ["Every citation in a final report points to a record the run retrieved", `PASS: 0 invalid citations`],
  ["Evidence gate stops diagnoses with too little support", "PASS: it refused 4 or more unsupported diagnosis attempts in each demo investigation"],
  ["Prompt injection (s9): planted instructions are not followed", s9run ? `${C.unauth_attempted === 0 ? "PASS" : "FAIL"}: ${C.unauth_attempted} unauthorized tool attempts` : "not run yet"],
  ["Correct abstention on insufficient-evidence cases (s5, s6)", `s5: ${outcomeOf(5)} | s6: ${outcomeOf(6)}`],
  ["No false diagnosis on the healthy case (s7)", cell(7).blackbox === "pending" ? "not run yet" :
    ["blackbox", "b1", "b0"].map(k => `${NAME[k]} ${cell(7)[k].startsWith("wrong") ? "FAIL (invented a cause)" : "PASS"}`).join(", ")
    + (cell(7).blackbox.includes("abstained") ? "; BlackBox abstained instead of answering “no fault”" : "")],
  ["Snapshots: no answer leakage, nothing after the cutoff", "PASS: 120 leakage tests; 12 of 12 snapshots valid (one known leak excluded in advance)"],
  ["Full demo, 10 steps, on two different real faults", "PASS: screenshots of every step"],
  ["Reproducibility: frozen code, config and data; hashes per run", "PASS"],
  ["Automated tests", "PASS: 370 passed, 0 failed (2 expected failures for known defects)"],
  ["Correct diagnosis on the solvable cases (s1–s4)", `FAIL: BlackBox ${solv("blackbox")}, B1 ${solv("b1")}, B0 ${solv("b0")}`],
  ["Retrieving the decisive evidence", `FAIL: BlackBox ${bb.dec_ret} of ${bb.dec_tot} records`],
  ["Baselines finish with a usable answer", `PARTLY: ${C.timeouts} timeouts and 1 answer cut off at the token limit`],
];
const checkShade = (r, j) => j !== 1 ? null : r[1].startsWith("PASS") ? PASS : r[1].startsWith("FAIL") ? FAIL
  : r[1].includes("PASS") && !r[1].includes("FAIL") ? PASS : PART;

const children = [
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 20 },
    children: [new TextRun({ text: "BlackBox: Milestone 3 (Results, Failures and Iteration)", bold: true, size: 30, font: FONT })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 90 },
    children: [new TextRun({ text: "Likhith Vardhan Goruputi (lvg8030) · Praneeth Yashovardhan Kadem (pk3206) · Bhavana Peruri (bp2847) · 19 November 2026", size: 18, font: FONT })] }),

  P("BlackBox investigates a production incident the way an on-call engineer would. It runs **once per incident, when a monitoring alert fires** (it is not a background process). It reads a frozen copy of the evidence available at that moment and answers with a **diagnosis** (which component failed and how, with cited evidence), **insufficient evidence**, or **no fault observed**. Everything runs on one laptop: qwen3:4b (4-bit) on Ollama, RTX 3060 6 GB GPU, 8,192-token context. All three systems below use the same model, settings (temperature 0, seed 7) and report format; only the way they gather evidence differs."),
  table([17, 43, 40], [
    ["System", "What it does", "Question the comparison answers"],
    ["**B0**: logs-only baseline", "One model call: the alert plus as many of the newest log lines as fit in the prompt (about 35 of 3,000 records). No tools.", "Does investigating beat simply reading the recent logs?"],
    ["**B1**: same-evidence baseline", "One model call: the alert plus **exactly the records BlackBox retrieved** on the same case. No tools, no rounds. If they do not fit, the oldest are dropped and the run is flagged and left out of this comparison.", "With identical evidence, does step-by-step reasoning help? (B1 vs BlackBox)"],
    ["**BlackBox** (the system under test; Milestone 2 feedback called it “B2”; there is no other B2)", "Up to **6 rounds and 12 tool calls (max 3 per round)**. Each round it updates its hypotheses, calls read-only tools, and may stop only when the evidence gate passes; otherwise it ends at the budget.", "Does iterative, evidence-checked investigation find the real cause?"],
  ]),
  P("*Terms.* **Decisive evidence**: the 3–4 records per case, fixed in a hidden answer key, that identify the cause (usually the fault commit, its deployment, one metric, one log line). **Correct diagnosis**: component and mechanism both match the key. **Abstention**: answering “insufficient evidence”; it is correct on s5/s6 and a miss on solvable cases. **Evidence gate (v1)**: BlackBox may diagnose only if its top hypothesis cites at least 2 retrieved records, one from logs, traces, metrics, configs or deployments. It checks evidence, not causal truth.", { after: 40 }),

  H("1. System implemented so far, and how the parts are integrated"),
  P("**One incident, end to end** (every step below runs on real software we built):"),
  N("**Fault.** In the React shop's operator panel we pick a fault. Our controller makes a real git commit (or config change / operator action), builds it with our build tool and deploys it to checkout-svc or payment-svc (payment-svc uses SQLite with 1.5 M rows).", true),
  N("**Symptom and alert.** A load generator sends real orders; the services write JSON logs, traces and 5-second metrics; an alert rule fires on the checkout error rate or latency."),
  N("**Snapshot.** A snapshot builder freezes every record available before the alert cutoff (logs, traces, metrics, deployments, builds, commits, configs, tickets) and writes the hidden answer key to a private folder the investigator cannot read."),
  N("**Investigation.** The BlackBox API runs the coordinator. The model proposes tool calls; the coordinator executes only the 11 read-only tools, checks every citation and enforces the budget and gate. Every round’s intermediate state (hypotheses, tool calls and results, gate decisions, tokens) is saved, so drift or bias across iterations can be inspected; the 8 repeat runs measure how consistent BlackBox is."),
  N("**Untrusted and conflicting evidence.** Ticket and log text is passed as quoted data, never as instructions; ticket comments written after the alert are hidden. When ticket comments contradict each other, all of them are shown with their timestamps and the model must settle the question with runtime evidence (logs, metrics, deployments). s3 contains such a ticket, but BlackBox never opened it, so the effect of contradictions is **not measured yet**; s9 tests planted instructions."),
  N("**Interface.** The React BlackBox app streams each round live, shows the final report, opens every cited record at its source, and keeps a history (with cancel/retry)."),
  N("**Evaluation.** A harness runs B0, B1 and BlackBox on the 9 frozen cases, one run at a time on the single GPU, records hashes of code, config, model and answer key, and scores the outputs against the keys."),
  P("**Integration status.** Verified end to end: shop UI and operator controls (2 faults), telemetry capture, alerts and snapshot builder (12 snapshots valid), BlackBox API and UI, harness and scorer. Built and tested but **not yet used by the model in an investigation**: the read-only GitHub connector (commits, files at a pinned commit, GitHub Actions; it read 3 real CI runs). Live reading of the services' own log files worked in one live run (15 records). Jira and Jenkins are not connected: tickets are team-written fixtures and builds use our own tool."),
  P("**Code and data.** BlackBox, evaluation, results and screenshots: [[github.com/likhith2366/blackbox]] (branch main holds the frozen evaluation code and all results; branch live-integration holds the later API and UI additions, kept off main so the running evaluation uses unchanged code). Demo company: [[github.com/likhith2366/blackbox-demo-shop]]. Start everything with python scripts/start_demo.py; docs/DEMO_WALKTHROUGH.md lists the demo steps."),
  P("**How the test cases were made.** All nine cases are custom faults on this shop (listed in the table in Section 3), following the Milestone 2 plan: two misleading-symptom, two unrelated-change, two insufficient-evidence, one healthy, one connector failure and one prompt injection. Snapshots and keys were frozen before any evaluation run, and no prompt was tuned on them."),

  H("2. Baseline results"),
  P("**Pilot (development incident INC-0000, retry amplification).** All three systems missed and none retrieved a decisive record. B0 said “database lock contention” and B1 a Windows connection error; both were wrong. BlackBox abstained after spending 5 of its 6 calls on trace searches, 3 of them identical repeats. The formal baseline results are in Section 3."),

  H("3. Current results (formal evaluation, frozen)"),
  P(`**${data.done} of 47 planned runs are complete** (${trioTxt}). Verdicts come from an automated matcher and are provisional until the two-person review. “Evidence x/4” means decisive records retrieved.`),
  table([40, 8, 18, 17, 17], evalRows, evalShade),
  gap(),
  P(`Correct top-1 diagnoses on the four solvable primary cases (s1–s4): BlackBox **${solv("blackbox")}**, B1 **${solv("b1")}**, B0 **${solv("b0")}**. BlackBox retrieved ${bb.dec_ret} of ${bb.dec_tot} decisive records, and ${data.bb_redundant} of its ${data.bb_calls} tool calls (${pct}%) were identical repeats. The two s1 baseline timeouts occurred while the GPU was overheating (96 °C, clock 307 of 2,100 MHz); runs on a cool GPU took 11–40 s.`),

  H("4. Experiments and comparisons completed"),
  B("**Prompt change (before/after, pilot incident, 2 runs each).** Adding a note that listed the sources not yet queried changed the outcome from abstaining (0/4 evidence) to a **wrong diagnosis that passed the gate** in both runs. We rejected the note; it is not in the frozen setup."),
  B(`**BlackBox vs B1 vs B0** on ${rng(data.trios)} (table above). BlackBox abstains or names a symptom; B1 has wrongly said “no fault” (s2) and once ran out of output tokens (s4); B0 commits to a symptom-level cause.${abstTxt}`),
  B("**Two end-to-end demos through the UIs** (development incidents INC-9001 connection leak, INC-9002 retry policy): both abstained, with 1/4 decisive records each."),

  H("5. What worked (checks that passed, and those that did not)"),
  table([46, 54], checks, checkShade),
  gap(),

  H("6. What failed"),
  B("**No correct diagnosis yet** on any solvable case, by any system."),
  B("**BlackBox never queried deployments or commits** in any run, although every fault was a change recorded there."),
  B("**One tool call per round** even though 3 are allowed, so the 6-round limit ends runs with half the budget unused; many calls are identical repeats."),
  B("**Symptom labels pass the gate** (pilot with the note, s3): the gate counts citations, it cannot tell a symptom from a cause."),
  B("**Early abstention** (s2: gave up after one metrics query) and **baseline failures** (2 timeouts on an overheating GPU, 1 answer cut off at the token limit)."),

  H("7. Failure analysis and likely causes"),
  P("**Worked example (INC-9001).** The fault commit, its deployment and the key metric were each the *first* result of a simple no-argument call (search_commits, list_deployments, get_metrics on payment-svc), but those calls were never made. The fourth decisive record, a “db pool acquire timed out” log, was retrieved three times and never cited. Nothing was truncated. **The evidence was there; the model did not look in the right place.** Likely causes, strongest first:"),
  N("**Symptom-first search.** The model keeps querying the alerting service. Our prompt says “the most recent change is not automatically the cause”, which a small model may read as “ignore changes”, and nothing forces a change check.", true),
  N("**Copying the example.** The prompt's only example call is a log search with level ERROR, limit 15; INC-9002 sent exactly that call 5 times in a row."),
  N("**Small-model planning.** The 4B model plans one step at a time and holds on to one weak hypothesis."),
  N("**Gate v1 counts evidence, not causality**, so symptom-level answers can pass."),
  N("**Hardware.** Overheating and memory pressure coincided with the timeouts; we record this as an observed condition, not as the cause of every failure."),

  H("8. Changes attempted and their effects"),
  table([34, 66], [
    ["Change", "Effect"],
    ["Coverage note in the prompt (only prompt-tuning experiment)", "Made results worse (wrong diagnosis passed the gate, 2 of 2 runs). Rejected. Because a wording change alone hurt, we expect little from further prompt tuning; our next change is a rule in the coordinator (Section 10), tested on development incidents, never on frozen cases."],
    ["First round must call a tool; forced decision when the budget ends", "No empty or runaway runs since. Accuracy effect not measured."],
    ["Citation checking; explicit failures for bad tool output and model JSON; prompt fitted to the context", "Fabricated ids are no longer counted, crashes and silent overflows are gone. Verified by tests."],
    ["Answer-key hash check; second scorer that also counts cited evidence", "Caught one real scoring error; the second scorer changed no counts."],
    ["Scheduler that waits for a cool GPU and free memory", "Runs after it was added finished in 11–40 s with no timeouts. A bug in its throttle check was found and fixed."],
  ]),
  gap(),

  H("9. Remaining gaps, and what we are still waiting for"),
  P(`**Why the evaluation is not finished.** The 47 runs (9 cases × 3 systems, 8 BlackBox repeats, 12 ablations) share one 6 GB GPU and must run one at a time. To avoid the overheating timeouts we saw, the scheduler waits between runs until the GPU is below 80 °C and 1.5 GB of memory is free, so each run takes about 1–3 minutes of wall time. **${47 - data.done} runs remain**; they are running now and will finish well before the final evaluation week. After that come the two-person review of every verdict and claim, and the repeat and ablation comparisons.`),
  P(`**Gaps against our Milestone 2 success criteria.** Success was defined as a reproducible evaluation (achieved), with accuracy left open. Still open: top-1/top-3 accuracy (currently ${solv("blackbox")} for BlackBox); ${data.trios.length < 9 ? "the remaining main cases until they finish; " : ""} the unsupported-claim rate, which needs the human review; repeat agreement and the three ablations; and use of GitHub evidence by the model.`),

  H("10. Prioritized plan for the final two weeks"),
  N("**Finish all 47 frozen runs (by Nov 21)**, plus a separately labelled rerun of the timed-out cells; originals stay.", true),
  N("**Two-person review (Nov 21–23)** of every verdict and claim, including a teammate check of each answer key."),
  N("**One targeted fix for the main failure (Nov 19–23, on development incidents):** require one deployment query and one commit query before any conclusion, refuse identical repeat calls, and encourage up to 3 calls per round. Run it as a new labelled version (v2) and compare decisive-evidence coverage and accuracy with v1; frozen v1 results stay unchanged."),
  N("**If that helps, gate v2:** a diagnosis must cite the change that links the component to the symptom."),
  N("**Final evaluation and submission (Nov 23–Dec 3):** v1 vs v2 vs baselines, repeats, ablations, latency per run, example successes and failures, PDF report and 8–10 minute demo."),
];

const doc = new Document({ creator: "Likhith Vardhan Goruputi", title: "BlackBox Milestone 3",
  styles: {
    default: { document: { run: { font: FONT, size: BODY } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 22, bold: true, font: FONT, color: "1F3864" },
        paragraph: { spacing: { before: 90, after: 35 }, keepNext: true, outlineLevel: 0 } },
    ],
  },
  numbering: { config: [
    { reference: "bul", levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
      style: { paragraph: { indent: { left: 300, hanging: 200 } } } }] },
    { reference: "num", levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT,
      style: { paragraph: { indent: { left: 300, hanging: 240 } } } }] },
  ] },
  sections: [{
    properties: { page: { size: { width: 12240, height: 15840 }, margin: { top: 720, bottom: 720, left: 936, right: 936 } } },
    footers: { default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER,
      children: [new TextRun({ children: [PageNumber.CURRENT], size: 16, font: FONT })] })] }) },
    children,
  }],
});
Packer.toBuffer(doc).then(b => { fs.writeFileSync(OUT, b); console.log("wrote", OUT); });
