from collections import defaultdict
from decimal import Decimal as D
from app.reasons import rejected

ORDER = {'discharge': 0, 'transfer': 1, 'admit': 2}
# 同刻先出后入；跨编号域同刻先处理急诊（ER）再处理住院（IP），保证转床先闭前床
SCOPE_ORDER = {'ER': 0, 'IP': 1}

def dedupe(rows):
    """同事件号取最高版本；同版矛盾全部隔离；完全重复/旧版单列。"""
    keys = defaultdict(list)
    for row in rows:
        keys[row['id']].append(row)
    chosen, bad = [], []
    for _, group in sorted(keys.items()):
        version = max(x['version'] for x in group)
        latest = [x for x in group if x['version'] == version]
        sig = lambda x: (x['patient'], x['bed'], x['ward'], x['time'], x['type'], x.get('scope', 'IP'))
        if len({sig(x) for x in latest}) > 1:
            bad.extend(rejected(x, 'CONFLICT', '同版事件矛盾') for x in group)
            continue
        pick = min(latest, key=lambda x: x['_line'])
        chosen.append(pick)
        bad.extend(rejected(x, 'DUPLICATE', '重复或旧版事件') for x in group if x is not pick)
    return chosen, bad

def person_index(links):
    """er_patient -> person；ip_patient -> person。未关联编号自成一人。"""
    er_map, ip_map = {}, {}
    for link in links:
        person = f"ER:{link['er_patient']}"
        er_map[link['er_patient']] = (person, link['link_id'])
        ip_map[link['ip_patient']] = (person, link['link_id'])
    return er_map, ip_map

def _identity(row, er_map, ip_map):
    if row.get('scope', 'IP') == 'ER':
        return er_map.get(row['patient'], (f"ER:{row['patient']}", None))
    return ip_map.get(row['patient'], (f"IP:{row['patient']}", None))

def rebuild(rows, links, observation_end):
    """按住院人回放全量事件，跨编号域 admit 视为同床接续，接回断成两截的占床链。"""
    events, bad = dedupe(rows)
    er_map, ip_map = person_index(links)
    groups = defaultdict(list)
    basis = {}
    for row in events:
        person, link_id = _identity(row, er_map, ip_map)
        groups[person].append(row)
        basis[person] = link_id
    intervals, retained, splices = [], [], []
    for person, evs in sorted(groups.items()):
        link_id = basis[person]
        active = None
        last = None  # 同一条人链上最近一次有效事件，用于跨编号域接回（ORPHAN 不可作锚点）
        for row in sorted(evs, key=lambda x: (x['time'], ORDER[x['type']], SCOPE_ORDER.get(x.get('scope', 'IP'), 1), x['id'])):
            kind, scope = row['type'], row.get('scope', 'IP')
            if kind == 'admit':
                if active is not None:
                    if scope == 'IP' and active.get('scope', 'IP') == 'ER':
                        # 跨编号域入院（ER 链仍开放，含 ER 转床后未归床）：
                        # 在入院时刻关闭 ER 最后占床段（半开端点相接），再打开住院床，人链不断
                        if row['time'] > active['time']:
                            intervals.append(_segment(person, active, row['time'], False, link_id))
                        splices.append({'person': person, 'bed': row['bed'], 'at': row['time'],
                                        'link_id': link_id, 'er_patient': active['patient'], 'ip_patient': row['patient']})
                        active = row
                        retained.append(row)
                        last = row
                        continue
                    bad.append(rejected(row, 'STATE', '已有占床段'))
                    continue
                if (scope == 'IP' and link_id and last is not None
                        and last.get('scope', 'IP') == 'ER' and last['type'] in ('discharge', 'transfer')
                        and row['time'] >= last['time']):
                    # ER 链已关闭（出院/转床），住院号入院把断成两截的人链接回：
                    # 同床补回两段之间的空档；异床则各自闭区间、人链连续
                    splices.append({'person': person, 'bed': row['bed'], 'at': row['time'],
                                    'link_id': link_id, 'er_patient': last['patient'], 'ip_patient': row['patient']})
                    if row['bed'] == last['bed'] and row['time'] > last['time']:
                        intervals.append(_segment(person, last, row['time'], False, link_id))
                    active = row
                    retained.append(row)
                    last = row
                    continue
                active = row
            else:
                if active is None:
                    # 隔离且不更新 last：无入院的出院不能作为跨域接续锚点；原始行仍保留在 bad 凭据中
                    bad.append(rejected(row, 'ORPHAN', '没有入院事件'))
                    continue
                if kind == 'discharge' and row['bed'] != active['bed']:
                    bad.append(rejected(row, 'STATE', '出院床号不符'))
                    continue
                if row['time'] > active['time']:
                    intervals.append(_segment(person, active, row['time'], False, link_id))
                active = row if kind == 'transfer' else None
            retained.append(row)
            last = row
        if active is not None and observation_end > active['time']:
            intervals.append(_segment(person, active, observation_end, True, link_id))
    intervals.sort(key=lambda x: (x['bed'], x['start'], x['person']))
    return intervals, retained, bad, splices

def _segment(person, anchor, end, open_, link_id):
    return {'person': person, 'patient': anchor['patient'], 'bed': anchor['bed'], 'ward': anchor['ward'],
            'start': anchor['time'], 'end': end, 'open': open_, 'source': anchor['id'],
            'scope': anchor.get('scope', 'IP'), 'link_id': link_id or '',
            'basis': f"对应关系 {link_id}" if link_id else '无对应（原始编号）'}

def person_stays(intervals):
    """住院人视角的住院时长：同一张人链上的占床分钟合计。"""
    total = defaultdict(lambda: D(0))
    beds = defaultdict(set)
    for item in intervals:
        total[item['person']] += D(str((item['end'] - item['start']).total_seconds())) / 60
        beds[item['person']].add(item['bed'])
    return [{'person': person, 'los_minutes': minutes, 'beds': ' / '.join(sorted(beds[person])),
             'link_id': next((x['link_id'] for x in intervals if x['person'] == person and x['link_id']), '')}
            for person, minutes in sorted(total.items())]

def bed_board(intervals):
    """看板：每张床当前/历史占用者及其依据的对应关系。"""
    beds = defaultdict(list)
    for item in intervals:
        beds[item['bed']].append(item)
    board = []
    for bed in sorted(beds):
        for item in sorted(beds[bed], key=lambda x: x['start']):
            board.append({'bed': bed, 'ward': item['ward'], 'person': item['person'],
                          'patient': item['patient'], 'scope': item['scope'], 'start': item['start'],
                          'end': item['end'], 'open': item['open'], 'link_id': item['link_id'],
                          'basis': item['basis']})
    return board
