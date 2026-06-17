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

def analyze_log(lines, players_map):
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

    for l in lines:
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
            cm = re.match(role + r'\(\w+,\s*ActionCard\([^,]+,\s*(\d+)', l)
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

        prev_line = l

    # ── Derived ──
    for p in plist:
        pips = s['main_pips'][p]
        s[f'pip_total_{p}'] = sum(pips)
        s[f'pip_avg_{p}'] = round(sum(pips) / len(pips), 1) if pips else 0
        s[f'pip_high_{p}'] = sum(1 for x in pips if x >= 3)
        s[f'pip_low_{p}'] = sum(1 for x in pips if x <= 2)
        s[f'prelude_total_{p}'] = sum(s['prelude_pips'][p])

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
    ambition_timeline = []
    for decl in s['ambition_declarations']:
        pname = player_name(s, decl['player'])
        px = player_hex(decl['player'])
        ax = AMBITION_HEX.get(decl['ambition'], '#888')
        vox = ' (via Vox)' if decl['via_vox'] else ''
        ambition_timeline.append(
            f'<div class="timeline-entry" style="border-left:3px solid {ax}">'
            f'<span class="tl-chapter">Ch {decl["chapter"]}</span>'
            f'<span class="tl-player" style="color:{px}">{pname}</span>'
            f'<span class="tl-ambition" style="color:{ax}">{decl["ambition"]}</span>'
            f'<span class="tl-note">{vox}</span>'
            f'</div>'
        )

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
  .timeline-entry {{ display: flex; align-items: center; gap: 10px; padding: 8px 12px;
                      background: var(--surface2); border-radius: 8px; font-size: 0.82rem; }}
  .tl-chapter {{ color: var(--muted); font-size: 0.72rem; font-weight: 700;
                  text-transform: uppercase; min-width: 34px; }}
  .tl-player {{ font-weight: 700; min-width: 70px; }}
  .tl-ambition {{ font-weight: 800; min-width: 70px; }}
  .tl-note {{ color: var(--muted); font-size: 0.75rem; }}

  /* Court cards */
  .court-player {{ margin-bottom: 14px; }}
  .court-player-name {{ font-weight: 800; font-size: 0.9rem; margin-bottom: 6px; }}
  .court-cards {{ display: flex; flex-wrap: wrap; gap: 5px; }}
  .card-pill {{ background: var(--surface2); border: 1px solid var(--border);
                border-radius: 20px; padding: 3px 10px; font-size: 0.72rem; color: var(--muted); }}

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

    stats = analyze_log(lines, players_map)
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
