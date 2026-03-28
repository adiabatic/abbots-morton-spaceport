from dataclasses import dataclass, field


Anchor = tuple[int, int]


@dataclass(frozen=True)
class CompiledGlyphMeta:
    name: str
    base_name: str
    family: str | None
    sequence: tuple[str, ...]
    traits: frozenset[str]
    modifiers: frozenset[str]
    compat_assertions: frozenset[str]
    entry: tuple[Anchor, ...]
    entry_curs_only: tuple[Anchor, ...]
    exit: tuple[Anchor, ...]
    after: tuple[str, ...]
    before: tuple[str, ...]
    not_after: tuple[str, ...]
    not_before: tuple[str, ...]
    reverse_upgrade_from: tuple[str, ...]
    preferred_over: tuple[str, ...]
    word_final: bool
    is_contextual: bool
    is_entry_variant: bool
    is_exit_variant: bool
    entry_suffix: str | None
    exit_suffix: str | None
    extended_entry_suffix: str | None
    extended_exit_suffix: str | None
    entry_restriction_y: int | None
    is_noentry: bool

    @property
    def entry_ys(self) -> tuple[int, ...]:
        return tuple(anchor[1] for anchor in self.entry)

    @property
    def all_entry_ys(self) -> tuple[int, ...]:
        return tuple(anchor[1] for anchor in (*self.entry, *self.entry_curs_only))

    @property
    def exit_ys(self) -> tuple[int, ...]:
        return tuple(anchor[1] for anchor in self.exit)


@dataclass
class CaltPlan:
    glyph_meta: dict[str, CompiledGlyphMeta]
    bk_replacements: dict[str, dict[int, str]] = field(default_factory=dict)
    bk_exclusions: dict[str, dict[int, list[str]]] = field(default_factory=dict)
    pair_overrides: dict[str, list[tuple[str, list[str]]]] = field(default_factory=dict)
    fwd_upgrades: dict[str, list[tuple[str, str, int, list[str]]]] = field(default_factory=dict)
    fwd_replacements: dict[str, dict[int, str]] = field(default_factory=dict)
    fwd_exclusions: dict[str, dict[int, list[str]]] = field(default_factory=dict)
    fwd_pair_overrides: dict[str, list[tuple[str, list[str], list[str]]]] = field(default_factory=dict)
    reverse_only_upgrades: list[tuple[str, list[str], list[int], list[str]]] = field(default_factory=list)
    terminal_entry_only: set[str] = field(default_factory=set)
    terminal_exit_only: set[str] = field(default_factory=set)
    exit_classes: dict[int, set[str]] = field(default_factory=dict)
    entry_classes: dict[int, set[str]] = field(default_factory=dict)
    entry_exclusive: dict[int, set[str]] = field(default_factory=dict)
    fwd_use_exclusive: set[tuple[str, int]] = field(default_factory=set)
    fwd_preferred_lookahead: dict[str, list[tuple[str, int, int]]] = field(default_factory=dict)
    sorted_bases: list[str] = field(default_factory=list)
    cycle_bases: set[str] = field(default_factory=set)
    edges: dict[str, set[str]] = field(default_factory=dict)
    pair_only: list[str] = field(default_factory=list)
    all_bk_bases: list[str] = field(default_factory=list)
    all_fwd_bases: set[str] = field(default_factory=set)
    fwd_only: list[str] = field(default_factory=list)
    lig_fwd_bases: set[str] = field(default_factory=set)
    early_pair_upgrade_bases: set[str] = field(default_factory=set)
    early_fwd_pairs: set[str] = field(default_factory=set)
    ligatures: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
