# Agents.md — Arcs Analysis Project

Context for AI agents working on this codebase. Read this before touching anything.

---

## What this project does

`arcs_analyze.py` ingests Arcs board game replay HTML files (from Boardgame Arena or HRF format) and outputs a self-contained `_analysis.html` with charts, timelines, and per-player breakdowns. Run it as:

```
python3 arcs_analyze.py <replay.html>
```

Outputs `<replay>_analysis.html` next to the input.

---

## Player identities (this group)

- **Geekus** = Patrick (the repo owner — "me" in conversations)
- **Scrotum / Scrot / Jefe** = Jeff (same human, different display names across games)
- **Schvimvs** = third player (handle only, real name unknown)

In logs, players appear as colors: `Blue`, `Red`, `White`, `Yellow`. The lobby maps color → handle. `fa()` converts color to single-letter abbreviation (B/R/W/Y).

---

## Replay file format

Files are HTML with two key `<div>` sections:

### `id="lobby"`
Contains game metadata in a line-oriented format:
```
title  game-name
version  0.8.153
name player-B Geekus
name player-R Schvimvs
name player-W Scrotum
seating B R W
options ...
```
Parsed by `parse_lobby()`.

### `id="replay"`
One log action per line. Parsed by `parse_replay_log()` into a list of strings.

---

## Critical log format knowledge

### ActionCard — 3 params, not 2
```
ActionCard(Suit, CardNumber, PipCount)
```
**The pip count is the THIRD parameter.** Card number (2–6) is second. Easy to write a regex that captures card number instead of pip count — use `ActionCard\([^,]+,\s*\d+,\s*(\d+)` to skip card number and grab pip count. This bug has already been fixed in the codebase; don't reintroduce it.

### Card pip matrix
| Suit | 2 (Tycoon) | 3 (Tyrant) | 4 (Warlord) | 5 (Keeper) | 6 (Empath) |
|---|---|---|---|---|---|
| Administration | 4 | 3 | 3 | 3 | 2 |
| Aggression | 3 | 2 | 2 | 2 | 2 |
| Construction | 4 | 3 | 3 | 2 | 2 |
| Mobilization | 4 | 3 | 3 | 2 | 2 |

Aggression is pip-starved (max 3, flat 2 for cards 3–6). Administration is the richest suit (3 pips all the way to card 5).

### Trick-taking action types
```
LeadAction(Color, ActionCard(...), Suit)        — leads the trick, picks suit, full pips
SurpassAction(Color, ActionCard(...), Suit)     — same suit + higher number = full pips
PivotAction(Color, ActionCard(...), Suit)       — off-suit, 1 action only (from card number)
CopyAction(Color, ActionCard(...), Suit)        — face-down, 1 action from lead suit only
SeizeAction(Color, ActionCard(...), ...)        — burn extra card (or Surpass with 7) to steal initiative
PassAction(Color)                               — player is out of cards, sits out
```

### Chapter structure
```
ShuffledDeckCardsAction([ActionCard(...), ...])  — full 20-card deck in deal order
DealCardsAction                                  — triggers dealing (no explicit per-player breakdown)
StartRoundAction                                 — one per trick
TransferInitiativeAction(Color)                  — ends a trick, winner takes initiative
EndChapterAction                                 — chapter over
StartChapterAction                               — next chapter begins (increments cur_chapter)
```

### Deal reconstruction
The deck in `ShuffledDeckCardsAction` is dealt in **block order**: player 1 gets positions 1–6, player 2 gets 7–12, player 3 gets 13–18. The 2 remaining cards (19–20) become **court cards** — available for mid-chapter acquisition. `PlayOrderAction([Blue, White, Red])` establishes the deal order (appears once at game start, doesn't change).

Block deal is correct for initial hands. Players may acquire additional cards via:
- **`ReserveCardAction`** — guild card prelude ability (Union cards: AdminUnion, ArmsUnion, SpacingUnion, ConstructionUnion each let you recover/grab an action card of their suit during prelude)
- **Vox cards** (e.g., `CallToActionBB`) — can grant extra action cards mid-chapter
- **Court card acquisition** — taking leftover cards from the board

### Guild cards vs Influence
- **`InfluenceAction`** = main-phase action, spends pips to place influence and potentially gain court/guild cards from the board
- **`ReserveCardAction`** = prelude-phase ability triggered by a guild card (specifically Union cards), gives an extra action card. NOT the same as Influence.

### Prelude phase
After winning a trick (or seizing), the leader gets a prelude before their main action:
```
PrePreludeActionAction(Color, Suit, N)   — announces N prelude tokens
PreludeActionAction(Color, Suit, N)      — each prelude token use
EndPreludeAction(Color, Suit, 0, N)      — prelude done
MainTurnAction(Color, Suit, step, total) — main actions begin
```
Guild card prelude abilities (including `ReserveCardAction`) fire between `PreludeActionAction` and `EndPreludeAction`.

### Ambition scoring — requires Playwright
The raw log has `ScoreAmbitionAction` and `AmbitionsScoredAction` but **does NOT record who won first/second place**. The only way to get ambition outcomes is to render the full game headless with Playwright and scrape the human-readable scoring text ("Player scored first place Tycoon for ⟅6⟆"). `extract_scoring_playwright()` handles this. It's slow (~10–30s per game) but is the only data source for ambition results.

### VoxCard usage
```
DiscardVoxCardAction(Color, VoxCard("bc26", MassUprisingBB), ...)
```
Vox cards appear as `VoxCard("id", CardName)`. `DeclareAmbitionAction` often contains a nested `DiscardVoxCardAction` when declared via Vox.

### Seize behavior
Seize fires in two ways:
1. Burn an extra action card during your turn (SeizeAction with a card + a following action)
2. Surpass with card number 7 (rare — no card 7 exists in base game, Leaders & Lores may differ)

After seizing, the seizer becomes leader and immediately does prelude + main action. Key coaching insight: **seizing on a copy/pivot turn is almost always correct** because you're burning what would have been a 1-pip turn anyway, and the initiative value is high.

---

## Analysis state dict `s` — key fields

`analyze_log(lines, players_map, scoring_data)` returns `s`:

```python
s['players']              # {abbrev: name}
s['final_scores']         # {abbrev: int}
s['winner']               # abbrev
s['turn_roles']           # {abbrev: {role: count}}
s['main_pips']            # {abbrev: [pip_vals]}       — one per trick played as Lead/Surpass/Seize
s['prelude_pips']         # {abbrev: [pip_vals]}
s['chapter_pips']         # {chapter: {abbrev: total}}
s['ambition_declarations']# list of decl dicts
s['scoring_lookup']       # {(chapter, abbrev, ambition): scoring_dict}  — from Playwright
s['chapter_deals']        # {chapter: {abbrev: [(suit, num, pips), ...]}}  — initial hands
s['chapter_court_cards']  # {chapter: [(suit, num, pips), ...]}           — leftover/court
s['deal_order']           # [color, ...] from PlayOrderAction
s['rounds_per_chapter']   # {chapter: round_count}
s['buildings_total']      # {abbrev: {type: count}}
s['seizes_by_chapter']    # {chapter: {abbrev: count}}
s['court_cards_by_player']# {abbrev: set(card_names)}
s['guild_cards']          # {abbrev: set(card_names)}
```

---

## Ambition declaration dict fields

Each entry in `s['ambition_declarations']`:
```python
{
  'player': abbrev,
  'ambition': 'Tycoon'|'Tyrant'|'Warlord'|'Keeper'|'Empath',
  'chapter': int,
  'round': int,
  'card_num': int,      # card number used to declare
  'pips': int,          # pip value of that card
  'via_vox': bool,
  'status': 'Won'|'2nd'|'Missing'|'NoData',
  'ch_vp': {chapter: vp},
  'ch_rank': {chapter: {abbrev: rank}},
  # snipe fields removed — do not re-add "sniped" framing to UI
}
```

---

## HTML output architecture

`generate_html(s, source_filename)` builds a single-file HTML string with:
- Inline CSS (dark theme, CSS variables)
- Chart.js from CDN for bar/line charts
- Sections: Score, Per-Player, Pip Analysis, Seize Analysis, Deal Luck, Court Cards, Resource Economy, Ambitions, Declaration Timeline

All f-strings use `{{` / `}}` to escape literal braces (since the whole thing is one big f-string).

Colors:
```python
PLAYER_HEX  = {'B': '#3B82F6', 'R': '#EF4444', 'W': '#CBD5E1', 'Y': '#F59E0B'}
AMBITION_HEX = {'Tycoon': '#F59E0B', 'Tyrant': '#EF4444', 'Warlord': '#8B5CF6',
                 'Keeper': '#10B981', 'Empath': '#EC4899'}
ACTION_HEX   = {'Aggression': '#B82020', 'Mobilization': '#308090',
                 'Construction': '#D85020', 'Administration': '#C8953A'}
```

---

## Known gotchas / things already fixed

1. **Pip count regex** — fixed. Always capture the 3rd param of ActionCard, not the 2nd.
2. **Snipe language** — removed entirely from UI by user request. Do not re-add "sniped", "sniper", "got sniped" anywhere in the output.
3. **Ambitions Won section** — was redundant and removed. The Declaration Timeline with Playwright scoring data covers it.
4. **`implicit_first` chapter detection** — some game variants don't emit `StartChapterAction` for ch1. The code handles this; don't break it.
5. **Scoring lookup keys** — `(chapter, abbrev, ambition)` where `abbrev` is single-letter (B/R/W/Y), not full color name.

---

## Strategic context (helps write better analysis copy)

- **Low pip hand ≠ bad chapter.** A player copying/pivoting most tricks doesn't benefit from high pip cards. Pip count is luck signal, not destiny.
- **Seizing is almost always right on a copy/pivot turn.** You're burning a 1-pip turn for initiative — nearly always a good trade.
- **Post-seize lead behavior matters.** Does the player declare ambition immediately? Play a high card to retain initiative? Geekus tends to play high cards (4.7 avg) to retain; Schvimvs declares ambition on ~62% of post-seize leads.
- **Union guild cards are a force multiplier.** Having AdminUnion/ArmsUnion/etc. effectively extends your hand by 1 card per chapter. Raiding to steal an opponent's Union card is doubly impactful (you gain, they lose).
- **End-of-chapter solo turns** (when others run out of cards) are free actions — full pip collection, main actions, zero opposition. Vox + Union cards can create a 3-card surplus that extends past opponents' hands.
- **Court cards in positions 19–20** are available for acquisition mid-chapter, not just from the initial deal.

---

## Running / dependencies

```bash
pip install playwright
playwright install chromium
python3 arcs_analyze.py <replay.html>
```

Playwright is optional — if not installed, ambition scoring falls back to raw log data (which is incomplete for winners).
