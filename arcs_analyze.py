#!/usr/bin/env python3
"""arcs_analyze.py — Analytical breakdown for Arcs board game replay HTML files.
Usage: python3 arcs_analyze.py <replay.html>
Outputs: <replay>_analysis.html next to the input file.
"""

import sys, re, json, os
from collections import defaultdict
from pathlib import Path

FULL_TO_ABBREV = {'Blue': 'B', 'Red': 'R', 'White': 'W', 'Yellow': 'Y'}
PLAYER_HEX = {'B': '#3B82F6', 'R': '#EF4444', 'W': '#CBD5E1', 'Y': '#F59E0B'}
AMBITION_HEX = {
    'Tycoon': '#F59E0B', 'Tyrant': '#EF4444',
    'Warlord': '#8B5CF6', 'Keeper': '#10B981', 'Empath': '#EC4899',
}
RESOURCE_HEX = {
    'Fuel':     '#C4A820',   # dark gold (sampled from asset-fuel)
    'Material': '#C06090',   # purple-pink (sampled from asset-material)
    'Weapon':   '#C86030',   # rust orange-brown (sampled from asset-weapon)
    'Relic':    '#70C0D0',   # light teal (sampled from asset-relic)
    'Psionic':  '#0080D0',   # blue (sampled from asset-psionic)
}
ALL_RESOURCES = ['Fuel', 'Material', 'Weapon', 'Relic', 'Psionic']

ACTION_HEX = {
    'Aggression':     '#B82020',   # deep red
    'Mobilization':   '#308090',   # teal
    'Construction':   '#D85020',   # burnt orange
    'Administration': '#C8953A',   # warm gold
}

# Guild card icon mapping — which resource icon each Guild card provides
GUILD_CARD_ICONS = {
    'AdminUnion': None,
    'ArmsUnion': 'Weapon',
    'MaterialCartel': 'Material',
    'MiningInterest': 'Material',
    'SecretOrder': 'Psionic',
    'LoyalKeepers': 'Relic',
    'LoyalPilots': 'Fuel',
    'SwornGuardians': 'Weapon',
    'SpacingUnion': 'Fuel',
    'ConstructionUnion': 'Material',
    'ElderBroker': 'Psionic',
    'GalacticBards': 'Psionic',
    'ShippingInterest': 'Fuel',
    'LoyalEngineers': 'Material',
    'LoyalEmpaths': 'Psionic',
    'Gatekeepers': 'Psionic',
    'PrisonWardens': 'Weapon',
    'Farseers': 'Psionic',
    'LatticeSpies': 'Psionic',
    'RelicFence': 'Relic',
    'FuelCartel': 'Fuel',
    'LoyalMarines': 'Weapon',
    'SilverTongues': 'Psionic',
    'CourtEnforcers': 'Weapon',
    'Skirmishers': 'Weapon',
    'PopulistDemandsBB': None,
    'SongOfFreedomBB': None,
    'CallToActionBB': None,
    'OutrageSpreadsBB': None,
}


# ── PLAYWRIGHT SCORING EXTRACTION ────────────────────────────────────────────

def extract_scoring_playwright(replay_path):
    """Use Playwright to render the replay and extract chapter-scoring data."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return []

    scores = []
    try:
        abs_path = str(Path(replay_path).resolve())
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.goto(f'file://{abs_path}')
                page.wait_for_load_state("domcontentloaded")
                page.wait_for_timeout(10000)
                page.click('body')
                for _ in range(80):
                    page.wait_for_timeout(3000)
                    body_text = page.inner_text('body')
                    if 'Game Over' in body_text:
                        break
                else:
                    body_text = page.inner_text('body')

                cur_chapter = None
                for line in body_text.split('\n'):
                    line = line.strip()
                    if 'Chapter' in line and 'had ended' in line:
                        cm = re.search(r'Chapter (\d+) had ended', line)
                        if cm:
                            cur_chapter = int(cm.group(1))
                    elif cur_chapter and 'scored' in line.lower() and 'place' in line:
                        scores.append((cur_chapter, line))
            finally:
                browser.close()
    except Exception:
        return []
    return scores


def parse_scoring_lines(chapter_scores):
    """Parse chapter-scoring tuples into structured data."""
    results = []
    pattern = r'(\w+) scored (first|second) place (\w+) for ⟅(\d+)⟆'
    for ch, line in chapter_scores:
        m = re.search(pattern, line)
        if m:
            results.append({
                'chapter': ch,
                'player': m.group(1),
                'place': m.group(2),
                'ambition': m.group(3),
                'vp': int(m.group(4)),
            })
    return results


# ── PARSING ──────────────────────────────────────────────────────────────────

def fa(color_str):
    return FULL_TO_ABBREV.get(color_str, color_str[0].upper() if color_str else '?')

def parse_lobby(content):
    m = re.search(r'id="lobby"[^>]*>(.*?)</div>', content, re.DOTALL)
    if not m:
        return {}
    lobby = m.group(1)
    info = {}
    t = re.search(r'title\s+(.+)', lobby)
    info['title'] = t.group(1).strip() if t else 'Unknown Game'
    v = re.search(r'version\s+(.+)', lobby)
    info['version'] = v.group(1).strip() if v else '?'
    info['players'] = {}
    for pm in re.finditer(r'name\s+player-(\w+)\s+(\S+)', lobby):
        info['players'][pm.group(1).upper()] = pm.group(2)
    sm = re.search(r'seating\s+(.+)', lobby)
    info['seating'] = sm.group(1).strip().split() if sm else []
    om = re.search(r'options\s+(.+)', lobby)
    info['options'] = om.group(1).strip().split() if om else []
    return info

def parse_replay_log(content):
    m = re.search(r'id="replay"[^>]*>(.*?)</div>', content, re.DOTALL)
    if not m:
        return []
    return [l.strip() for l in m.group(1).split('\n') if l.strip()]


# ── ANALYSIS ─────────────────────────────────────────────────────────────────

def analyze_log(lines, players_map, scoring_data=None):
    plist = list(players_map.keys())

    def pp():  # per-player zero dict
        return {p: 0 for p in plist}

    s = {
        'players': players_map,
        'leaders': {}, 'lores': {},
        'final_scores': {}, 'winner': None, 'num_chapters': 0,
        'turn_roles': {p: defaultdict(int) for p in plist},
        'main_pips': {p: [] for p in plist},
        'prelude_pips': {p: [] for p in plist},
        'action_types': {p: defaultdict(int) for p in plist},
        'chapter_pips': defaultdict(pp),
        'chapter_actions': defaultdict(lambda: {p: defaultdict(int) for p in plist}),
        'buildings_total': {p: defaultdict(int) for p in plist},
        'buildings_by_chapter': defaultdict(lambda: {p: defaultdict(int) for p in plist}),
        'initiative_by_chapter': defaultdict(pp),
        'seizes_by_chapter': defaultdict(pp),
        'pip_actions_by_chapter': defaultdict(pp),
        'ambition_declarations': [],
        'ambitions_by_player': {p: defaultdict(int) for p in plist},
        'court_cards_by_player': {p: set() for p in plist},
        'court_by_chapter': defaultdict(lambda: {p: [] for p in plist}),
        'rounds_per_chapter': {},
        'resources_gained': {p: defaultdict(int) for p in plist},
        'resources_spent': {p: defaultdict(int) for p in plist},
        'battle_actions': {p: 0 for p in plist},
        'guild_cards': {p: set() for p in plist},
        'guild_card_icons': {p: defaultdict(int) for p in plist},
        'chapter_resources': defaultdict(lambda: {p: defaultdict(int) for p in plist}),
        'chapter_deals': {},      # chapter -> {abbrev: [(suit, num, pips), ...]}
        'deal_order': [],         # color list from PlayOrderAction
        'chapter_court_cards': {}, # chapter -> [(suit, num, pips), ...] leftover/court
        'ship_kills': {p: 0 for p in plist},      # confirmed kills dealt by player
        'ship_losses': {p: 0 for p in plist},     # confirmed own ships lost
        'battles_initiated': {p: 0 for p in plist},
        'battles_defended': {p: 0 for p in plist},
        'raid_successes': {p: 0 for p in plist},
        'raid_steals': [],        # list of (chapter, attacker, defender, card_name)
        'ransacks': {p: 0 for p in plist},
        'buildings_destroyed_dealt': {p: 0 for p in plist},  # starport/city hits dealt
        'buildings_destroyed_taken': {p: 0 for p in plist},
    }

    # Some games (Leaders&Lores) emit no StartChapterAction for ch1 (it starts implicitly
    # after setup). Others (base game) emit one immediately. Detect which case we're in.
    first_chapter_idx = next((i for i, l in enumerate(lines) if l == 'StartChapterAction'), None)
    first_round_idx   = next((i for i, l in enumerate(lines) if l == 'StartRoundAction'), None)
    implicit_first = (first_round_idx is not None and
                      (first_chapter_idx is None or first_round_idx < first_chapter_idx))
    cur_chapter = 1 if implicit_first else 0
    cur_round = 0
    prev_line = ''
    seen_secure = set()
    ship_hp = {}  # ship_id ("Blue/Ship/4") -> current HP; ships have 2 HP fresh

    for l in lines:
        # ── Play order (deal order) ──
        pom = re.match(r'PlayOrderAction\(\[([^\]]+)\]\)', l)
        if pom:
            s['deal_order'] = [c.strip() for c in pom.group(1).split(',')]

        # ── Dealt hands (block deal: player i gets positions i*6..(i+1)*6) ──
        dm = re.match(r'ShuffledDeckCardsAction\(\[(.+)\]\)', l)
        if dm and cur_chapter > 0:
            raw_cards = re.findall(r'ActionCard\((\w+),\s*(\d+),\s*(\d+)\)', dm.group(1))
            full_deck  = [(suit, int(num), int(pips)) for suit, num, pips in raw_cards]
            po = s['deal_order']
            n  = len(po)
            hands = {}
            for pi, color in enumerate(po):
                abbr = fa(color)
                hands[abbr] = full_deck[pi*6:(pi+1)*6]
            s['chapter_deals'][cur_chapter] = hands
            s['chapter_court_cards'][cur_chapter] = full_deck[n*6:]

        # ── Chapter / round structure ──
        if l == 'StartChapterAction':
            cur_chapter += 1
            cur_round = 0
        elif 'EndChapterAction' in l:
            s['rounds_per_chapter'][cur_chapter] = cur_round
            s['num_chapters'] = max(s['num_chapters'], cur_chapter)
        elif 'TransferInitiativeAction' in l and not prev_line.startswith('UndoAction'):
            # Count only non-undo-replayed transfers as completed rounds
            cur_round += 1

        # ── Game over ──
        if 'ArcsGameOverAction' in l:
            gm = re.search(
                r'ArcsGameOverAction\([^,]+,\s*\[[^\]]+\],\s*\[[^\]]+\],\s*(\w+),\s*\{([^}]+)\},\s*(\d+)\)', l)
            if gm:
                s['winner'] = fa(gm.group(1))
                for sm in re.finditer(r'(\w+)\s*->\s*(\d+)', gm.group(2)):
                    s['final_scores'][fa(sm.group(1))] = int(sm.group(2))
                s['num_chapters'] = max(s['num_chapters'], int(gm.group(3)))

        # ── Leaders & lores ──
        lm = re.match(r'AssignLeaderAction\((\w+),\s*(\w+)', l)
        if lm: s['leaders'][fa(lm.group(1))] = lm.group(2)
        lm = re.match(r'AssignLoreAction\((\w+),\s*(\w+)', l)
        if lm: s['lores'][fa(lm.group(1))] = lm.group(2)

        # ── Initiative ──
        im = re.search(r'TransferInitiativeAction\((\w+)\)', l)
        if im and cur_chapter > 0:
            p = fa(im.group(1))
            if p in s['initiative_by_chapter'][cur_chapter]:
                s['initiative_by_chapter'][cur_chapter][p] += 1

        # ── Turn roles ──
        for role in ['LeadAction', 'SurpassAction', 'PivotAction', 'CopyAction',
                     'SeizeAction', 'LatticeSeizeAction', 'PassAction']:
            rm = re.match(role + r'\((\w+),', l)
            if not rm:
                continue
            p = fa(rm.group(1))
            if p not in s['turn_roles']:
                continue
            label = role.replace('Action', '').replace('Lattice', '')
            s['turn_roles'][p][label] += 1
            if role in ('SeizeAction', 'LatticeSeizeAction') and cur_chapter > 0:
                s['seizes_by_chapter'][cur_chapter][p] += 1
            if role in ('LeadAction', 'SurpassAction', 'PivotAction', 'CopyAction'):
                # ActionCard(...) contains commas, so skip it explicitly
                at_m = re.search(r'Action\(\w+,\s*ActionCard\([^)]+\),\s*(\w+)', l)
                if at_m:
                    s['action_types'][p][at_m.group(1)] += 1

        # ── Main pips — count card pip values from Lead/Surpass/Seize play lines ──
        # ActionCard(type, pip_value, card_num); Copy/Pivot only get 1 pip action.
        for role, pip_scale in [('LeadAction', 'full'), ('SurpassAction', 'full'),
                                  ('SeizeAction', 'full'), ('CopyAction', 'one'),
                                  ('PivotAction', 'one')]:
            cm = re.match(role + r'\(\w+,\s*ActionCard\([^,]+,\s*\d+,\s*(\d+)', l)
            if cm:
                pip_val = int(cm.group(1)) if pip_scale == 'full' else 1
                pm2 = re.match(role + r'\((\w+)', l)
                if pm2:
                    p = fa(pm2.group(1))
                    if p in s['main_pips']:
                        s['main_pips'][p].append(pip_val)
                        s['chapter_pips'][cur_chapter][p] += pip_val

        # ── Prelude pips ──
        pm = re.search(r'EndPreludeAction\((\w+),\s*\w+,\s*\d+,\s*(\d+)\)', l)
        if pm:
            p = fa(pm.group(1))
            if p in s['prelude_pips']:
                s['prelude_pips'][p].append(int(pm.group(2)))

        # ── Buildings ──
        for btype, baction in [('City', 'BuildCityAction'), ('Starport', 'BuildStarportAction'),
                                ('Ship', 'BuildShipAction')]:
            bm = re.search(baction + r'\((\w+),', l)
            if bm:
                p = fa(bm.group(1))
                if p in s['buildings_total']:
                    s['buildings_total'][p][btype] += 1
                    s['buildings_by_chapter'][cur_chapter][p][btype] += 1

        # ── Combat: ship HP lifecycle (ships have 2 HP fresh) ──
        bsm = re.search(r'BuildShipAction\((\w+),', l)
        if bsm:
            sid_m = re.search(r'(\w+/Ship/\d+)', l)
            if sid_m:
                ship_hp[sid_m.group(1)] = 2
        rpm = re.match(r'RepairAction\((\w+),.*?,\s*(\w+/Ship/\d+),', l)
        if rpm:
            ship_hp[rpm.group(2)] = 2
        dhm = re.match(r'DealHitsAction\((\w+),\s*(\w+),\s*\w+,\s*\[([^\]]*)\]', l)
        if dhm:
            dealer, victim = fa(dhm.group(1)), fa(dhm.group(2))
            ship_targets = re.findall(r'(\w+/Ship/\d+)', dhm.group(3))
            for sid in ship_targets:
                cur = ship_hp.get(sid, 2)
                cur -= 1
                ship_hp[sid] = cur
                if cur <= 0 and cur != -999:
                    ship_hp[sid] = -999
                    if dealer in s['ship_kills']:
                        s['ship_kills'][dealer] += 1
                    owner = fa(sid.split('/')[0])
                    if owner in s['ship_losses']:
                        s['ship_losses'][owner] += 1
            n_sp = len(re.findall(r'/Starport/\d+', dhm.group(3)))
            n_city = len(re.findall(r'/City/\d+', dhm.group(3)))
            if n_sp or n_city:
                if dealer in s['buildings_destroyed_dealt']:
                    s['buildings_destroyed_dealt'][dealer] += n_sp + n_city
                if victim in s['buildings_destroyed_taken']:
                    s['buildings_destroyed_taken'][victim] += n_sp + n_city

        # ── Combat: battles initiated / defended ──
        bfm = re.match(r'BattleFactionAction\((\w+),.*?,\s*(\w+),\s*\[\],\s*(\w+),', l)
        if bfm:
            attacker, defender = fa(bfm.group(1)), fa(bfm.group(3))
            if attacker in s['battles_initiated']:
                s['battles_initiated'][attacker] += 1
            if defender in s['battles_defended']:
                s['battles_defended'][defender] += 1

        # ── Combat: guild card raids ──
        brcm = re.match(r'BattleRaidCourtCardAction\((\w+),\s*(\w+),\s*GuildCard\("[^"]+",\s*(\w+)\)', l)
        if brcm:
            attacker, defender, card = fa(brcm.group(1)), fa(brcm.group(2)), brcm.group(3)
            if attacker in s['raid_successes']:
                s['raid_successes'][attacker] += 1
            s['raid_steals'].append((cur_chapter, attacker, defender, card))

        rsm = re.match(r'RansackAction\((\w+),', l)
        if rsm:
            p = fa(rsm.group(1))
            if p in s['ransacks']:
                s['ransacks'][p] += 1

        # ── Resource economy ──
        # Gains: TaxGainAction(player, |(ResourceType), ...)
        tgm = re.match(r'TaxGainAction\((\w+),\s*\|\((\w+)\)', l)
        if tgm:
            p, res = fa(tgm.group(1)), tgm.group(2)
            if p in s['resources_gained']:
                s['resources_gained'][p][res] += 1
        # GainResourceAction(player, ResourceType, ...) — prelude / ability gains
        grm = re.match(r'GainResourceAction\((\w+),\s*(\w+),', l)
        if grm:
            p, res = fa(grm.group(1)), grm.group(2)
            if p in s['resources_gained']:
                s['resources_gained'][p][res] += 1
        # Spends: any action where the cost field is PayResource(Type#id, ...)
        spm = re.search(r'\b\w+Action\((\w+),\s*PayResource\((\w+)#', l)
        if spm:
            p, res = fa(spm.group(1)), spm.group(2)
            if p in s['resources_spent']:
                s['resources_spent'][p][res] += 1

        # ── Chapter actions — the 7 player-facing game actions ──
        # Use \b word boundary to avoid PostTaxAction matching TaxAction,
        # MayInfluenceAction matching InfluenceAction, etc.
        for aname, aact in [
            ('Tax',      'TaxAction'),
            ('Influence','InfluenceAction'),
            ('Battle',   'BattleFactionAction'),   # initiating action, not dice roll
            ('Secure',   'SecureAction'),
            ('Repair',   'RepairAction'),
        ]:
            am = re.search(r'\b' + aact + r'\((\w+),', l)
            if am:
                p = fa(am.group(1))
                if p in s['chapter_actions'][cur_chapter]:
                    s['chapter_actions'][cur_chapter][p][aname] += 1
                if aname == 'Battle':
                    s['battle_actions'][p] += 1

        # Move: only count Pip-cost legs (NoCost legs are free ability follow-ons)
        mm2 = re.search(r'\bMoveListAction\((\w+),\s*\w+,\s*\w+,\s*\[.*?\],\s*\w+,\s*(Pip)', l)
        if mm2:
            p = fa(mm2.group(1))
            if p in s['chapter_actions'][cur_chapter]:
                s['chapter_actions'][cur_chapter][p]['Move'] += 1

        # Build = City + Starport + Ship combined
        for aact in ('BuildCityAction', 'BuildStarportAction', 'BuildShipAction'):
            am = re.search(r'\b' + aact + r'\((\w+),', l)
            if am:
                p = fa(am.group(1))
                if p in s['chapter_actions'][cur_chapter]:
                    s['chapter_actions'][cur_chapter][p]['Build'] += 1

        # ── Ambitions ──
        adm = re.search(
            r'DeclareAmbitionAction\((\w+),\s*(None|\|[^,]*),\s*(\w+),\s*AmbitionMarker', l)
        if adm:
            p = fa(adm.group(1))
            if p in s['ambitions_by_player']:
                s['ambition_declarations'].append({
                    'chapter': cur_chapter, 'player': p,
                    'ambition': adm.group(3), 'via_vox': adm.group(2) != 'None'
                })
                s['ambitions_by_player'][p][adm.group(3)] += 1

        # ── Court cards ──
        scm = re.search(r'SecuredLaneAction\((\w+),\s*\[([^\]]*)\]', l)
        if scm:
            p = fa(scm.group(1))
            for cm in re.finditer(r'"(bc\d+)",\s*(\w+)\)', scm.group(2)):
                card_id, card_name = cm.group(1), cm.group(2)
                key = (card_id, cur_chapter, cur_round, p)
                if key not in seen_secure and p in s['court_cards_by_player']:
                    seen_secure.add(key)
                    s['court_cards_by_player'][p].add(card_name)
                    s['court_by_chapter'][cur_chapter][p].append(card_name)

        # ── Guild cards — unique cards per player ──
        gcm = re.search(r'SecuredLaneAction\((\w+), (\[[^\]]+\])', l)
        if gcm:
            p = fa(gcm.group(1))
            cards_str = gcm.group(2)
            for gc in re.finditer(r'GuildCard\("(\w+)", (\w+)\)', cards_str):
                card_id = gc.group(1)
                card_name = gc.group(2)
                key = (card_id, p)
                if key not in seen_secure:
                    seen_secure.add(key)
                    if p in s['guild_cards']:
                        s['guild_cards'][p].add(card_name)
                        icon = GUILD_CARD_ICONS.get(card_name)
                        if icon:
                            s['guild_card_icons'][p][icon] += 1

        # ── Resource holdings snapshots ──
        rrm = re.search(r'ReorderResourcesAction\((\w+), \[([^\]]*)\]', l)
        if rrm:
            p = fa(rrm.group(1))
            res_str = rrm.group(2)
            for res in re.findall(r'\|\((\w+)#', res_str):
                if p in s['chapter_resources'][cur_chapter]:
                    s['chapter_resources'][cur_chapter][p][res] += 1

        prev_line = l

    # ── Derived ──
    for p in plist:
        pips = s['main_pips'][p]
        s[f'pip_total_{p}'] = sum(pips)
        s[f'pip_avg_{p}'] = round(sum(pips) / len(pips), 1) if pips else 0
        s[f'pip_high_{p}'] = sum(1 for x in pips if x >= 3)
        s[f'pip_low_{p}'] = sum(1 for x in pips if x <= 2)
        s[f'prelude_total_{p}'] = sum(s['prelude_pips'][p])
        kills, losses = s['ship_kills'][p], s['ship_losses'][p]
        s[f'kd_{p}'] = round(kills / losses, 2) if losses else (float(kills) if kills else 0.0)

   # ── Build scoring lookup from Playwright data ──
    # Scoring data has color names (Blue, Red, White, Yellow)
    # Need to map to abbreviations (B, R, W, Y)
    scoring_lookup = {}
    if scoring_data:
        for sd in scoring_data:
            color_abbr = FULL_TO_ABBREV.get(sd['player'], sd['player'][0].upper())
            key = (sd['chapter'], color_abbr, sd['ambition'])
            scoring_lookup[key] = sd

    # ── Ambition evaluation (competitive ranking) ──
    # Two-pass snipe detection: track both "did I snipe someone" and "did someone snipe me"
    decls_by_amb = defaultdict(list)
    for decl in s['ambition_declarations']:
        decls_by_amb[decl['ambition']].append(decl)

    for amb, decl_list in decls_by_amb.items():
        for i, decl in enumerate(decl_list):
            p = decl['player']
            prev = decl_list[i - 1] if i > 0 else None
            nxt  = decl_list[i + 1] if i < len(decl_list) - 1 else None
            # This player re-declared over someone else
            decl['is_sniper']        = prev is not None and prev['player'] != p
            decl['sniped_from']      = prev['player'] if decl['is_sniper'] else None
            # A different player later re-declared over this player
            decl['got_sniped']       = nxt is not None and nxt['player'] != p
            decl['sniped_by']        = nxt['player'] if decl['got_sniped'] else None

    # Score each declared ambition per chapter
    for decl in s['ambition_declarations']:
        amb = decl['ambition']
        p = decl['player']
        ch = decl['chapter']
        decl['ch_score'] = {}
        decl['ch_rank'] = {}
        decl['ch_vp'] = {}

        for c in range(1, s['num_chapters'] + 1):
            key = (c, p, amb)
            if key in scoring_lookup:
                sd = scoring_lookup[key]
                decl['ch_vp'][c] = sd['vp']
                decl['ch_rank'][c] = {p: sd['place']}
                # Build full ranking for this chapter from scoring data
                ch_players = {pl: 0 for pl in plist}
                for s2 in scoring_data:
                    if s2['chapter'] == c and s2['ambition'] == amb:
                        ch_players[s2['player']] = s2['vp']
                decl['ch_score'][c] = ch_players
            else:
                # No scoring data for this chapter/ambition/player
                decl['ch_vp'][c] = None
                decl['ch_score'][c] = {pl: None for pl in plist}
                decl['ch_rank'][c] = {pl: None for pl in plist}

        # Determine overall status
        player_ch_ranks = [decl['ch_rank'].get(c, {}).get(p) for c in range(1, s['num_chapters'] + 1)]
        player_ch_vps = [decl.get('ch_vp', {}).get(c) for c in range(1, s['num_chapters'] + 1)]

        has_won = any(r == 'first' for r in player_ch_ranks)
        has_second = any(r == 'second' for r in player_ch_ranks)
        has_data = any(r is not None for r in player_ch_ranks)

        if has_won:
            decl['status'] = 'Won'
        elif has_second:
            decl['status'] = '2nd'
        elif has_data:
            decl['status'] = 'Missing'
        else:
            decl['status'] = 'Missing'

        # Check if any chapter has actual ranking data (not NoData or None)
        has_ranking = any(r in ('first', 'second') for r in player_ch_ranks)
        has_no_data = any(r == 'NoData' for r in player_ch_ranks)

        if has_no_data:
            # Tyrant/Warlord: data not available
            decl['status'] = 'NoData'
            decl['reason'] = 'Captives/Trophies not tracked in replay'
        elif any(r == 'first' for r in player_ch_ranks):
            decl['status'] = 'Won'
            decl['first_chapters'] = [c+1 for c, r in enumerate(player_ch_ranks) if r == 'first']
        elif any(r == 'second' for r in player_ch_ranks):
            decl['status'] = '2nd'
            decl['second_chapters'] = [c+1 for c, r in enumerate(player_ch_ranks) if r == 'second']
        else:
            decl['status'] = 'Missing'
            decl['reason'] = 'Never placed in top 2'

    return s


# ── HTML GENERATION ───────────────────────────────────────────────────────────

def player_name(s, p):
    return s['players'].get(p, p)

def player_hex(p):
    return PLAYER_HEX.get(p, '#888')

def jc(obj):
    return json.dumps(obj)

def generate_html(s, source_filename):
    plist = list(s['players'].keys())
    pnames = [player_name(s, p) for p in plist]
    phexes = [player_hex(p) for p in plist]
    winner = s['winner']
    chapters = list(range(1, s['num_chapters'] + 1))
    chapter_labels = [f'Ch {c}' for c in chapters]

    # Score bars dataset
    scores = [s['final_scores'].get(p, 0) for p in plist]
    max_score = max(scores) if scores else 1

    # Turn roles data
    roles = ['Lead', 'Surpass', 'Pivot', 'Copy', 'Seize', 'Pass']
    role_colors = ['#3B82F6', '#06B6D4', '#8B5CF6', '#F59E0B', '#EF4444', '#6B7280']
    role_datasets = []
    for i, role in enumerate(roles):
        role_datasets.append({
            'label': role,
            'data': [s['turn_roles'][p].get(role, 0) for p in plist],
            'backgroundColor': role_colors[i],
        })

    # Pip throughput
    pip_totals = [s.get(f'pip_total_{p}', 0) for p in plist]
    pip_avgs = [s.get(f'pip_avg_{p}', 0) for p in plist]
    prelude_totals = [s.get(f'prelude_total_{p}', 0) for p in plist]

    # Initiative gains by chapter — stacked bars, seizes shown as hatched overlay
    # "Regular" = gained initiative by leading the round (total gains minus seizes)
    # "Seize"   = paid a card to grab initiative mid-round (highlighted)
    init_datasets = []
    for i, p in enumerate(plist):
        pname = player_name(s, p)
        px = phexes[i]
        total = [s['initiative_by_chapter'].get(c, {}).get(p, 0) for c in chapters]
        seize = [s['seizes_by_chapter'].get(c, {}).get(p, 0) for c in chapters]
        regular = [t - z for t, z in zip(total, seize)]
        init_datasets.append({
            'label': pname,
            'data': regular,
            'backgroundColor': px,
            'stack': p,
        })
        if any(v > 0 for v in seize):
            init_datasets.append({
                'label': f'{pname} (Seize ★)',
                'data': seize,
                'backgroundColor': '#F0C040',
                'borderColor': px,
                'borderWidth': 2,
                'stack': p,
            })

    # Pip throughput by chapter
    pip_chapter_datasets = []
    for i, p in enumerate(plist):
        pip_chapter_datasets.append({
            'label': player_name(s, p),
            'data': [s['chapter_pips'].get(c, {}).get(p, 0) for c in chapters],
            'borderColor': phexes[i],
            'backgroundColor': phexes[i] + '33',
            'tension': 0.3,
            'fill': False,
        })

    # Action types — horizontal stacked % bar (one bar per player, segments = action types)
    action_types_list = ['Construction', 'Mobilization', 'Aggression', 'Administration']
    act_type_colors = [ACTION_HEX['Construction'], ACTION_HEX['Mobilization'],
                       ACTION_HEX['Aggression'], ACTION_HEX['Administration']]
    # Compute % of each player's turns spent on each action type
    act_type_totals = {p: max(1, sum(s['action_types'][p].get(at, 0) for at in action_types_list))
                       for p in plist}
    act_type_datasets = []
    for i, at in enumerate(action_types_list):
        act_type_datasets.append({
            'label': at,
            'data': [round(100 * s['action_types'][p].get(at, 0) / act_type_totals[p], 1)
                     for p in plist],
            'backgroundColor': act_type_colors[i],
        })

    # Buildings total — grouped bar
    btypes = ['City', 'Starport', 'Ship']
    bcolors = ['#10B981', '#3B82F6', '#8B5CF6']
    building_datasets = []
    for i, bt in enumerate(btypes):
        building_datasets.append({
            'label': bt,
            'data': [s['buildings_total'][p].get(bt, 0) for p in plist],
            'backgroundColor': bcolors[i],
        })

    # The 7 player-facing game actions — colors match home card type from game art:
    #   Tax→Administration gold, Influence→Mobilization light, Move→Mobilization dark,
    #   Battle→Aggression bright, Secure→Aggression dark,
    #   Build→Construction bright, Repair→Construction light
    ALL_ACTS   = ['Tax', 'Influence', 'Move', 'Battle', 'Secure', 'Build', 'Repair']
    ALL_COLORS = ['#C8953A', '#42A8B8', '#2D7A8A', '#C41C1C', '#8B2020', '#D85020', '#E88030']
    act_types  = ALL_ACTS
    act_colors = ALL_COLORS

    action_totals = {p: {a: sum(s['chapter_actions'][c][p].get(a, 0) for c in chapters)
                         for a in ALL_ACTS} for p in plist}

    # Existing stacked bar (summary activity chart — keep all types)
    action_datasets = []
    for i, at in enumerate(ALL_ACTS):
        action_datasets.append({
            'label': at,
            'data': [action_totals[p][at] for p in plist],
            'backgroundColor': ALL_COLORS[i],
        })

    # Per-player chapter timeline datasets (one chart per player)
    def player_chapter_datasets(p):
        ds = []
        for i, aname in enumerate(ALL_ACTS):
            vals = [s['chapter_actions'].get(c, {}).get(p, {}).get(aname, 0) for c in chapters]
            if any(v > 0 for v in vals):   # skip zero-rows
                ds.append({'label': aname, 'data': vals, 'backgroundColor': ALL_COLORS[i]})
        return ds

    # Combined grouped-stacked chart: one stack per player, segments = action types.
    # Legend labels only emitted for the first player to avoid duplicates.
    # borderColor carries the player hex so the stackBorder plugin can draw one
    # outline rect per complete stack rather than per segment.
    def combined_chapter_datasets():
        ds = []
        for i_p, p in enumerate(plist):
            pname = player_name(s, p)
            px = phexes[i_p]
            for i_a, aname in enumerate(ALL_ACTS):
                vals = [s['chapter_actions'].get(c, {}).get(p, {}).get(aname, 0) for c in chapters]
                ds.append({
                    'label': aname if i_p == 0 else '',
                    'stack': pname,
                    'data': vals,
                    'backgroundColor': ALL_COLORS[i_a],
                    'borderColor': px,   # read by stackBorderPlugin
                    'borderWidth': 0,    # no per-segment borders
                })
        return ds

    # Player-stack legend for the combined chart (player name → color)
    combined_player_legend = ''.join(
        f'<span style="display:inline-flex;align-items:center;gap:5px;margin-right:14px">'
        f'<span style="width:12px;height:12px;border:2px solid {phexes[i]};border-radius:2px;display:inline-block"></span>'
        f'<span style="color:{phexes[i]};font-size:0.82rem">{player_name(s, p)}</span></span>'
        for i, p in enumerate(plist)
    )

    # Court cards
    court_counts = [len(s['court_cards_by_player'].get(p, set())) for p in plist]

    # Ambitions summary
    all_ambitions = ['Tycoon', 'Tyrant', 'Warlord', 'Keeper', 'Empath']
    ambition_datasets = []
    for amb in all_ambitions:
        ambition_datasets.append({
            'label': amb,
            'data': [s['ambitions_by_player'][p].get(amb, 0) for p in plist],
            'backgroundColor': AMBITION_HEX.get(amb, '#888'),
        })

    # Ambition timeline entries
    STATUS_ICONS = {'Won': '✓', 'Missing': '✗', 'Sniped': '⚡', '2nd': '🥈', 'NoData': '?'}
    STATUS_COLORS = {'Won': '#22C55E', 'Missing': '#EF4444', 'Sniped': '#F59E0B', '2nd': '#60A5FA', 'NoData': '#6B7280'}
    ambition_timeline = []
    for decl in s['ambition_declarations']:
        pname = player_name(s, decl['player'])
        px = player_hex(decl['player'])
        ax = AMBITION_HEX.get(decl['ambition'], '#888')
        vox = ' (via Vox)' if decl['via_vox'] else ''
        status = decl['status']
        sc = STATUS_COLORS.get(status, '#888')
        si = STATUS_ICONS.get(status, '?')
        # Build VP/placement inline — only for the declaration chapter
        c = decl['chapter']
        vp = decl.get('ch_vp', {}).get(c)
        rank = decl['ch_rank'].get(c, {}).get(decl['player'])
        if vp is not None and rank:
            vp_str = f'<span style="color:#FBBF24;font-size:0.72rem">Ch{c}: {vp}VP</span> <span style="color:#60A5FA;font-size:0.72rem">({rank})</span>'
        elif rank:
            vp_str = f'<span style="color:#4B5563;font-size:0.72rem">Ch{c}: —</span> <span style="color:#4B5563;font-size:0.72rem">({rank})</span>'
        else:
            vp_str = f'<span style="color:#4B5563;font-size:0.72rem">Ch{c}: ?</span>'

        # Build scoring results — 1st/2nd place with VP, sorted first before second
        scoring_entries = []
        for key, sd in s.get('scoring_lookup', {}).items():
            if key[0] == c and key[2] == decl['ambition']:
                scoring_entries.append(sd)
        scoring_entries.sort(key=lambda x: 0 if x['place'] == 'first' else 1)
        result_parts = []
        for sd in scoring_entries:
            wabbr = FULL_TO_ABBREV.get(sd['player'], sd['player'][0].upper())
            wname = player_name(s, wabbr)
            whex  = player_hex(wabbr)
            medal = '🥇' if sd['place'] == 'first' else '🥈'
            result_parts.append(f'<span style="color:{whex};font-weight:700">{medal} {wname} {sd["vp"]}VP</span>')
        results_str = ' <span style="color:#475569"> · </span> '.join(result_parts) if result_parts else '<span style="color:#4B5563">no scoring data</span>'
        vox_str = f'<span style="color:#64748B;font-size:0.7rem">via Vox</span>' if decl['via_vox'] else ''
        ambition_timeline.append(
            f'<div class="timeline-entry" style="border-left:3px solid {ax}">'
            f'<span class="tl-chapter">Ch {decl["chapter"]}</span>'
            f'<span class="tl-player" style="color:{px}">{pname}</span>'
            f'<span class="tl-ambition" style="color:{ax}">{decl["ambition"]}</span>'
            f'<span class="tl-results">{results_str}</span>'
            f'{(" " + vox_str) if vox_str else ""}'
            f'</div>'
        )

     # Ambitions won — status tracking
    STATUS_COLORS = {'Won': '#22C55E', 'Missing': '#EF4444', 'Sniped': '#F59E0B', '2nd': '#60A5FA', 'NoData': '#6B7280'}

    # Resource economy datasets
    res_colors = [RESOURCE_HEX[r] for r in ALL_RESOURCES]
    res_gained_datasets = []
    res_spent_datasets = []
    for i, res in enumerate(ALL_RESOURCES):
        gained_vals = [s['resources_gained'].get(p, {}).get(res, 0) for p in plist]
        spent_vals  = [s['resources_spent'].get(p, {}).get(res, 0) for p in plist]
        if any(v > 0 for v in gained_vals + spent_vals):
            res_gained_datasets.append({'label': res, 'data': gained_vals, 'backgroundColor': res_colors[i]})
            res_spent_datasets.append({'label': res, 'data': spent_vals,  'backgroundColor': res_colors[i]})

    # Deal luck — per-chapter starting hands
    SUIT_HEX = {'Administration': '#C8953A', 'Aggression': '#B82020',
                'Construction': '#D85020', 'Mobilization': '#308090'}
    SUIT_ABB  = {'Administration': 'Adm', 'Aggression': 'Agg',
                 'Construction': 'Con', 'Mobilization': 'Mob'}
    AMBITION_NAMES = {2: 'Tycoon', 3: 'Tyrant', 4: 'Warlord', 5: 'Keeper', 6: 'Empath'}

    deal_luck_rows = []
    for ch in sorted(s['chapter_deals'].keys()):
        hands = s['chapter_deals'][ch]
        court = s['chapter_court_cards'].get(ch, [])
        row_cells = [f'<td class="deal-ch">Ch {ch}</td>']
        for p in plist:
            hand = hands.get(p, [])
            total_pips = sum(pip for _, _, pip in hand)
            sixes = sum(1 for _, num, _ in hand if num == 6)
            twos  = sum(1 for _, num, _ in hand if num == 2)
            if total_pips >= 18:
                qual_cls, qual_tip = 'deal-rich', f'{total_pips}pip'
            elif total_pips <= 12:
                qual_cls, qual_tip = 'deal-poor', f'{total_pips}pip'
            else:
                qual_cls, qual_tip = 'deal-mid', f'{total_pips}pip'

            card_spans = []
            for suit, num, pips in hand:
                sh = SUIT_HEX.get(suit, '#888')
                sa = SUIT_ABB.get(suit, suit[:3])
                amb = AMBITION_NAMES.get(num, '?')[:4]
                extra = ''
                if num == 6:
                    extra = ' deal-six'
                elif num == 2:
                    extra = ' deal-two'
                card_spans.append(
                    f'<span class="deal-card{extra}" style="border-color:{sh}" title="{suit} {num} ({pips}pip / {amb})">'
                    f'<span style="color:{sh}">{sa}{num}</span>'
                    f'<span class="deal-pip">{pips}p</span></span>'
                )
            note = ''
            if sixes >= 3:
                note = f'<span class="deal-note deal-note-bad">⚠ {sixes}×6</span>'
            elif twos >= 3:
                note = f'<span class="deal-note deal-note-good">★ {twos}×2</span>'
            row_cells.append(
                f'<td class="deal-player-cell"><div class="deal-player-header">'
                f'<span class="{qual_cls}">{qual_tip}</span>{note}</div>'
                f'<div class="deal-cards">{"".join(card_spans)}</div></td>'
            )
        # Court cards (leftover)
        if court:
            court_spans = []
            for suit, num, pips in court:
                sh = SUIT_HEX.get(suit, '#888')
                sa = SUIT_ABB.get(suit, suit[:3])
                amb = AMBITION_NAMES.get(num, '?')[:4]
                court_spans.append(
                    f'<span class="deal-card deal-court" style="border-color:{sh}">'
                    f'<span style="color:{sh}">{sa}{num}</span>'
                    f'<span class="deal-pip">{pips}p</span></span>'
                )
            row_cells.append(f'<td class="deal-player-cell"><div class="deal-cards">{"".join(court_spans)}</div></td>')
        else:
            row_cells.append('<td></td>')
        deal_luck_rows.append(f'<tr>{"".join(row_cells)}</tr>')

    deal_luck_header = '<tr><th>Chapter</th>' + ''.join(
        f'<th style="color:{player_hex(p)}">{player_name(s,p)}</th>' for p in plist
    ) + '<th class="deal-court-hdr">Court</th></tr>'

    deal_luck_html = f'''
    <div class="card">
      <h2>Deal Luck — Starting Hands by Chapter</h2>
      <p class="deal-legend"><span class="deal-rich">18+pip = pip-rich</span>
        · <span class="deal-poor">≤12pip = pip-poor</span>
        · <span class="deal-note deal-note-bad">⚠ lots of 6s</span>
        · <span class="deal-note deal-note-good">★ lots of 2s</span>
        · <span class="deal-court deal-card" style="border-color:#555">court = available to acquire</span></p>
      <div class="table-wrap">
        <table class="deal-table">
          <thead>{deal_luck_header}</thead>
          <tbody>{"".join(deal_luck_rows)}</tbody>
        </table>
      </div>
    </div>''' if deal_luck_rows else ''

    # Combat — kills, raids, battles initiated/defended
    combat_rows = []
    any_combat = any(s['battles_initiated'][p] or s['battles_defended'][p] for p in plist)
    for p in plist:
        px = player_hex(p)
        kills, losses = s['ship_kills'][p], s['ship_losses'][p]
        kd = s[f'kd_{p}']
        kd_cls = 'deal-rich' if kd >= 1.5 else ('deal-poor' if kd < 0.8 else 'deal-mid')
        combat_rows.append(
            f'<tr><td style="color:{px};font-weight:700">{player_name(s,p)}</td>'
            f'<td class="num">{s["battles_initiated"][p]}</td>'
            f'<td class="num">{s["battles_defended"][p]}</td>'
            f'<td class="num">{kills}</td>'
            f'<td class="num">{losses}</td>'
            f'<td class="num {kd_cls}">{kd}</td>'
            f'<td class="num">{s["raid_successes"][p]}</td>'
            f'<td class="num">{s["ransacks"][p]}</td>'
            f'<td class="num">{s["buildings_destroyed_dealt"][p]}</td>'
            f'<td class="num">{s["buildings_destroyed_taken"][p]}</td></tr>'
        )

    raid_steal_rows = []
    for ch, attacker, defender, card in s['raid_steals']:
        ax, dx = player_hex(attacker), player_hex(defender)
        raid_steal_rows.append(
            f'<div class="timeline-entry" style="border-left:3px solid {ax}">'
            f'<span class="tl-chapter">Ch {ch}</span>'
            f'<span class="tl-player" style="color:{ax}">{player_name(s,attacker)}</span>'
            f'<span class="tl-results">raids <span style="color:{dx};font-weight:700">{player_name(s,defender)}</span> '
            f'for <span style="font-weight:700">{card}</span></span></div>'
        )

    combat_html = f'''
    <div class="card">
      <h2>Combat</h2>
      <p class="deal-legend">Ships have 2 HP — a kill requires two confirmed hits on the same ship. K/D ≥1.5 highlighted green, &lt;0.8 highlighted red.</p>
      <div class="table-wrap">
        <table class="deal-table">
          <thead><tr><th>Player</th><th class="num">Battles init.</th><th class="num">Battles defended</th>
            <th class="num">Kills</th><th class="num">Losses</th><th class="num">K/D</th>
            <th class="num">Card raids won</th><th class="num">Ransacks</th>
            <th class="num">Bldgs hit (dealt)</th><th class="num">Bldgs hit (taken)</th></tr></thead>
          <tbody>{"".join(combat_rows)}</tbody>
        </table>
      </div>
      {f'<div class="timeline" style="margin-top:16px">{"".join(raid_steal_rows)}</div>' if raid_steal_rows else ''}
    </div>''' if any_combat else ''

    # Court cards detail per player
    court_detail_html = []
    for p in plist:
        px = player_hex(p)
        cards = sorted(s['court_cards_by_player'].get(p, set()))
        cards_html = ''.join(f'<span class="card-pill">{c}</span>' for c in cards)
        court_detail_html.append(
            f'<div class="court-player"><div class="court-player-name" style="color:{px}">'
            f'{player_name(s,p)}</div><div class="court-cards">{cards_html}</div></div>'
        )

    # Leader/lore rows
    leader_lore_html = ''
    if s['leaders']:
        rows = []
        for p in plist:
            px = player_hex(p)
            leader = s['leaders'].get(p, '—')
            lore = s['lores'].get(p, '—')
            rows.append(
                f'<tr><td style="color:{px};font-weight:700">{player_name(s,p)}</td>'
                f'<td>{leader}</td><td>{lore}</td></tr>'
            )
        leader_lore_html = f'''
        <div class="card">
          <h2>Leaders & Lores</h2>
          <table class="data-table">
            <thead><tr><th>Player</th><th>Leader</th><th>Lore</th></tr></thead>
            <tbody>{"".join(rows)}</tbody>
          </table>
        </div>'''

    # Score cards HTML
    score_cards = []
    sorted_players = sorted(plist, key=lambda p: s['final_scores'].get(p, 0), reverse=True)
    for rank, p in enumerate(sorted_players):
        score = s['final_scores'].get(p, 0)
        px = player_hex(p)
        pct = int(100 * score / max_score)
        crown = '👑 ' if p == winner else ''
        score_cards.append(
            f'<div class="score-card" style="border-top:4px solid {px}">'
            f'<div class="score-rank">#{rank+1}</div>'
            f'<div class="score-name" style="color:{px}">{crown}{player_name(s,p)}</div>'
            f'<div class="score-num">{score}</div>'
            f'<div class="score-bar-wrap"><div class="score-bar" style="width:{pct}%;background:{px}"></div></div>'
            f'<div class="score-sub">{s["leaders"].get(p,"")}'
            f'{"/" + s["lores"].get(p,"") if s["lores"].get(p) else ""}</div>'
            f'</div>'
        )

    # Options pills
    opts = s.get('options', [])
    opts_html = ' '.join(f'<span class="opt-pill">{o}</span>' for o in opts)

    # Rounds per chapter
    rounds_row = ' '.join(
        f'<div class="rounds-chip">Ch {c}: {s["rounds_per_chapter"].get(c,"?")} rounds</div>'
        for c in chapters
    )

    def winner_badge(p):
        return '&nbsp;<span class="winner-badge">WINNER</span>' if p == winner else ''

    def pip_table_rows():
        rows = []
        for p in plist:
            lead_n = s['turn_roles'][p].get('Lead', 0) + s['turn_roles'][p].get('Surpass', 0)
            total_n = max(1, sum(s['turn_roles'][p].values()))
            rows.append(
                f'<tr>'
                f'<td style="color:{player_hex(p)};font-weight:700">{player_name(s,p)}{winner_badge(p)}</td>'
                f'<td class="num">{s.get(f"pip_total_{p}",0)}</td>'
                f'<td class="num">{s.get(f"pip_avg_{p}",0)}</td>'
                f'<td class="num">{s.get(f"pip_high_{p}",0)}</td>'
                f'<td class="num">{s.get(f"pip_low_{p}",0)}</td>'
                f'<td class="num">{s.get(f"prelude_total_{p}",0)}</td>'
                f'<td class="num">{round(100*lead_n/total_n)}%</td>'
                f'</tr>'
            )
        return ''.join(rows)

    def activity_table_rows():
        rows = []
        for p in plist:
            cells = ''.join(f'<td class="num">{action_totals[p][a]}</td>' for a in ALL_ACTS)
            total = sum(action_totals[p].values())
            rows.append(
                f'<tr><td style="color:{player_hex(p)};font-weight:700">{player_name(s,p)}</td>'
                f'{cells}<td class="num" style="font-weight:800">{total}</td></tr>'
            )
        return ''.join(rows)

    player_grid_class = 'grid-3' if len(plist) == 3 else 'grid-2'

    def player_chart_card(p, chart_id):
        px = player_hex(p)
        return (
            f'<div class="card">'
            f'<h2 style="color:{px}">{player_name(s,p)}</h2>'
            f'<div class="chart-wrap tall"><canvas id="{chart_id}"></canvas></div>'
            f'</div>'
        )

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{s["title"]} — Arcs Analysis</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  :root {{
    --bg: #0F172A; --surface: #1E293B; --surface2: #263248;
    --border: #334155; --text: #E2E8F0; --muted: #94A3B8;
    --radius: 12px;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: var(--bg); color: var(--text); font-family: 'Segoe UI', system-ui, sans-serif;
         font-size: 14px; line-height: 1.5; }}
  a {{ color: var(--muted); }}
  h1 {{ font-size: 2rem; font-weight: 800; letter-spacing: -0.5px; }}
  h2 {{ font-size: 1.1rem; font-weight: 700; color: #CBD5E1; margin-bottom: 14px; letter-spacing: 0.3px; }}
  h3 {{ font-size: 0.85rem; font-weight: 600; color: var(--muted); text-transform: uppercase;
        letter-spacing: 0.8px; margin-bottom: 8px; }}

  header {{ padding: 40px 40px 0; border-bottom: 1px solid var(--border); padding-bottom: 28px;
             background: linear-gradient(180deg, #111827 0%, var(--bg) 100%); }}
  header .meta {{ color: var(--muted); font-size: 0.82rem; margin-top: 6px; }}
  header .opt-pill {{ display: inline-block; background: var(--surface2); border: 1px solid var(--border);
                       border-radius: 20px; padding: 2px 10px; font-size: 0.75rem; margin: 3px 2px; color: var(--muted); }}
  header .rounds-chip {{ display: inline-block; background: var(--surface); border: 1px solid var(--border);
                          border-radius: 6px; padding: 2px 10px; font-size: 0.75rem; margin: 3px 2px; color: var(--muted); }}

  .main {{ padding: 32px 40px; max-width: 1400px; }}

  .grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
  .grid-3 {{ display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 20px; }}
  .grid-auto {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 20px; }}
  .span2 {{ grid-column: span 2; }}
  .span3 {{ grid-column: span 3; }}

  .card {{ background: var(--surface); border-radius: var(--radius); padding: 22px 24px;
            border: 1px solid var(--border); }}
  .card.full {{ grid-column: 1 / -1; }}

  /* Score cards */
  .score-row {{ display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 24px; }}
  .score-card {{ background: var(--surface); border-radius: var(--radius); padding: 20px 24px;
                  border: 1px solid var(--border); flex: 1; min-width: 160px; }}
  .score-rank {{ font-size: 0.75rem; color: var(--muted); font-weight: 700; text-transform: uppercase;
                 letter-spacing: 1px; }}
  .score-name {{ font-size: 1.2rem; font-weight: 800; margin: 4px 0; }}
  .score-num {{ font-size: 3rem; font-weight: 900; line-height: 1; margin: 4px 0 10px; }}
  .score-bar-wrap {{ height: 6px; background: var(--border); border-radius: 3px; overflow: hidden; }}
  .score-bar {{ height: 100%; border-radius: 3px; transition: width 1s; }}
  .score-sub {{ font-size: 0.75rem; color: var(--muted); margin-top: 8px; }}

  /* Chart wrapper */
  .chart-wrap {{ position: relative; }}
  .chart-wrap canvas {{ max-height: 280px; }}
  .chart-wrap.tall canvas {{ max-height: 340px; }}

  /* Data table */
  .data-table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
  .data-table th {{ color: var(--muted); font-weight: 600; text-transform: uppercase;
                    font-size: 0.72rem; letter-spacing: 0.6px; padding: 8px 12px;
                    border-bottom: 1px solid var(--border); text-align: left; }}
  .data-table td {{ padding: 8px 12px; border-bottom: 1px solid var(--border); }}
  .data-table tr:last-child td {{ border-bottom: none; }}
  .data-table tr:hover td {{ background: var(--surface2); }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; font-weight: 600; }}
  .winner-badge {{ background: #854d0e; color: #fef08a; border-radius: 4px;
                   padding: 1px 6px; font-size: 0.7rem; font-weight: 700; }}

  /* Ambition timeline */
  .timeline {{ display: flex; flex-direction: column; gap: 8px; max-height: 380px;
               overflow-y: auto; padding-right: 4px; }}
  .timeline::-webkit-scrollbar {{ width: 4px; }}
  .timeline::-webkit-scrollbar-thumb {{ background: var(--border); border-radius: 2px; }}
  .timeline-entry {{ display: flex; align-items: center; gap: 12px; padding: 7px 12px;
                      background: var(--surface2); border-radius: 8px; font-size: 0.82rem; flex-wrap: wrap; }}
  .tl-chapter {{ color: var(--muted); font-size: 0.72rem; font-weight: 700;
                  text-transform: uppercase; min-width: 32px; flex-shrink: 0; }}
  .tl-player {{ font-weight: 700; min-width: 66px; flex-shrink: 0; }}
  .tl-ambition {{ font-weight: 800; min-width: 66px; flex-shrink: 0; }}
  .tl-results {{ flex: 1; font-size: 0.8rem; }}

  /* Court cards */
  .court-player {{ margin-bottom: 14px; }}
  .court-player-name {{ font-weight: 800; font-size: 0.9rem; margin-bottom: 6px; }}
  .court-cards {{ display: flex; flex-wrap: wrap; gap: 5px; }}
  .card-pill {{ background: var(--surface2); border: 1px solid var(--border);
                border-radius: 20px; padding: 3px 10px; font-size: 0.72rem; color: var(--muted); }}

  /* Deal luck table */
  .deal-table {{ width: 100%; border-collapse: collapse; }}
  .deal-table th, .deal-table td {{ padding: 8px 12px; border-bottom: 1px solid var(--border); vertical-align: top; }}
  .deal-table th {{ font-size: 0.75rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; }}
  .deal-ch {{ color: var(--muted); font-size: 0.75rem; font-weight: 700; white-space: nowrap; }}
  .deal-player-cell {{ min-width: 200px; }}
  .deal-player-header {{ display: flex; align-items: center; gap: 8px; margin-bottom: 6px; font-size: 0.72rem; }}
  .deal-cards {{ display: flex; flex-wrap: wrap; gap: 4px; }}
  .deal-card {{ display: inline-flex; align-items: center; gap: 3px; background: var(--surface2);
                border: 1px solid; border-radius: 5px; padding: 2px 6px; font-size: 0.72rem; font-weight: 600; }}
  .deal-pip {{ color: var(--muted); font-weight: 400; font-size: 0.65rem; }}
  .deal-six {{ opacity: 0.55; }}
  .deal-two {{ outline: 1px solid #F59E0B33; }}
  .deal-court {{ opacity: 0.45; border-style: dashed !important; }}
  .deal-court-hdr {{ color: var(--muted); font-size: 0.7rem; }}
  .deal-rich {{ color: #22C55E; font-weight: 700; }}
  .deal-poor {{ color: #EF4444; font-weight: 700; }}
  .deal-mid  {{ color: var(--muted); }}
  .deal-note {{ font-size: 0.65rem; font-weight: 700; padding: 1px 5px; border-radius: 4px; }}
  .deal-note-bad  {{ background: #EF444420; color: #EF4444; }}
  .deal-note-good {{ background: #F59E0B20; color: #F59E0B; }}
  .deal-legend {{ font-size: 0.72rem; color: var(--muted); margin: 0 0 14px; display: flex; flex-wrap: wrap; gap: 12px; align-items: center; }}

  /* Section divider */
  .section-label {{ font-size: 0.7rem; font-weight: 700; text-transform: uppercase;
                     letter-spacing: 1.2px; color: var(--muted); margin: 32px 0 14px;
                     display: flex; align-items: center; gap: 10px; }}
  .section-label::after {{ content: ''; flex: 1; height: 1px; background: var(--border); }}

  /* Stat pills for quick stats */
  .stat-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 10px; }}
  .stat-item {{ background: var(--surface2); border-radius: 8px; padding: 12px 14px; }}
  .stat-val {{ font-size: 1.6rem; font-weight: 900; line-height: 1; }}
   .stat-lbl {{ font-size: 0.7rem; color: var(--muted); margin-top: 4px; font-weight: 600;
                 text-transform: uppercase; letter-spacing: 0.5px; }}

   /* Ambition result cards */
   .ambition-result {{ background: var(--surface2); border-radius: 8px; padding: 10px 14px;
                        font-size: 0.82rem; }}
   .ar-header {{ display: flex; align-items: center; gap: 8px; margin-bottom: 4px; flex-wrap: wrap; }}
   .ar-player {{ font-weight: 800; }}
   .ar-ambition {{ font-weight: 700; }}
   .ar-status {{ font-weight: 800; font-size: 0.78rem; margin-left: auto; }}
   .ar-ch-breakdown {{ font-size: 0.75rem; margin-bottom: 2px; letter-spacing: 0.5px; }}
   .ar-meta {{ font-size: 0.72rem; color: var(--muted); }}

   footer {{ padding: 24px 40px; color: var(--muted); font-size: 0.75rem; border-top: 1px solid var(--border); }}

  @media (max-width: 900px) {{
    .main, header, footer {{ padding-left: 16px; padding-right: 16px; }}
    .grid-2, .grid-3 {{ grid-template-columns: 1fr; }}
    .score-num {{ font-size: 2rem; }}
  }}
</style>
</head>
<body>

<header>
  <h1>{s["title"]}</h1>
  <div class="meta">
    Arcs v{s["version"]} &nbsp;·&nbsp; {s["num_chapters"]} chapters &nbsp;·&nbsp;
    Source: {source_filename}
  </div>
  <div style="margin-top:10px">{opts_html}</div>
  <div style="margin-top:8px">{rounds_row}</div>
</header>

<div class="main">

  <!-- ── SCORES ── -->
  <div class="section-label">Final Scores</div>
  <div class="score-row">
    {"".join(score_cards)}
  </div>

  {leader_lore_html}

  <!-- ── ACTION ECONOMY ── -->
  <div class="section-label">Action Economy</div>
  <div class="grid-2">

    <div class="card">
      <h2>Turn Role Distribution</h2>
      <div class="chart-wrap">
        <canvas id="chartRoles"></canvas>
      </div>
    </div>

    <div class="card">
      <h2>Pip Throughput</h2>
      <div class="chart-wrap">
        <canvas id="chartPips"></canvas>
      </div>
    </div>

  </div>

  <!-- Pip breakdown table -->
  <div class="card" style="margin-top:20px">
    <h2>Pip Breakdown</h2>
    <table class="data-table">
      <thead>
        <tr>
          <th>Player</th>
          <th class="num">Total Main Pips</th>
          <th class="num">Avg Pips/Turn</th>
          <th class="num">High-Pip Turns (≥3)</th>
          <th class="num">Low-Pip Turns (≤2)</th>
          <th class="num">Prelude Pips</th>
          <th class="num">Lead/Surpass %</th>
        </tr>
      </thead>
      <tbody>
        {pip_table_rows()}
      </tbody>
    </table>
  </div>

  <!-- ── INITIATIVE ── -->
  <div class="section-label">Initiative Control</div>
  <div class="grid-2">

    <div class="card">
      <h2>Initiative Gains by Chapter <span style="font-size:0.75em;color:#F0C040">★ = Seize (costs a card)</span></h2>
      <div class="chart-wrap tall">
        <canvas id="chartInit"></canvas>
      </div>
    </div>

    <div class="card">
      <h2>Main-Action Pips by Chapter</h2>
      <div class="chart-wrap tall">
        <canvas id="chartPipLine"></canvas>
      </div>
    </div>

  </div>

  <!-- ── ACTION TYPES ── -->
  <div class="section-label">Action Types & Activities</div>
  <div class="grid-2">

    <div class="card">
      <h2>Action Type Mix (% of turns)</h2>
      <div class="chart-wrap tall">
        <canvas id="chartActTypes"></canvas>
      </div>
    </div>

    <div class="card">
      <h2>Actions per Chapter — All Players</h2>
      <p style="color:var(--muted);font-size:0.82rem;margin:0 0 10px">
        Groups = chapters &nbsp;·&nbsp; Stacks = players &nbsp;·&nbsp; Segments = action types
      </p>
      <div style="margin-bottom:10px">{combined_player_legend}</div>
      <div class="chart-wrap tall">
        <canvas id="chartCombined"></canvas>
      </div>
    </div>

  </div>

  <!-- ── DETAILED ACTION TIMELINE ── -->
  <div class="section-label">Action Timeline by Player</div>
  <p style="color:var(--muted);font-size:0.82rem;margin-bottom:16px">
    Each bar is one chapter. Stacked segments show action type mix within that chapter.
  </p>
  <div class="{player_grid_class}">
    {"".join(player_chart_card(p, f'chartPlayer{p}') for p in plist)}
  </div>

  <!-- Action totals table -->
  <div class="card" style="margin-top:20px">
    <h2>Action Counts (all chapters)</h2>
    <div style="overflow-x:auto">
    <table class="data-table">
      <thead>
        <tr>
          <th>Player</th>
          {"".join(f'<th class="num" style="color:{ALL_COLORS[i]}">{a}</th>' for i,a in enumerate(ALL_ACTS))}
          <th class="num">Total</th>
        </tr>
      </thead>
      <tbody>
        {activity_table_rows()}
      </tbody>
    </table>
    </div>
  </div>

  <!-- ── BUILDINGS ── -->
  <div class="section-label">Infrastructure</div>
  <div class="grid-2">

    <div class="card">
      <h2>Buildings Built</h2>
      <div class="chart-wrap">
        <canvas id="chartBuildings"></canvas>
      </div>
    </div>

    <div class="card">
      <h2>Court Cards Secured</h2>
      <div class="chart-wrap">
        <canvas id="chartCourt"></canvas>
      </div>
    </div>

  </div>

  <!-- ── DEAL LUCK ── -->
  {deal_luck_html}

  <!-- ── COMBAT ── -->
  {combat_html}

  <!-- ── COURT DETAIL ── -->
  <div class="card" style="margin-top:20px">
    <h2>Court Card Details</h2>
    {"".join(court_detail_html)}
  </div>

  <!-- ── RESOURCE ECONOMY ── -->
  <div class="section-label">Resource Economy</div>
  <div class="grid-2">

    <div class="card">
      <h2>Resources Gained (Tax)</h2>
      <div class="chart-wrap">
        <canvas id="chartResGained"></canvas>
      </div>
    </div>

    <div class="card">
      <h2>Resources Spent</h2>
      <div class="chart-wrap">
        <canvas id="chartResSpent"></canvas>
      </div>
    </div>

  </div>

  <!-- ── AMBITIONS ── -->
  <div class="section-label">Ambitions</div>
  <div class="grid-2">

    <div class="card">
      <h2>Ambitions Declared</h2>
      <div class="chart-wrap">
        <canvas id="chartAmbitions"></canvas>
      </div>
    </div>

    <div class="card">
      <h2>Declaration Timeline</h2>
      <div class="timeline">
        {"".join(ambition_timeline)}
      </div>
    </div>

  </div>


</div><!-- /main -->

<footer>
  Generated by arcs_analyze.py &nbsp;·&nbsp; Arcs: Conflict and Collapse in the Reach
</footer>

<script>
Chart.defaults.color = '#94A3B8';
Chart.defaults.borderColor = '#334155';
Chart.defaults.font.family = "'Segoe UI', system-ui, sans-serif";
Chart.defaults.font.size = 12;

const pnames = {jc(pnames)};
const phexes = {jc(phexes)};

// ── Turn role distribution ──
new Chart(document.getElementById('chartRoles'), {{
  type: 'bar',
  data: {{
    labels: pnames,
    datasets: {jc(role_datasets)},
  }},
  options: {{
    responsive: true, maintainAspectRatio: true,
    plugins: {{ legend: {{ position: 'bottom', labels: {{ boxWidth: 12, padding: 10 }} }} }},
    scales: {{
      x: {{ stacked: true, grid: {{ display: false }} }},
      y: {{ stacked: true, grid: {{ color: '#334155' }}, ticks: {{ stepSize: 5 }} }},
    }},
  }},
}});

// ── Pip throughput ──
new Chart(document.getElementById('chartPips'), {{
  type: 'bar',
  data: {{
    labels: pnames,
    datasets: [
      {{ label: 'Main Pips (total)', data: {jc(pip_totals)}, backgroundColor: phexes }},
      {{ label: 'Prelude Pips (total)', data: {jc(prelude_totals)},
         backgroundColor: phexes.map(h => h + '66') }},
    ],
  }},
  options: {{
    responsive: true, maintainAspectRatio: true,
    plugins: {{ legend: {{ position: 'bottom', labels: {{ boxWidth: 12, padding: 10 }} }} }},
    scales: {{
      x: {{ grouped: true, grid: {{ display: false }} }},
      y: {{ grid: {{ color: '#334155' }} }},
    }},
  }},
}});

// ── Initiative by chapter ──
new Chart(document.getElementById('chartInit'), {{
  type: 'bar',
  data: {{ labels: {jc(chapter_labels)}, datasets: {jc(init_datasets)} }},
  options: {{
    responsive: true, maintainAspectRatio: true,
    plugins: {{ legend: {{ position: 'bottom', labels: {{ boxWidth: 12, padding: 10 }} }} }},
    scales: {{
      x: {{ stacked: true, grid: {{ display: false }} }},
      y: {{ stacked: true, grid: {{ color: '#334155' }}, ticks: {{ stepSize: 1 }} }},
    }},
  }},
}});

// ── Pip line by chapter ──
new Chart(document.getElementById('chartPipLine'), {{
  type: 'line',
  data: {{ labels: {jc(chapter_labels)}, datasets: {jc(pip_chapter_datasets)} }},
  options: {{
    responsive: true, maintainAspectRatio: true,
    plugins: {{ legend: {{ position: 'bottom', labels: {{ boxWidth: 12, padding: 10 }} }} }},
    scales: {{
      x: {{ grid: {{ display: false }} }},
      y: {{ grid: {{ color: '#334155' }} }},
    }},
  }},
}});

// ── Action type mix — horizontal stacked % bar ──
new Chart(document.getElementById('chartActTypes'), {{
  type: 'bar',
  data: {{
    labels: pnames,
    datasets: {jc(act_type_datasets)},
  }},
  options: {{
    responsive: true, maintainAspectRatio: true,
    indexAxis: 'y',
    plugins: {{
      legend: {{ position: 'bottom', labels: {{ boxWidth: 12, padding: 10 }} }},
      tooltip: {{
        callbacks: {{
          label: ctx => ` ${{ctx.dataset.label}}: ${{ctx.parsed.x}}%`,
        }},
      }},
    }},
    scales: {{
      x: {{
        stacked: true,
        max: 100,
        grid: {{ color: '#334155' }},
        ticks: {{ callback: v => v + '%' }},
      }},
      y: {{ stacked: true, grid: {{ display: false }} }},
    }},
  }},
}});

// ── Combined grouped-stacked actions per chapter ──
// Plugin draws one border rect around each complete player stack
const stackBorderPlugin = {{
  id: 'stackBorder',
  afterDatasetsDraw(chart) {{
    const {{ctx}} = chart;
    const stackMap = {{}};
    chart.data.datasets.forEach((ds, di) => {{
      const meta = chart.getDatasetMeta(di);
      if (meta.hidden) return;
      meta.data.forEach((bar, xi) => {{
        const key = `${{ds.stack}}||${{xi}}`;
        if (!stackMap[key]) stackMap[key] = {{
          top: Infinity, bottom: -Infinity,
          x: bar.x, width: bar.width, color: ds.borderColor,
        }};
        if (Math.abs(bar.base - bar.y) > 0.5) {{
          stackMap[key].top    = Math.min(stackMap[key].top,    bar.y);
          stackMap[key].bottom = Math.max(stackMap[key].bottom, bar.base);
        }}
      }});
    }});
    ctx.save();
    Object.values(stackMap).forEach(({{top, bottom, x, width, color}}) => {{
      if (top === Infinity) return;
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.strokeRect(x - width / 2 + 1, top, width - 2, bottom - top);
    }});
    ctx.restore();
  }},
}};

new Chart(document.getElementById('chartCombined'), {{
  type: 'bar',
  data: {{ labels: {jc(chapter_labels)}, datasets: {jc(combined_chapter_datasets())} }},
  options: {{
    responsive: true, maintainAspectRatio: true,
    plugins: {{
      legend: {{
        position: 'bottom',
        labels: {{
          boxWidth: 12, padding: 10,
          filter: item => item.text !== '',
        }},
      }},
      tooltip: {{
        callbacks: {{
          title: ctx => `${{ctx[0].dataset.stack}} — ${{ctx[0].label}}`,
          label: ctx => ctx.dataset.label ? ` ${{ctx.dataset.label}}: ${{ctx.raw}}` : null,
        }},
      }},
    }},
    scales: {{
      x: {{ stacked: true, grid: {{ display: false }} }},
      y: {{ stacked: true, grid: {{ color: '#334155' }}, beginAtZero: true }},
    }},
  }},
  plugins: [stackBorderPlugin],
}});

// ── Per-player chapter timelines ──
{chr(10).join(
    f"""new Chart(document.getElementById('chartPlayer{p}'), {{
  type: 'bar',
  data: {{ labels: {jc(chapter_labels)}, datasets: {jc(player_chapter_datasets(p))} }},
  options: {{
    responsive: true, maintainAspectRatio: true,
    plugins: {{ legend: {{ position: 'bottom', labels: {{ boxWidth: 10, padding: 8, font: {{ size: 11 }} }} }} }},
    scales: {{
      x: {{ stacked: true, grid: {{ display: false }} }},
      y: {{ stacked: true, grid: {{ color: '#334155' }}, beginAtZero: true }},
    }},
  }},
}});"""
    for p in plist
)}

// ── Buildings ──
new Chart(document.getElementById('chartBuildings'), {{
  type: 'bar',
  data: {{ labels: pnames, datasets: {jc(building_datasets)} }},
  options: {{
    responsive: true, maintainAspectRatio: true,
    plugins: {{ legend: {{ position: 'bottom', labels: {{ boxWidth: 12, padding: 10 }} }} }},
    scales: {{
      x: {{ grid: {{ display: false }} }},
      y: {{ grid: {{ color: '#334155' }}, ticks: {{ stepSize: 1 }} }},
    }},
  }},
}});

// ── Court cards ──
new Chart(document.getElementById('chartCourt'), {{
  type: 'bar',
  data: {{
    labels: pnames,
    datasets: [{{ label: 'Unique court cards', data: {jc(court_counts)}, backgroundColor: phexes }}],
  }},
  options: {{
    responsive: true, maintainAspectRatio: true,
    indexAxis: 'y',
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ grid: {{ color: '#334155' }}, ticks: {{ stepSize: 1 }} }},
      y: {{ grid: {{ display: false }} }},
    }},
  }},
}});

// ── Resource economy ──
const resOpts = {{
  responsive: true, maintainAspectRatio: true,
  plugins: {{ legend: {{ position: 'bottom', labels: {{ boxWidth: 12, padding: 10 }} }} }},
  scales: {{
    x: {{ stacked: true, grid: {{ display: false }} }},
    y: {{ stacked: true, grid: {{ color: '#334155' }}, beginAtZero: true, ticks: {{ stepSize: 1 }} }},
  }},
}};
new Chart(document.getElementById('chartResGained'), {{
  type: 'bar',
  data: {{ labels: pnames, datasets: {jc(res_gained_datasets)} }},
  options: resOpts,
}});
new Chart(document.getElementById('chartResSpent'), {{
  type: 'bar',
  data: {{ labels: pnames, datasets: {jc(res_spent_datasets)} }},
  options: resOpts,
}});

// ── Ambitions ──
new Chart(document.getElementById('chartAmbitions'), {{
  type: 'bar',
  data: {{ labels: pnames, datasets: {jc(ambition_datasets)} }},
  options: {{
    responsive: true, maintainAspectRatio: true,
    plugins: {{ legend: {{ position: 'bottom', labels: {{ boxWidth: 12, padding: 10 }} }} }},
    scales: {{
      x: {{ stacked: true, grid: {{ display: false }} }},
      y: {{ stacked: true, grid: {{ color: '#334155' }}, ticks: {{ stepSize: 1 }} }},
    }},
  }},
}});

</script>
</body>
</html>'''
    return html


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 arcs_analyze.py <replay.html>")
        sys.exit(1)

    src = Path(sys.argv[1])
    if not src.exists():
        print(f"File not found: {src}")
        sys.exit(1)

    print(f"Reading {src}...")
    content = src.read_text(encoding='utf-8', errors='ignore')

    lobby = parse_lobby(content)
    lines = parse_replay_log(content)

    if not lines:
        print("No replay log found in this file.")
        sys.exit(1)

    players_map = lobby.get('players', {})
    if not players_map:
        print("Could not find player names in lobby data.")
        sys.exit(1)

    print(f"Players: {players_map}")
    print(f"Analyzing {len(lines)} log lines...")

    print("Extracting scoring data via Playwright...")
    scoring_lines = extract_scoring_playwright(str(src))
    print(f"  Found {len(scoring_lines)} scoring lines")
    scoring_data = parse_scoring_lines(scoring_lines)

    stats = analyze_log(lines, players_map, scoring_data=scoring_data)
    # Build scoring lookup for HTML generation
    scoring_lookup = {}
    for sd in scoring_data:
        color_abbr = FULL_TO_ABBREV.get(sd['player'], sd['player'][0].upper())
        key = (sd['chapter'], color_abbr, sd['ambition'])
        scoring_lookup[key] = sd
    stats['scoring_lookup'] = scoring_lookup
    stats['title'] = lobby.get('title', 'Arcs Game')
    stats['version'] = lobby.get('version', '?')
    stats['options'] = lobby.get('options', [])

    out_path = src.with_name(src.stem + '_analysis.html')
    html = generate_html(stats, src.name)
    out_path.write_text(html, encoding='utf-8')

    winner_name = stats['players'].get(stats.get('winner', ''), '?')
    print(f"Done! Winner: {winner_name}")
    print(f"Report: {out_path}")


if __name__ == '__main__':
    main()
