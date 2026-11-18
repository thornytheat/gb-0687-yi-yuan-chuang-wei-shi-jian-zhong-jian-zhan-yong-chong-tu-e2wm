from .rules import rebuild, person_stays, bed_board
from app.modules.m4_conflicts.rules import conflicts
from app.modules.m5_calendar.rules import calendar
from app.modules.m6_reconcile.rules import reconcile
from app import identity

def execute(ctx):
    links = identity.active_links()
    # 从 M2 全量采信事件独立重建：M3 会把断链出院按 ORPHAN 隔离，身份接回必须看到原始全量
    source = ctx.get('m2_records', ctx['records'])
    intervals, retained, bad, splices = rebuild(source, links, ctx['observation_end'])
    # M8 独立从 M2 全量重建，隔离原因单独列示，不并入 M3-M7 的全局 quarantine，避免重复计数
    ctx['identity_quarantine'] = bad
    ctx['identity_intervals'] = intervals
    ctx['identity_links'] = links
    ctx['identity_splices'] = splices
    ctx['identity_stays'] = person_stays(intervals)
    ctx['identity_conflicts'] = conflicts([{**x, 'patient': x['person']} for x in intervals])
    ctx['identity_calendar'] = calendar(intervals)
    ctx['identity_reconciliation'] = reconcile(intervals, ctx['identity_calendar'])
    ctx['identity_board'] = bed_board(intervals)
    # 身份重建阶段自身守恒：M2 采信事件 = 重放采信 + 本阶段隔离（M3-M7 的隔离不计入）
    base = ctx.get('m2_count', len(ctx['records']))
    ctx['identity_balance'] = {'m2_events': base, 'replayed': len(retained),
                               'quarantined': len(bad), 'balanced': base == len(retained) + len(bad)}
    ctx['stages'].append({'title': '身份接回', 'rows': ctx['identity_board'],
                          'metrics': {'生效对应': len(links), '接回点': len(splices),
                                      '人链': len(ctx['identity_stays']), '双占段': len(ctx['identity_conflicts']),
                                      '守恒': '通过' if ctx['identity_balance']['balanced'] else '差异'}})
    return ctx
