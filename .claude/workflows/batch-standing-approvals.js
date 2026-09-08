export const meta = {
  name: 'batch-standing-approvals',
  description: 'Turn a docket of approved review-surface units into standing-approval rules, commit one commit per phenomenon on the current branch, then launch the detached gate-and-cycle chain',
  whenToUse: 'The batch form of the dont-bug-me-about-this-ever-again skill. args: the unit ids, as an array, a whitespace-separated string, or {units: [...]}. Running it is the go-ahead for the per-phenomenon commits.',
  phases: [
    { title: 'Cluster', detail: 'a clean-tree check, then one probe over every unit, the distinct changes clustered by phenomenon and balanced by work' },
    { title: 'Analyze', detail: 'one read-only analyst per cluster, at most three holding a surface at once, each rule proven on a scratch rules copy', model: 'opus' },
    { title: 'Land', detail: 'one lander, serial, in dependency order: paste, one whole-domain dry run, one targeted test run, one commit per phenomenon' },
    { title: 'Verify', detail: 'one dry run over the live rules and one probe over the whole input list' },
    { title: 'Launch', detail: 'the detached make test-rebuild && make review-cycle SERVE=bg chain, only on a clean tree with commits behind it' },
  ],
}

const raw = args && typeof args === 'object' && !Array.isArray(args) && 'units' in args ? args.units : args
const listed = Array.isArray(raw) ? raw : typeof raw === 'string' ? raw.split(/[\s,]+/) : []
const UNITS = [...new Set(listed.map(unit => String(unit).trim()).filter(Boolean))]
if (!UNITS.length) return { error: 'batch-standing-approvals takes unit ids as args: an array, a whitespace-separated string, or {units: [...]}' }
log(`${UNITS.length} unit ids`)

const SCRATCH = 'tmp/standing-wf'
const SURFACE_HOLDERS = 3
const SKILL = '.claude/skills/dont-bug-me-about-this-ever-again/SKILL.md'
const TARGETED_TESTS = 'uv run pytest rebuild/test_standing_verdicts.py rebuild/test_standing_probe.py --lane contracts -n auto --dist worksteal'
const CHAIN = 'nohup caffeinate -i sh -c \'make test-rebuild && make review-cycle SERVE=bg; echo "rc=$?"\' > tmp/cycle-pass.log 2>&1 &'

const COMMON = `You work in the git repository at your working directory (its CLAUDE.md names Abbots Morton Spaceport). Read, in this order, before anything else: CLAUDE.md, and obey it in full; ${SKILL} in full, including its Batches section, which is the procedure you are one stage of; the module docstring at the top of rebuild/tools/standing_verdicts.py, the authority on every rule shape and on the composed reading (never restate its contracts anywhere); doc/parallelism.md and doc/running-long-steps.md; and skim rebuild/standing-approvals.yaml for the file's idiom (ids, the agent-written note: voice, match blocks, cell lists, except_left).

Surface loads: every standing_probe.py call except a bare --shapes, and every standing_verdicts.py run, loads the review surface; the skill's Concurrency bullet says what that costs and why the cap is procedure. You run at most one surface-loading process at a time, and other agents may be holding one beside you. Batch every unit id and flag you already have into one call. Give each such Bash call a timeout of 600000 ms and redirect its output to a file under ${SCRATCH}/ (the project's tmp/, which is gitignored; never /tmp), then read that file with grep or sed, never whole.

Never run make test, make test-rebuild, make review-cycle, make artifact-cycle, make kernel-gate, make conform-deep or rebuild_gate: this workflow's last step launches the gate-and-cycle chain once, after every commit. Never start or stop the review server. Nothing but this workflow's lander commits; git status, git diff, git log and git show are yours. Never edit rebuild/review-census-pins.json.

The user approved every window in this batch at a sitting, and that approval is the decision to record; the skill's step 3 still applies to each phenomenon: find the record (a verdict family, the rune edit found by git log -S<token> -- glyph_data/runes/qs<Family>.yaml, a sitting note) and cite it in the note:. A survey whose siblings carry neither or reject on the same stroke is a conflict to hold out and report, never a rule.

Your final message is the structured result and nothing else.`

const UNIT_WHY_LIST = { type: 'array', items: { type: 'object', properties: { unit: { type: 'string' }, why: { type: 'string' } }, required: ['unit', 'why'] } }
const NAME_WHY_LIST = { type: 'array', items: { type: 'object', properties: { name: { type: 'string' }, why: { type: 'string' } }, required: ['name', 'why'] } }
const STRING_LIST = { type: 'array', items: { type: 'string' } }

const CLUSTERS_SCHEMA = {
  type: 'object',
  properties: {
    head_before: { type: 'string' },
    tree_clean: { type: 'boolean', description: 'git status --porcelain printed nothing before anything else ran' },
    dirty_paths: STRING_LIST,
    probe_dump: { type: 'string' },
    shapes_menu: { type: 'string' },
    clusters: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          key: { type: 'string' },
          title: { type: 'string' },
          weight: { type: 'string', description: 'the work argued: what makes this cluster light or heavy' },
          units: STRING_LIST,
          phenomena: {
            type: 'array',
            items: {
              type: 'object',
              properties: {
                change: { type: 'string', description: 'position, before glyph → after cell, seam change in and out, rendered reading' },
                units: STRING_LIST,
                candidate_rules: STRING_LIST,
                shape_guess: { type: 'string' },
                composes_with: STRING_LIST,
              },
              required: ['change', 'units', 'candidate_rules', 'shape_guess', 'composes_with'],
            },
          },
          brief: { type: 'string' },
        },
        required: ['key', 'title', 'weight', 'units', 'phenomena', 'brief'],
      },
    },
    already_covered: { type: 'array', items: { type: 'object', properties: { unit: { type: 'string' }, by: { type: 'string' } }, required: ['unit', 'by'] } },
    not_human: UNIT_WHY_LIST,
    held_out: UNIT_WHY_LIST,
  },
  required: ['head_before', 'tree_clean', 'dirty_paths', 'probe_dump', 'shapes_menu', 'clusters', 'already_covered', 'not_human', 'held_out'],
}

const ANALYSIS_SCHEMA = {
  type: 'object',
  properties: {
    group: { type: 'string' },
    phenomena: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          name: { type: 'string', description: 'short data-expect style name' },
          kind: { type: 'string', enum: ['extend-existing', 'new-rule', 'matcher-extension', 'already-covered'] },
          rule_ids: STRING_LIST,
          yaml: { type: 'string', description: 'new-rule: the complete rule block to append; extend-existing: the exact old and new lines; empty for already-covered' },
          matcher_change: { type: 'string', description: 'the exact standing_verdicts.py (and probe) change with its contract cases; empty when none is needed' },
          decision_record: { type: 'string' },
          units_covered: STRING_LIST,
          survey: { type: 'string', description: 'how the swath was enumerated and what the targeted run showed on the scratch copy, including fills outside the docket read against the blind spot' },
          commit_message: { type: 'string' },
          depends_on: STRING_LIST,
          order: { type: 'integer' },
        },
        required: ['name', 'kind', 'rule_ids', 'yaml', 'matcher_change', 'decision_record', 'units_covered', 'survey', 'commit_message', 'depends_on', 'order'],
      },
    },
    units_unexplained: UNIT_WHY_LIST,
    notes_for_lander: { type: 'string' },
  },
  required: ['group', 'phenomena', 'units_unexplained', 'notes_for_lander'],
}

const LAND_SCHEMA = {
  type: 'object',
  properties: {
    commits: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          sha: { type: 'string' },
          subject: { type: 'string' },
          rule_ids: STRING_LIST,
          matcher_moved: { type: 'boolean' },
          units_filled: STRING_LIST,
        },
        required: ['sha', 'subject', 'rule_ids', 'matcher_moved', 'units_filled'],
      },
    },
    skipped: NAME_WHY_LIST,
    not_landed: NAME_WHY_LIST,
    tests_run: { type: 'string' },
    tree_clean: { type: 'boolean' },
  },
  required: ['commits', 'skipped', 'not_landed', 'tests_run', 'tree_clean'],
}

const VERIFY_SCHEMA = {
  type: 'object',
  properties: {
    filled: { type: 'array', items: { type: 'object', properties: { unit: { type: 'string' }, by: { type: 'string' } }, required: ['unit', 'by'] } },
    already_verdicted: STRING_LIST,
    still_blank: UNIT_WHY_LIST,
    rollup_warnings: STRING_LIST,
    commits: STRING_LIST,
    tree_clean: { type: 'boolean' },
  },
  required: ['filled', 'already_verdicted', 'still_blank', 'rollup_warnings', 'commits', 'tree_clean'],
}

const LAUNCH_SCHEMA = {
  type: 'object',
  properties: {
    launched: { type: 'boolean' },
    pid: { type: 'integer' },
    log: { type: 'string' },
    alive: { type: 'boolean' },
    why_not: { type: 'string' },
  },
  required: ['launched', 'pid', 'log', 'alive', 'why_not'],
}

phase('Cluster')
const clusters = await agent(`${COMMON}

You are the clustering agent, read-only against the tree: edit no tracked file, commit nothing, write only under ${SCRATCH}/. Start with git status --porcelain and record git rev-parse HEAD as head_before. This workflow runs only on a clean tree, because its lander commits shared files by path and reverts a phenomenon it cannot land by file, and an uncommitted edit already sitting in one of those files would be folded into a commit or discarded: if the listing is non-empty, stop there and return tree_clean false with every listed line under dirty_paths, empty clusters and empty lists, probing nothing; otherwise tree_clean is true and dirty_paths is empty. Then rm -rf ${SCRATCH} && mkdir -p ${SCRATCH}, write uv run python rebuild/tools/standing_probe.py --shapes > ${SCRATCH}/shapes-menu.txt (the one probe form that never loads the surface, so nothing else rides on it), then make ONE probe call over every unit id: uv run python rebuild/tools/standing_probe.py ${UNITS.join(' ')} > ${SCRATCH}/probe-all.txt 2>&1. That dump is the batch's only probe until the analysts run; every later agent reads it instead of re-probing these units. Report its path as probe_dump and the menu's as shapes_menu.

For each unit, from its block in the dump (a block starts at its u-… line): list every change in the window as (position index, before glyph, after cell, seam change into and out of it, the rendered reading) that no "rule X: matches" line explains and the composed: line does not credit. A unit every change of which is already explained goes under already_covered with the rule or credit that explains it; a "not a human unit on this surface" line goes under not_human; a unit whose same-deltas tally or survey siblings show neither or reject on the stroke in question goes under held_out with why.

Cluster the distinct changes by phenomenon, as the skill's "Cluster by phenomenon" bullet says: same pivot family and form, same follower family, same seam change, same shift or trade. A window with two unexplained changes belongs to two phenomena. Every change that would extend the same checked-in rule (a pivot form, follower or cell it lacks) is one phenomenon naming that rule under candidate_rules. Phenomena that must compose in the same windows go in the same cluster, so one analyst reads the composed line on one scratch copy.

Balance the clusters by the work each costs, never by unit count (the skill's "Balance the clusters" bullet): a matcher extension or a cell-enumerating shape weighs more than a form added to an existing rule. Target ${SURFACE_HOLDERS} clusters, since that many analysts hold a surface at once; split further only when a cluster would hold more phenomena than one analyst can carry through the skill's steps 2 through 5 in one context. Each cluster's brief is the analyst's orientation: the phenomena, the specimen and unit ids per phenomenon, the candidate rules and the shape you expect, the companions in the same windows, and where to look for the decision's record. It is a hypothesis for the analyst to verify, not a finding.`, { label: 'cluster:all', phase: 'Cluster', schema: CLUSTERS_SCHEMA })

if (!clusters) return { units: UNITS, error: 'the clustering agent returned nothing' }
if (!clusters.tree_clean) return { units: UNITS, error: `the tree is not clean, and the lander commits shared files by path and reverts by file, so nothing runs until it is: ${clusters.dirty_paths.join(' ')}` }
log(`${clusters.clusters.length} clusters; ${clusters.already_covered.length} already covered, ${clusters.held_out.length} held out, ${clusters.not_human.length} not human`)

function analystPrompt(cluster) {
  return `${COMMON}

You are the analyst for cluster ${cluster.key}: ${cluster.title}. Read-only against the tree: edit no tracked file, commit nothing, write only under ${SCRATCH}/${cluster.key}/. Up to ${SURFACE_HOLDERS - 1} other analysts hold a surface beside you. The probe output for every unit in the batch is at ${clusters.probe_dump} (read your units' blocks there rather than re-probing them) and the shapes menu at ${clusters.shapes_menu}. The clustering agent recorded the units it held out or found already covered; do not re-open them.

Your units: ${cluster.units.join(' ')}

Your orientation, a hypothesis to verify against the dump and your own survey:
${cluster.brief}

Your phenomena as the clustering agent saw them:
${JSON.stringify(cluster.phenomena, null, 2)}

Method, per the skill's steps 1 through 5 for each phenomenon: name every change from the dump's rendered grain; survey the swath in ONE probe call per round (--survey, --coverage RULE_ID, --extension-cells, --retarget-cells, --find and extra unit ids all ride one load, and a flag's arguments come from a table the probe already printed); find the decision's record; pick the smallest home in the skill's order; draft the rule in the file's idiom onto a scratch copy, cp rebuild/standing-approvals.yaml ${SCRATCH}/${cluster.key}/rules.yaml and edit the copy, and prove it with the targeted run only: uv run python rebuild/tools/standing_verdicts.py verdicts-autosave.json --rules ${SCRATCH}/${cluster.key}/rules.yaml --explain RULE_ID --targeted --unit <each of the phenomenon's units> > ${SCRATCH}/${cluster.key}/dry-RULE_ID.txt 2>&1, passing the same --rules to every probe call. Read the rollup, not the own line; the explain block's split must cover the survey; probe the filled units outside the survey in one call and read each against the shape's blind spot. Drafts stack on the same scratch copy so a composed reading can be read before either lands. The whole-domain run is the lander's, not yours.

If no shape as it currently matches names the survey, do not implement the extension: describe exactly which matcher function must newly accept what, the docstring sentence to change (present tense, no narration of before and after), the positive, negative and, if the shape composes, composed-walk contract cases for rebuild/test_standing_verdicts.py in that file's idiom, and the probe change if the shape enumerates cells; still write the rule YAML that will use it, and say in survey that it is unproven pending the extension.

Return one phenomenon per rule to write or extend. yaml is paste-ready and proofread against the file's indentation (rules sit under a two-space "  - id:" list); for extend-existing give the exact old and new lines. depends_on lists, by phenomenon name from this batch or by rule id (existing or drafted, from any cluster), what must land before this rule's dry run reads right: a rule it composes with, or the matcher extension it needs. commit_message is the subject in the repository idiom, "Stop having to verdict ·X ~b~ ·Y" (braces for a family list: ·{Bay,Day} ~b~ ·Gay), sentence case, how the letters look different and never the mechanism, with an optional one-sentence body.`
}

async function capped(cap, thunks) {
  const results = new Array(thunks.length).fill(null)
  let next = 0
  async function worker() {
    while (next < thunks.length) {
      const index = next++
      try {
        results[index] = await thunks[index]()
      } catch {
        results[index] = null
      }
    }
  }
  await Promise.all(Array.from({ length: Math.min(cap, thunks.length) }, worker))
  return results
}

phase('Analyze')
const analysisResults = await capped(SURFACE_HOLDERS, clusters.clusters.map(cluster => () =>
  agent(analystPrompt(cluster), { label: `analyze:${cluster.key}`, phase: 'Analyze', model: 'opus', schema: ANALYSIS_SCHEMA })))
const analyses = analysisResults.filter(Boolean)
const missing = clusters.clusters.filter((cluster, index) => !analysisResults[index]).map(cluster => cluster.key)
log(`analyses back for ${analyses.map(analysis => analysis.group).join(', ') || 'no cluster'}${missing.length ? `; nothing back for ${missing.join(', ')}` : ''}`)

function landingOrder(analyses) {
  const all = analyses.flatMap((analysis, groupIndex) => [...analysis.phenomena]
    .sort((a, b) => a.order - b.order)
    .map(phenomenon => ({ ...phenomenon, group: analysis.group, groupIndex })))
  const pending = all.filter(phenomenon => phenomenon.kind !== 'already-covered')
  const byName = new Map()
  const byRule = new Map()
  for (const phenomenon of pending) {
    if (!byName.has(phenomenon.name)) byName.set(phenomenon.name, phenomenon)
    for (const id of phenomenon.rule_ids) if (!byRule.has(id)) byRule.set(id, phenomenon)
  }
  const placed = new Set()
  const ordered = []
  let remaining = pending
  while (remaining.length) {
    const ready = remaining.filter(phenomenon => phenomenon.depends_on.every(dep => {
      const target = byName.get(dep) || byRule.get(dep)
      return !target || target === phenomenon || placed.has(target)
    }))
    if (!ready.length) log(`dependency cycle at ${remaining[0].name}; landing it in analyst order`)
    const wave = ready.length ? ready : [remaining[0]]
    for (const phenomenon of wave) {
      ordered.push(phenomenon)
      placed.add(phenomenon)
    }
    remaining = remaining.filter(phenomenon => !placed.has(phenomenon))
  }
  return ordered
}

const ordered = landingOrder(analyses)
log(ordered.length ? `landing order: ${ordered.map(phenomenon => `${phenomenon.group}: ${phenomenon.name}`).join(' → ')}` : 'nothing to land')

let landing = null
let verify = null
let launch = null

if (ordered.length) {
  phase('Land')
  landing = await agent(`${COMMON}

You are the lander and you run alone: no other agent touches the tree or holds a surface while you work, so you may edit tracked files and commit on the current branch. You are the only agent in this workflow that commits, and running this workflow was the user's go-ahead for these commits. Do not push, do not branch, no worktree, never --amend, never rewrite history. Scratch goes under ${SCRATCH}/land/. The probe dump for every unit is at ${clusters.probe_dump}.

Before anything else, git status --porcelain must print nothing. The clustering agent found the tree clean and every step below rests on that: the files a phenomenon touches hold no uncommitted edit but that phenomenon's, so a revert by file and a commit by path each reach exactly that and nothing else. If anything is listed, land nothing, put every phenomenon under not_landed with the listing as its why, and stop.

The clustering report:
${JSON.stringify({ head_before: clusters.head_before, already_covered: clusters.already_covered, held_out: clusters.held_out, not_human: clusters.not_human }, null, 2)}

What the analysts left for you:
${JSON.stringify(analyses.map(analysis => ({ group: analysis.group, notes_for_lander: analysis.notes_for_lander, units_unexplained: analysis.units_unexplained })), null, 2)}

Land the phenomena below in the order given, which is dependency order across every cluster: a rule another composes with, and a matcher extension, come before what needs them. For each, in order:
(1) grep -c its units_covered in the newest fill file under ${SCRATCH}/land/; a phenomenon whose every unit an earlier commit already filled, or that the analysts list as already verdicted, is skipped and reported, never re-landed; two analysts drafting the same extension to one rule is one landing.
(2) Apply yaml verbatim: append a new rule at the end of rebuild/standing-approvals.yaml, or edit the existing rule in place. When matcher_change is non-empty, implement it in rebuild/tools/standing_verdicts.py (and rebuild/tools/standing_probe.py when the shape enumerates cells) exactly as described, docstring in present tense with no narration of what changed, the contract cases in rebuild/test_standing_verdicts.py in the file's idiom, then make prettier (pyright runs from the PostToolUse hook; fix what it reports). No code comments beyond the surrounding density.
(3) ONE whole-domain dry run: uv run python rebuild/tools/standing_verdicts.py verdicts-autosave.json --out ${SCRATCH}/land/dry-<n>.json --explain RULE_ID > ${SCRATCH}/land/dry-<n>.txt 2>&1; read only that rule's lines out of the file (its own line, the composed lines crediting it, its rollup, its tripwire, the explain block, and every WARNING line) with grep -n, never cat. Every one of the phenomenon's units must be filled, already verdicted, or held only by except_left; a rule reaching nothing may never be committed. When the fill list differs from the analyst's survey, probe the difference in one call and read it against the shape's blind spot. A rollup short of the survey: fix from the analyst's report with at most a couple of re-runs; if it still cannot honestly fill, git checkout -- <the files you edited for this phenomenon> (the tree was clean when you started and every earlier phenomenon is committed, so nothing else in them is uncommitted) and report it under not_landed.
(4) ONE targeted test run: ${TARGETED_TESTS} > ${SCRATCH}/land/tests-<n>.txt 2>&1, and read its tail; green is required whether or not the matcher moved.
(5) Commit only the files this phenomenon changed, by path (git add rebuild/standing-approvals.yaml plus, when the matcher moved, rebuild/tools/standing_verdicts.py rebuild/tools/standing_probe.py rebuild/test_standing_verdicts.py rebuild/test_standing_probe.py; never git add -A, never tmp/, var/, verdicts-*.json or rebuild/review-census-pins.json), subject from commit_message in the reader-terms idiom (sentence case, data-expect notation, how the letters look different, never the mechanism), an optional short body, no attribution or trailer lines of any kind. The census pins are the cycle's to rewrite after every commit here and the orchestrator's to commit with the rule that moved them or as their own commit; never touch them.

When done, git status --porcelain must show nothing (tmp/ is ignored). Report every commit with its sha.

The phenomena, in landing order:
${JSON.stringify(ordered.map(({ groupIndex, ...phenomenon }) => phenomenon), null, 2)}`, { label: 'land:all', phase: 'Land', schema: LAND_SCHEMA })
  log(landing ? `${landing.commits.length} commits landed, ${landing.skipped.length} skipped, ${landing.not_landed.length} not landed` : 'the lander returned nothing; the verifier lists its commits from git')

  phase('Verify')
  verify = await agent(`${COMMON}

You are the final verifier: read-only, commit nothing, write only under ${SCRATCH}/verify/. The commits landed are git log --oneline ${clusters.head_before}..HEAD; list them under commits.${landing ? '' : ' The lander returned no report, so that log is the only record of what it committed.'}

Run, one after the other and never together: one whole-domain dry run over the live rules, uv run python rebuild/tools/standing_verdicts.py verdicts-autosave.json --out ${SCRATCH}/verify/dry.json > ${SCRATCH}/verify/dry.txt 2>&1; then one probe over every input unit in one call, uv run python rebuild/tools/standing_probe.py ${UNITS.join(' ')} > ${SCRATCH}/verify/probe.txt 2>&1. For each input unit say whether it now fills, by which rule's own line or which composed credit (its "rule X: matches" and composed: lines, and its presence in the fill file), was already verdicted, is not a human unit, or stays blank and why. List every WARNING line of the dry run (a rule reaching nothing, a matched unit verdicted outside approve/either, an except_left family no window joins from). git status --porcelain must be empty; report tree_clean from it.

The input units: ${UNITS.join(' ')}

The lander's report:
${JSON.stringify(landing, null, 2)}`, { label: 'verify:all', phase: 'Verify', schema: VERIFY_SCHEMA })
  log(verify ? `verified: ${verify.filled.length} filled, ${verify.already_verdicted.length} already verdicted, ${verify.still_blank.length} still blank, ${verify.rollup_warnings.length} warnings` : 'the verifier returned nothing')

  const committed = (landing && landing.commits.length) || (verify && verify.commits.length)
  if (committed && verify && verify.tree_clean) {
    phase('Launch')
    launch = await agent(`${COMMON}

Every rule of this batch is committed and no agent holds the surface any longer. Read doc/running-long-steps.md and the skill's steps 6(b) and 7. Check that git status --porcelain is empty and that no gate, cycle or surface-loading process is running (pgrep -f 'standing_verdict[s]|standing_prob[e]|artifact_cycl[e]|rebuild_gat[e]|run_m[1]', bracketed so the watcher cannot match itself); refuse with why_not if either check fails. Then mkdir -p tmp and launch exactly this line: ${CHAIN}
Capture $! as the pid, wait a few seconds, and confirm kill -0 <pid>. Return the pid, the log path tmp/cycle-pass.log, and whether it is alive. Do not wait for it.`, { label: 'launch:chain', phase: 'Launch', effort: 'low', schema: LAUNCH_SCHEMA })
    log(launch && launch.launched ? `chain launched, pid ${launch.pid}` : `chain not launched: ${launch ? launch.why_not : 'the launcher returned nothing'}`)
  } else {
    log(`chain not launched: ${committed ? 'the verifier did not report a clean tree' : 'no commit landed'}`)
  }
}

const watch = launch && launch.launched ? [
  `kill -0 ${launch.pid} says whether the chain still runs; judge liveness by the pid, never by grepping process names`,
  'tmp/cycle-pass.log ends with rc=<n>; rc=0 is the only green, and a red make test-rebuild stops the cycle from starting',
  'var/build-logs/latest/ holds the cycle half: terminal.log to the Cycle complete. banner, one <nn>-<step>.log per step for a red one',
  'when the pass prints its rebuild/review-census-pins.json diff, read the invariant block and commit that diff as its own commit the moment it lands; never leave it in the tree',
  'make verdict-ready must read READY; if it reads NOT READY, name the unmet clause and clear it',
] : [
  `no chain launched: ${launch ? launch.why_not : ordered.length ? 'nothing committed or the tree is not clean' : 'nothing landed'}; once commits exist on a clean tree, launch it by hand as the skill's step 6(b) says: ${CHAIN}`,
]

return {
  units: UNITS,
  clusters,
  analyses,
  landing_order: ordered.map(phenomenon => `${phenomenon.group}: ${phenomenon.name}`),
  landing,
  verify,
  launch,
  watch,
}
