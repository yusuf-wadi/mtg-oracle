"""Deck radar — score a deck across the 105 MTG axis primitives.

v0 implementation: hand-curated regex/keyword patterns per axis, applied to
each card's oracle text + type line, weighted by deck quantity. The point is
to ship a visible dashboard mode quickly; the embedding-based version
(BAAI/bge-small-en-v1.5 over axis seeds) is a planned upgrade.

The patterns are kept in this module rather than in mtg-axes.json because they
are an implementation detail — the canonical axis definitions in data/mtg-axes.json
should stay clean rules-vocabulary seeds for the embedding work.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable

_ROOT = Path(__file__).resolve().parent.parent
_AXES_PATH = _ROOT / "data" / "mtg-axes.json"


# Family display labels. Keys must match families in mtg-axes.json.
FAMILY_LABELS = {
    "zones": "Zones",
    "subtypes_qualifiers": "Subtypes",
    "casting_and_costs": "Casting & costs",
    "actions_offense": "Offense / combat",
    "actions_disruption": "Disruption",
    "actions_advantage": "Card advantage",
    "triggers_state": "Triggers & state",
    "counters_and_modifications": "Counters & mods",
    "tokens_and_creation": "Tokens & creation",
    "static_keywords": "Static keywords",
    "commander_specific": "Commander",
    "broader_concepts": "Game concepts",
}


# Per-axis match patterns. Each axis gets a list of regex patterns (case-
# insensitive). A card's contribution to that axis is the number of distinct
# patterns it matches (0 or 1 typically) times its quantity. Patterns are
# anchored with word boundaries where it matters.
#
# Type-line cues are accessed via the "TYPELINE:" prefix — the scorer routes
# those to the type_line string instead of oracle text.
AXIS_PATTERNS: dict[str, list[str]] = {
    # ---- zones ----
    "graveyard":   [r"\bgraveyard\b", r"\bfrom (?:a|your|target)? ?graveyard\b"],
    "library":     [r"\blibrary\b", r"\bsearch your library\b", r"\btop of your library\b"],
    "hand":        [r"\binto (?:your|their) hand\b", r"\bfrom (?:your|a) hand\b"],
    "exile":       [r"\bexile\b"],
    "stack":       [r"\bon the stack\b", r"\bwhile .* is on the stack\b"],
    "command":     [r"\bcommand zone\b", r"\bcommander\b"],

    # ---- subtypes / qualifiers ----
    "legendary":         [r"TYPELINE:\blegendary\b"],
    "equipment":         [r"TYPELINE:\bequipment\b", r"\bequip\b", r"\battached creature\b"],
    "aura":              [r"TYPELINE:\baura\b", r"\benchant creature\b", r"\benchanted creature\b"],
    "vehicle":           [r"TYPELINE:\bvehicle\b", r"\bcrew\b"],
    "tribal_creature":   [r"\b(?:elf|elves|goblin|dragon|zombie|merfolk|wizard|human|vampire|knight|soldier|angel|demon|sliver|spirit|cat) (?:creatures? )?(?:you control|you own)\b"],
    "snow":              [r"TYPELINE:\bsnow\b", r"\bsnow mana\b", r"\bsnow permanent\b"],

    # ---- casting & costs ----
    "additional_cost":  [r"\bas an additional cost\b", r"\bpay \{[a-z0-9/]+\} (?:more|in addition)\b"],
    "alternative_cost": [r"\brather than pay\b", r"\bwithout paying its mana cost\b", r"\bmadness\b", r"\bevoke\b", r"\bforetell\b", r"\bemerge\b"],
    "x_cost":           [r"\{x\}", r" with X in its mana cost\b"],
    "life_payment":     [r"\bpay \d+ life\b", r"\bpays? \d+ life\b", r"\blose \d+ life as you cast\b"],
    "mana_ability":     [r"\badd (?:one|two|three|four|five|\{[wubrgc0-9/]+\})\b", r"\badd .* mana of any\b"],
    "kicker":           [r"\bkicker\b", r"\bif it was kicked\b", r"\bmultikicker\b"],
    "flashback":        [r"\bflashback\b"],
    "cascade":          [r"\bcascade\b"],
    "convoke":          [r"\bconvoke\b"],
    "delve":            [r"\bdelve\b"],
    "cycling":          [r"\bcycling\b", r"\bcycle\b"],

    # ---- offense / combat ----
    "attack":         [r"\battacks?\b", r"\bdeclare attackers?\b", r"\bwhenever .* attacks\b"],
    "block":          [r"\bblocks?\b", r"\bdeclare blockers?\b", r"\bwhenever .* blocks\b"],
    "combat_damage":  [r"\bcombat damage\b"],
    "deal_damage":    [r"\bdeals? \d+ damage\b", r"\bdeals? damage to\b"],
    "first_strike":   [r"\bfirst strike\b"],
    "double_strike":  [r"\bdouble strike\b"],
    "trample":        [r"\btrample\b"],
    "menace":         [r"\bmenace\b"],
    "flying":         [r"\bflying\b"],
    "reach":          [r"\breach\b"],
    "haste":          [r"\bhaste\b"],
    "vigilance":      [r"\bvigilance\b"],
    "deathtouch":     [r"\bdeathtouch\b"],
    "lifelink":       [r"\blifelink\b"],
    "indestructible": [r"\bindestructible\b"],
    "hexproof":       [r"\bhexproof\b"],
    "shroud":         [r"\bshroud\b"],
    "ward":           [r"\bward\b"],
    "protection":     [r"\bprotection from\b"],
    "extra_combat":   [r"\badditional combat phase\b", r"\bextra combat phase\b", r"\buntap all creatures\b"],
    "prowess":        [r"\bprowess\b"],
    "landfall":       [r"\blandfall\b", r"\bwhenever a land enters the battlefield\b"],

    # ---- disruption ----
    "destroy":           [r"\bdestroy target\b", r"\bdestroy all\b", r"\bdestroy each\b"],
    "exile_action":      [r"\bexile target\b", r"\bexile all\b", r"\bexile each\b", r"\bexile that\b"],
    "counter_spell":     [r"\bcounter target\b"],
    "bounce":            [r"\breturn target .* to (?:its|their) owner['’]s hand\b", r"\bbounce\b"],
    "discard":           [r"\bdiscard (?:a|two|three|your)\b", r"\bdiscards? a card\b"],
    "mill":              [r"\bmill\b", r"\bputs? the top .* cards? of .* library into .* graveyard\b"],
    "sacrifice":         [r"\bsacrifices? a\b", r"\bsacrifice .* creature\b", r"\bas an additional cost .* sacrifice\b"],
    "damage_prevention": [r"\bprevent (?:all|the next|that) damage\b", r"\bwould be dealt .* prevent\b"],
    "tap_opponent":      [r"\btap target\b", r"\bdoes not untap\b", r"\bdon't untap\b"],
    "goad":              [r"\bgoad\b"],

    # ---- card advantage ----
    "draw_card":           [r"\bdraws? a card\b", r"\bdraws? \w+ cards?\b"],
    "tutor":               [r"\bsearch your library for\b"],
    "scry":                [r"\bscry \d+\b"],
    "surveil":             [r"\bsurveil \d+\b"],
    "return_to_hand":      [r"\breturn .* to (?:its|their) owner['’]s hand\b", r"\breturn .* from your graveyard to your hand\b"],
    "return_to_battlefield":[r"\breturn .* from (?:your|a) graveyard to the battlefield\b", r"\breanimate\b"],
    "ramp":                [r"\bsearch your library for .* land\b", r"\bput .* land card .* onto the battlefield\b", r"\badd .* mana\b"],
    "treasure":            [r"\btreasure token\b"],
    "dredge":              [r"\bdredge \d+\b"],
    "storm":               [r"\bstorm\b"],

    # ---- triggers & state ----
    "enters_battlefield":  [r"\bwhen(?:ever)? .* enters the battlefield\b", r"\benters the battlefield\b"],
    "leaves_battlefield":  [r"\bwhen(?:ever)? .* leaves the battlefield\b", r"\bleaves the battlefield\b"],
    "dies":                [r"\bwhen(?:ever)? .* dies\b", r"\bwhen this creature dies\b"],
    "attacks_trigger":     [r"\bwhen(?:ever)? .* attacks\b"],
    "blocks_trigger":      [r"\bwhen(?:ever)? .* blocks\b"],
    "cast_trigger":        [r"\bwhen(?:ever)? you cast\b", r"\bwhen(?:ever)? a player casts\b"],
    "upkeep_trigger":      [r"\bat the beginning of (?:your|each) upkeep\b"],
    "end_step_trigger":    [r"\bat the beginning of (?:your|the|each) end step\b", r"\bat the beginning of the next end step\b"],
    "control_change":      [r"\bgain control of\b", r"\bexchange control\b"],

    # ---- counters & modifications ----
    "plus_one_counter":     [r"\+1/\+1 counter", r"\bplus one .* counter\b"],
    "minus_one_counter":    [r"-1/-1 counter"],
    "loyalty_counter":      [r"\bloyalty counter\b", r"TYPELINE:\bplaneswalker\b"],
    "charge_counter":       [r"\bcharge counter\b"],
    "experience_counter":   [r"\bexperience counter\b"],
    "energy":               [r"\benergy counter\b", r"\{e\}"],
    "proliferate":          [r"\bproliferate\b"],
    "power_toughness_buff": [r"\bgets \+\d+/\+\d+\b", r"\bget \+\d+/\+\d+\b", r"\banthem\b"],

    # ---- tokens & creation ----
    "create_token":  [r"\bcreate (?:a|an|\w+) .* token\b", r"\bcreates? .* token\b"],
    "copy_spell":    [r"\bcopy target\b", r"\bcopy that spell\b", r"\bcreate a token that's a copy\b"],
    "food":          [r"\bfood token\b"],
    "clue":          [r"\bclue token\b"],
    "blood":         [r"\bblood token\b"],

    # ---- static keywords ----
    "flash":         [r"\bflash\b"],
    "defender":      [r"\bdefender\b"],
    "phasing":       [r"\bphasing\b", r"\bphases? out\b", r"\bphases? in\b"],
    "morph":         [r"\bmorph\b", r"\bface-down\b"],
    "modal_dfc":     [r"\bmodal double-faced\b"],
    "saga":          [r"TYPELINE:\bsaga\b", r"\blore counter\b"],
    "adventure":     [r"\badventure\b"],
    "transform":     [r"\btransform\b"],

    # ---- commander-specific ----
    "commander_cast":   [r"\bcommander\b", r"\bfrom the command zone\b"],
    "partner":          [r"\bpartner\b"],
    "monarch":          [r"\bbecome the monarch\b", r"\byou are the monarch\b", r"\bthe monarch\b"],
    "initiative":       [r"\binitiative\b"],
    "venture_dungeon":  [r"\bventure into the dungeon\b"],

    # ---- broader concepts ----
    "target_any":          [r"\btarget creature\b", r"\btarget player\b", r"\btarget permanent\b", r"\btarget spell\b"],
    "untargeted":          [r"\bnon-?targeted\b", r"\beach creature\b", r"\beach player\b", r"\beach opponent\b"],
    "may_choice":          [r"\byou may\b"],
    "replacement_effect":  [r"\binstead\b", r"\bif .* would .*, .* instead\b"],
    "devotion":            [r"\bdevotion\b"],
}


def _compile_patterns(raw: dict[str, list[str]]) -> dict[str, list[tuple[bool, re.Pattern]]]:
    out: dict[str, list[tuple[bool, re.Pattern]]] = {}
    for axis_id, pats in raw.items():
        compiled: list[tuple[bool, re.Pattern]] = []
        for p in pats:
            if p.startswith("TYPELINE:"):
                compiled.append((True, re.compile(p[len("TYPELINE:"):], re.IGNORECASE)))
            else:
                compiled.append((False, re.compile(p, re.IGNORECASE)))
        out[axis_id] = compiled
    return out


_COMPILED = _compile_patterns(AXIS_PATTERNS)


_AXES_CACHE: dict | None = None


def load_axes() -> dict:
    global _AXES_CACHE
    if _AXES_CACHE is None:
        with open(_AXES_PATH, encoding="utf-8") as f:
            _AXES_CACHE = json.load(f)
    return _AXES_CACHE


def families() -> list[dict]:
    """Public family + axis structure for the API response."""
    axes = load_axes()
    out = []
    for fam_id, fam_axes in axes.items():
        if fam_id.startswith("_"):
            continue
        out.append({
            "id": fam_id,
            "label": FAMILY_LABELS.get(fam_id, fam_id),
            "axes": [{"id": a["id"]} for a in fam_axes],
        })
    return out


def axis_to_family() -> dict[str, str]:
    axes = load_axes()
    return {
        a["id"]: fam
        for fam, items in axes.items()
        if not fam.startswith("_")
        for a in items
    }


def score_deck(corpus: Iterable[dict]) -> tuple[dict[str, float], dict[str, float], dict[str, int], int]:
    """Score a deck across the 105 axes.

    corpus: iterable of {name, type_line, oracle_text, quantity}.
    Returns (axis_scores, family_scores, axis_match_counts, total_cards).

    - axis_scores: sum of quantities for cards matching the axis (signal strength)
    - family_scores: sum of axis scores per family
    - axis_match_counts: distinct-card count per axis (breadth of signal,
      used by the dashboard to tie-break the per-family representative)
    """
    axis_scores: dict[str, float] = {axis_id: 0.0 for axis_id in AXIS_PATTERNS}
    axis_match_counts: dict[str, int] = {axis_id: 0 for axis_id in AXIS_PATTERNS}
    total_cards = 0
    a2f = axis_to_family()

    for entry in corpus:
        qty = int(entry.get("quantity") or 1)
        oracle = (entry.get("oracle_text") or "").lower()
        type_line = (entry.get("type_line") or "").lower()
        total_cards += qty
        for axis_id, compiled in _COMPILED.items():
            hit = False
            for is_typeline, pat in compiled:
                target = type_line if is_typeline else oracle
                if pat.search(target):
                    hit = True
                    break
            if hit:
                axis_scores[axis_id] += qty
                axis_match_counts[axis_id] += 1

    family_scores: dict[str, float] = {}
    for axis_id, score in axis_scores.items():
        fam = a2f.get(axis_id)
        if fam is None:
            continue
        family_scores[fam] = family_scores.get(fam, 0.0) + score

    return axis_scores, family_scores, axis_match_counts, total_cards
