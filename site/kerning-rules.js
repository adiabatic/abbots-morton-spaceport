// Writes and reads back the kern rules for a pair with per-junction overrides and no alt-axis quadrants.
//
// A side of an override is a stance prefix, or null for the whole family. Two prefixes are nested (qsGay.ex-y0 holds qsGay.ex-y0.ex-ext-1) or disjoint, so the prefixes of a pair form a tree under the family. Each tree node stands for its own glyphs: the ones no deeper prefix claims. A glyph pair takes the value of the most specific override that matches it, comparing the left prefix first and then the right, or the cell value when none matches. The rules written for a pair are disjoint, so the separate lookups the build makes of them never add up on one glyph pair.

// The lines for one side of a kern rule: `left:`/`right:` for punctuation, `*_stance` for a stance prefix, and `*_family` for a family, either of the last two followed by an `except_*` carve-out when the side has one.
export function selectorLines(side, which) {
  if (side.glyph) return [`${which}: [${side.family}]`];
  const lines = [side.stance ? `${which}_stance: [${side.stance}]` : `${which}_family: [${side.family}]`];
  if (side.except && side.except.length) lines.push(`except_${which}: [${side.except.join(", ")}]`);
  return lines;
}

export function ruleBody(left, right, value) {
  return [...selectorLines(left, "left"), ...selectorLines(right, "right"), `value: ${value}`].join("\n");
}

function within(node, ancestor) {
  if (ancestor === null) return true;
  return node !== null && (node === ancestor || node.startsWith(`${ancestor}.`));
}

function depth(node) {
  return node === null ? -1 : node.length;
}

function specificity(a, b) {
  return depth(a.left) - depth(b.left) || depth(a.right) - depth(b.right);
}

function prefixTree(nodes) {
  const sorted = [...new Set(nodes)].filter((node) => node !== null).sort();
  const children = new Map([[null, []], ...sorted.map((node) => [node, []])]);
  for (const node of sorted) {
    let parent = null;
    for (const other of sorted) {
      if (other !== node && within(node, other) && depth(other) > depth(parent)) parent = other;
    }
    children.get(parent).push(node);
  }
  return { nodes: [null, ...sorted], children };
}

function winner(regions, left, right) {
  let best = null;
  for (const region of regions) {
    if (within(left, region.left) && within(right, region.right) && (!best || specificity(region, best) >= 0)) {
      best = region;
    }
  }
  return best;
}

// Covers a set of tree nodes under `top` with selectors, each a prefix (null for the family) minus the topmost prefixes beneath it that are outside the set.
function selectors(top, members, children) {
  const out = [];
  const visit = (node) => {
    if (!members.has(node)) {
      for (const child of children.get(node)) visit(child);
      return;
    }
    const except = [];
    const carve = (parent) => {
      for (const child of children.get(parent)) {
        if (members.has(child)) {
          carve(child);
        } else {
          except.push(child);
          visit(child);
        }
      }
    };
    carve(node);
    out.push({ node, except });
  };
  visit(top);
  return out;
}

// The value a junction (`left`, `right`, prefixes or null) takes when it has no override of its own: the value of the most specific override that holds it, or the cell value.
export function inheritedValue(cellValue, overrides, left, right) {
  return winner(overrides, left, right)?.value ?? cellValue;
}

// Whether the override at (`left`, `right`) among a pair's `overrides` can be dropped: whether every glyph pair keeps its value without it. An override can decide glyph pairs deeper than its own junction, which a shallower-left, deeper-right override takes over once it is gone, so every node pair of the pair's prefix trees is compared.
export function overrideIsRedundant(cellValue, overrides, left, right) {
  const others = overrides.filter((region) => region.left !== left || region.right !== right);
  const lefts = prefixTree(overrides.map((region) => region.left));
  const rights = prefixTree(overrides.map((region) => region.right));
  return lefts.nodes.every((l) =>
    rights.nodes.every((r) => inheritedValue(cellValue, overrides, l, r) === inheritedValue(cellValue, others, l, r)),
  );
}

// The rule bodies for one pair: the cell value on every glyph pair no override decides, then each override (`{left, right, value}`, sides as prefixes or null) on the glyph pairs it decides. A zero override gets explicit `value: 0` rules, which kern nothing but let reconstructOverrides read the override back.
export function partitionPair(leftRoot, rightRoot, cellValue, overrides) {
  const regions = [...overrides];
  const lefts = prefixTree(regions.map((region) => region.left));
  const rights = prefixTree(regions.map((region) => region.right));
  const side = (root, node, except) => (node === null ? { ...root, except } : { family: root.family, stance: node, except });
  const cell = { left: null, right: null, value: cellValue };
  const bodies = [];
  for (const region of [cell, ...regions]) {
    if (region === cell && region.value === 0) continue;
    const groups = new Map();
    for (const left of lefts.nodes) {
      if (!within(left, region.left)) continue;
      const won = rights.nodes.filter(
        (right) => within(right, region.right) && (winner(regions, left, right) ?? cell) === region,
      );
      if (!won.length) continue;
      const rightSelectors = selectors(region.right, new Set(won), rights.children);
      const groupKey = JSON.stringify(rightSelectors);
      if (!groups.has(groupKey)) groups.set(groupKey, { rightSelectors, members: new Set() });
      groups.get(groupKey).members.add(left);
    }
    for (const { rightSelectors, members } of groups.values()) {
      for (const l of selectors(region.left, members, lefts.children)) {
        for (const r of rightSelectors) {
          bodies.push(ruleBody(side(leftRoot, l.node, l.except), side(rightRoot, r.node, r.except), region.value));
        }
      }
    }
  }
  return bodies;
}

// Reads a pair's rules (`{left, right, value}`, each side `{node, except}`) back into overrides. An `except_*` prefix outside the side's family, as in a rule written for several families at once, holds none of this pair's glyphs, so it is left out. Each glyph pair takes the value of the most specific rule that covers it, or the cell value. The overrides are the fewest that reproduce those values, plus every rule without a carve-out, which is an explicit override.
export function reconstructOverrides(leftFamily, rightFamily, cellValue, pairRules) {
  const own = (family, sel) => ({ node: sel.node, except: sel.except.filter((prefix) => within(prefix, family)) });
  const rules = pairRules.map((rule) => ({ ...rule, left: own(leftFamily, rule.left), right: own(rightFamily, rule.right) }));
  const nodesOf = (rule) => ({ left: rule.left.node, right: rule.right.node });
  const lefts = prefixTree(rules.flatMap((rule) => [rule.left.node, ...rule.left.except]));
  const rights = prefixTree(rules.flatMap((rule) => [rule.right.node, ...rule.right.except]));
  const covers = (sel, node) => within(node, sel.node) && !sel.except.some((prefix) => within(node, prefix));
  const target = (left, right) => {
    let best = null;
    for (const rule of rules) {
      if (covers(rule.left, left) && covers(rule.right, right) && (!best || specificity(nodesOf(rule), nodesOf(best)) >= 0)) {
        best = rule;
      }
    }
    return best ? best.value : cellValue;
  };
  const explicit = new Set(
    rules
      .filter((rule) => !rule.left.except.length && !rule.right.except.length)
      .map((rule) => `${rule.left.node}|${rule.right.node}`),
  );
  const candidates = lefts.nodes.flatMap((left) => rights.nodes.map((right) => ({ left, right }))).sort(specificity);
  const chosen = [];
  for (const candidate of candidates) {
    const value = target(candidate.left, candidate.right);
    const inherited = winner(chosen, candidate.left, candidate.right)?.value ?? cellValue;
    if (value !== inherited || explicit.has(`${candidate.left}|${candidate.right}`)) chosen.push({ ...candidate, value });
  }
  return chosen;
}
