from decimal import Decimal as D
from app.normalize import instant
from app.modules.m8_identity.rules import rebuild, person_stays, bed_board, dedupe, person_index

END = instant('2026-11-18T00:00:00+08:00')

def ev(eid, patient, t, kind, bed='001', scope='ER', line=None):
    return dict(id=eid, patient=patient, bed=bed, ward='一病区',
                time=instant('2026-11-16T' + t + ':00+08:00'), type=kind,
                version=1, scope=scope, _line=line if line is not None else int(eid[-1]) + 1)

def link(er, ip, lid='L0001'):
    return [{'link_id': lid, 'er_patient': er, 'ip_patient': ip, 'status': 'active'}]

def test_without_link_two_chains_keep_gap():
    rows = [ev('E1', 'E100', '08:00', 'admit'), ev('E2', 'E100', '12:00', 'discharge'),
            ev('I1', 'I200', '13:00', 'admit', scope='IP'), ev('I2', 'I200', '18:00', 'discharge', scope='IP')]
    intervals, retained, bad, splices = rebuild(rows, [], END)
    assert len(splices) == 0 and len(intervals) == 2
    # 12:00-13:00 的空档被当作可收治：两段之间留出缺口
    assert intervals[0]['end'] == instant('2026-11-16T12:00:00+08:00')
    assert intervals[1]['start'] == instant('2026-11-16T13:00:00+08:00')
    assert all(x['link_id'] == '' and x['basis'].startswith('无对应') for x in intervals)

def test_same_bed_splice_closes_gap_and_los():
    rows = [ev('E1', 'E100', '08:00', 'admit'), ev('E2', 'E100', '12:00', 'discharge'),
            ev('I1', 'I200', '13:00', 'admit', scope='IP'), ev('I2', 'I200', '18:00', 'discharge', scope='IP')]
    intervals, retained, bad, splices = rebuild(rows, link('E100', 'I200'), END)
    assert len(splices) == 1 and splices[0]['link_id'] == 'L0001'
    # 同床空档 12:00-13:00 接回：ER 段、空档补段、IP 段首尾相接，床位连续占用
    same_bed = sorted((x for x in intervals if x['bed'] == '001'), key=lambda x: x['start'])
    assert len(same_bed) == 3
    for left, right in zip(same_bed, same_bed[1:]):
        assert left['end'] == right['start']
    assert same_bed[0]['start'] == instant('2026-11-16T08:00:00+08:00')
    assert same_bed[-1]['end'] == instant('2026-11-16T18:00:00+08:00')
    stays = {s['person']: s['los_minutes'] for s in person_stays(intervals)}
    assert stays['ER:E100'] == D('600')  # 08:00-18:00 完整住院时长
    assert all(x['basis'] == '对应关系 L0001' for x in intervals)

def test_touching_endpoint_splice():
    rows = [ev('E1', 'E100', '08:00', 'admit'), ev('E2', 'E100', '12:00', 'discharge'),
            ev('I1', 'I200', '12:00', 'admit', scope='IP'), ev('I2', 'I200', '18:00', 'discharge', scope='IP')]
    intervals, _, _, splices = rebuild(rows, link('E100', 'I200'), END)
    assert len(splices) == 1
    assert intervals[0]['end'] == intervals[1]['start']  # 半开区间端点相接，不重复计数

def test_cross_bed_transfer_chain():
    rows = [ev('E1', 'E100', '08:00', 'admit', '001'), ev('E2', 'E100', '12:00', 'transfer', '001'),
            ev('I1', 'I200', '12:00', 'admit', '002', 'IP'), ev('I2', 'I200', '18:00', 'discharge', '002', 'IP')]
    intervals, _, bad, splices = rebuild(rows, link('E100', 'I200'), END)
    assert bad == [] and len(splices) == 1
    beds = sorted({x['bed'] for x in intervals})
    assert beds == ['001', '002']
    # 前床在入院时刻关闭，人链时长完整
    assert person_stays(intervals)[0]['los_minutes'] == D('600')

def test_wrong_link_merges_two_people_and_hides_conflict():
    # 两个不同的人在同一张床时间重叠，本应判双占
    rows = [ev('E1', 'E100', '10:00', 'admit', '104'), ev('E2', 'E100', '15:00', 'discharge', '104'),
            ev('I1', 'I200', '14:00', 'admit', '104', 'IP'), ev('I2', 'I200', '19:00', 'discharge', '104', 'IP')]
    from app.modules.m4_conflicts.rules import conflicts
    before = conflicts([{**x, 'patient': x['person']} for x in rebuild(rows, [], END)[0]])
    assert len(before) == 1 and before[0]['minutes'] == 60
    # 错并为同一人后双占消失（误判）
    merged, _, _, _ = rebuild(rows, link('E100', 'I200'), END)
    assert conflicts([{**x, 'patient': x['person']} for x in merged]) == []
    assert len({x['person'] for x in merged}) == 1

def test_correction_restores_two_chains():
    rows = [ev('E1', 'E100', '10:00', 'admit', '104'), ev('E2', 'E100', '15:00', 'discharge', '104'),
            ev('I1', 'I200', '14:00', 'admit', '104', 'IP'), ev('I2', 'I200', '19:00', 'discharge', '104', 'IP')]
    from app.modules.m4_conflicts.rules import conflicts
    # 更正 = 生效对应集合中移除错误关联，重算即回到两条链
    restored, _, _, splices = rebuild(rows, [], END)
    assert splices == [] and len({x['person'] for x in restored}) == 2
    assert len(conflicts([{**x, 'patient': x['person']} for x in restored])) == 1
    # 时长回到各自链：E100=300，I200=300
    los = {s['person']: s['los_minutes'] for s in person_stays(restored)}
    assert los == {'ER:E100': D('300'), 'IP:I200': D('300')}

def test_orphan_and_balance():
    rows = [ev('E1', 'E100', '11:00', 'discharge')]
    intervals, retained, bad, _ = rebuild(rows, [], END)
    assert intervals == [] and retained == [] and bad[0]['code'] == 'ORPHAN'

def test_orphan_er_discharge_not_a_splice_anchor():
    # ER 只有一条无入院的出院（隔离），其后 IP 入院不得把它当接续点
    rows = [ev('E1', 'E100', '09:00', 'discharge'),
            ev('I1', 'I200', '10:00', 'admit', scope='IP'), ev('I2', 'I200', '18:00', 'discharge', scope='IP')]
    intervals, retained, bad, splices = rebuild(rows, link('E100', 'I200'), END)
    assert splices == []
    assert any(q['code'] == 'ORPHAN' for q in bad)
    # IP 链独立成段，未补 09:00-10:00 空档
    ip_seg = [x for x in intervals if x['scope'] == 'IP']
    assert len(ip_seg) == 1 and ip_seg[0]['start'] == instant('2026-11-16T10:00:00+08:00')

def test_open_segment_preserved():
    rows = [ev('I1', 'I200', '08:00', 'admit', scope='IP')]
    intervals, _, _, _ = rebuild(rows, [], END)
    assert intervals[0]['open'] is True and intervals[0]['end'] == END

def test_dedupe_same_version_conflict():
    rows = [ev('1', 'E100', '08:00', 'admit', line=2), ev('1', 'E100', '09:00', 'admit', line=3)]
    chosen, bad = dedupe(rows)
    assert chosen == [] and len(bad) == 2 and all(q['code'] == 'CONFLICT' for q in bad)

def test_board_carries_basis():
    rows = [ev('E1', 'E100', '08:00', 'admit'), ev('E2', 'E100', '12:00', 'discharge')]
    board = bed_board(rebuild(rows, link('E100', 'I200'), END)[0])
    assert board[0]['basis'] == '对应关系 L0001' and board[0]['link_id'] == 'L0001'
    assert board[0]['person'] == 'ER:E100'

def test_person_index_unknown_is_own_person():
    er_map, ip_map = person_index(link('E100', 'I200'))
    assert er_map['E100'][0] == ip_map['I200'][0] == 'ER:E100'
