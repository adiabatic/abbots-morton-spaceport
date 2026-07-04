export const meta = {
  name: 'policy-round-2-lever-hunt',
  description: 'Exhaustively build-test whether ANY closed-vocabulary policy record can faithfully revert a round-2 en-ext-1 reject without collateral',
  phases: [{ title: 'LeverHunt', detail: 'parallel agents propose + scratch-build-test candidate records across distinct lever families' }],
}

const PROTOCOL = `
GOAL: The 29 round-2 reject windows all share one phenomenon — the new pipeline puts a +1px "en-ext-1" baseline-entry extension on ·May where the old font did not. The reviewer rejected this. Round 1 claimed NO clean revert exists in the closed when:-vocabulary. Your job is to TRY HARD to disprove that by finding a closed-vocabulary policy record (in glyph_data/runes/*.yaml) that faithfully reverts at least one reject to OLD ink with ZERO collateral, and to BUILD-TEST every candidate. If you cannot, your negative result (with build evidence) strengthens the engine-work conclusion.

CODEPOINTS: E650=qsPea E652=qsTea E665=qsMay E670=qsIt E679=qsOy 0020=space 00B7=namer-dot 200C=ZWNJ.

THE TWO REPRESENTATIVE TARGETS (grep the control audit for exact old/new strings):
- SEAM 1 (Pea-May-Tea under ss03): row "ss03  E650:E665:E652" — OLD (baseline col) May = ...ex-y5.ex-ext-1 (NO en-ext-1); NEW May = en-ext-1+ex-ext-1. Want: drop en-ext-1, keep everything else, producing old ink.
- SEAM 2 (Oy-It-May, default): row "default  E679:E670:E665" — OLD May = en-y0.ex-y5 (NO en-ext-1); NEW May = baseline/en-ext-1. Want: drop en-ext-1.

THE MECHANISM: May en-ext-1 comes from qsMay.policy.extend[3] = {stance: loop, entry: baseline, by: 1, when: {left: {family: [qsPea, qsTea, qsTea_qsOy, qsYe, qsHe, qsIt], joined_at: baseline}}}. It fires whenever May immediate-left exits at baseline. Read rebuild/pipeline/settle.py (extend/contract/refuse application and the same-seam non-summing suppression, around lines 900-1000), rebuild/pipeline/specificity.py (record ordering), rebuild/schema/rune.schema.json (legal when: clauses — NOTE leftCondition has NO then), and rebuild/m1-divergences.yaml lines ~81-97 (round-1 documented negatives for halves-entry-extension-restored and same-seam-extension-non-summing). Round 1 already disproved a qsMay entry-side contract counter-record (6 UNMATCHED rows; "entry-side extend and contract do not net at name grain"). Find a DIFFERENT lever.

HARD GUARD WINDOWS (must NOT change — approved/either windows carrying the SAME en-ext-1 that the user approved):
- u-0393 = ss03 E650:E665:E652:E652 (Pea-May-Tea-Tea, approved; May = en-ext-1+ex-bind-pulled-back)
- the 40 ss03-chain X-May-Tea approvals and u-0468 (Tea-May-Tea ss03)
- the 93 extend[3] approvals: e.g. E670:E665 (It-May word-initial), E670:E670:E665 (It-It-May), and CRITICALLY E679:E670:E665:E652 (Oy-It-May-Tea, APPROVED — ink-identical Oy-It-May prefix to the SEAM-2 reject E679:E670:E665, differing only in the 4th glyph)
- the 28 same-seam-extension-non-summing approvals.

SCRATCH BUILD HARNESS (validated faithful — reproduces the control audit byte-identically):
1. Pre-edit control audit: tmp/round2-control-audit.tsv (columns: config TAB codepoints TAB kinds TAB matched_entry TAB baseline TAB new).
2. Make your OWN scratch dir (unique label): mkdir -p tmp/scratch/LEVH-<label>/runes ; cp glyph_data/runes/*.yaml tmp/scratch/LEVH-<label>/runes/
3. Edit ONLY your scratch copy under tmp/scratch/LEVH-<label>/runes/ (NEVER touch glyph_data/runes/).
4. Build + oracle: uv run python tmp/scratch_build.py tmp/scratch/LEVH-<label>/runes tmp/scratch/LEVH-<label>/out  → prints JSON {defect_errors, divergent_rows, unmatched, multi_matched, pass, audit}.
5. Diff vs control: diff <(sort tmp/round2-control-audit.tsv) <(sort tmp/scratch/LEVH-<label>/out/divergence-audit.tsv)  → '<' = control rows removed/changed, '>' = new rows.

SUCCESS CRITERION (strict — a clean closed-vocabulary revert):
(a) at least one TARGET reject window NEW column becomes byte-equal to its OLD/baseline ink (en-ext-1 dropped), AND
(b) the scratch oracle has unmatched == 0 and defect_errors == [] (ledger-expressible, gate-clean), AND
(c) ZERO non-target windows change in the audit diff (no guard window touched, no approved-class row flips to UNMATCHED).
If (a) holds but (b)/(c) fail, that is a FAILED lever (over-reach or breaks the oracle) — report with the collateral. Only all-three = a genuine lever.

Do NOT touch glyph_data/runes/, rebuild/, or any tmp file outside your own tmp/scratch/LEVH-<label>/. Build as many distinct candidates as you can; each build is ~7s.
`

phase('LeverHunt')

const SCHEMA = {
  type: 'object',
  properties: {
    candidates: { type: 'array', items: { type: 'object', properties: {
      label: {type:'string'},
      lever_family: {type:'string'},
      file: {type:'string'},
      record_yaml: {type:'string', description:'the exact policy record YAML added/changed in the scratch rune file'},
      target_rejects: {type:'array', items:{type:'string'}},
      built: {type:'boolean'},
      oracle_unmatched: {type:'number'},
      defect_errors: {type:'number'},
      target_reverted_to_old_ink: {type:'boolean'},
      collateral_windows_changed: {type:'array', items:{type:'string'}, description:'non-target windows that changed (guard breaks / over-reach); empty if none'},
      verdict: {type:'string', enum:['clean-lever','reverts-but-breaks-oracle','reverts-but-collateral','no-revert','schema-invalid']},
      evidence: {type:'string', description:'the decisive audit-diff lines or oracle JSON'}
    }, required:['label','file','record_yaml','built','verdict'] } },
    conclusion: {type:'string', description:'did ANY candidate meet the strict success criterion? If not, the specific structural reason no closed-vocabulary lever works.'}
  },
  required: ['candidates','conclusion']
}

const families = [
  {label:'may-right-scoped-suppress', desc:'Try to suppress qsMay extend[3] en-ext-1 via a qsMay.policy record (refuse/contract/extend-narrowing) keyed on May RIGHT context (right: qsTea half/x-height — the "May also exits toward half-Tea" case that distinguishes the SEAM-1 reject from the pulled-back-approved u-0393). Goal: drop en-ext-1 only when May ALSO carries ex-ext-1 (the summed case). Test whether a self/right condition can express "May has an x-height exit extension".'},
  {label:'may-extend-narrowing-and-nonsumming', desc:'Try narrowing qsMay.policy.extend[3] family list or its when:, and try to make the same-seam non-summing suppression (settle.py) fire on these windows (e.g. by giving the predecessor an exit extension so left.settled.extension>0 suppresses May entry-extend). Determine empirically whether any narrowing reverts a reject without dropping en-ext-1 from the 93 approvals.'},
  {label:'predecessor-exit-levers-seam1', desc:'SEAM 1: try predecessor-side records on qsPea (and qsTea for the Tea-Pea-May-Tea window): refuse/extend/contract or exit changes that drop May en-ext-1 for Pea-May-Tea-ss03 WITHOUT killing the Pea-to-May join (the naive {cell:{exit:none}} kills the join and yields UNMATCHED — avoid that). Also test a qsMay left-condition variant scoped to ss03 + right-half-Tea.'},
  {label:'it-side-levers-seam2', desc:'SEAM 2: qsIt sees Oy (left) and May (right) DIRECTLY. Try qsIt.policy records (refuse/extend/contract/exit changes) keyed on {left: qsOy, right: qsMay} to suppress May en-ext-1 in Oy-It-May. Critically test whether ANY such record can separate the reject E679:E670:E665 (Oy-It-May) from the APPROVED ink-identical sibling E679:E670:E665:E652 (Oy-It-May-Tea) — they share the Oy-It-May prefix and differ only in the 4th glyph.'},
  {label:'ligature-and-same-seam', desc:'Try records for u-0398 (E652:E679:E665:E652, the qsTea_qsOy ligature lead into May — a qsTea_qsOy.policy record) and the same-seam pair u-0417 (E650:E665:E670:E665, Pea-May-It-May) and u-0437 (200C:E665:E670:E665, ZWNJ-May-It-May) — qsMay records keyed on {right: qsIt, then: qsMay} (rightward then IS legal). Determine if these specific windows admit a clean revert even if the bulk seams do not.'},
]

const results = await parallel(families.map(f => () =>
  agent(`${PROTOCOL}\n\nYOUR LEVER FAMILY (${f.label}): ${f.desc}\n\nUse tmp/scratch/LEVH-${f.label}/ as your scratch root. Propose and BUILD-TEST every candidate you can think of in this family. Return structured results. Be rigorous and honest: a candidate earns verdict 'clean-lever' ONLY if it meets ALL THREE success criteria with build evidence.`,
    {label: f.label, phase:'LeverHunt', schema: SCHEMA})
))

return { families: families.map(f=>f.label), results }
