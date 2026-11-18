from datetime import datetime, timezone
from app.errors import DomainError
from app.store import connection

def init():
    with connection() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS identity_links(
            link_id TEXT PRIMARY KEY,
            er_patient TEXT NOT NULL,
            ip_patient TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('active','corrected')),
            replaces TEXT,
            created TEXT NOT NULL,
            source_run TEXT,
            note TEXT NOT NULL DEFAULT '')""")
        # 同一急诊号、同一住院号在任一时刻只能有一条生效对应
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_link_er_active ON identity_links(er_patient) WHERE status='active'")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_link_ip_active ON identity_links(ip_patient) WHERE status='active'")
        db.execute("CREATE INDEX IF NOT EXISTS idx_link_pair ON identity_links(er_patient,ip_patient)")

def _row(db, sql, args):
    cur = db.execute(sql, args)
    return cur.fetchone()

def known_patients(run_id=None):
    """从已保存批次或当前事件快照取 (patient, scope) 集合，用于校验编号来源。"""
    if not run_id:
        return set()
    from app import store
    result = store.fetch(run_id)
    return {(r["patient"], r.get("scope", "IP")) for r in result.get("records", [])}

def submit_link(er_patient, ip_patient, run_id=None, note=""):
    er_patient, ip_patient, note = str(er_patient).strip(), str(ip_patient).strip(), str(note or "").strip()
    if not er_patient or not ip_patient:
        raise DomainError("MISSING", "急诊号与住院号均必填")
    known = known_patients(run_id)
    if known:
        if (er_patient, "ER") not in known:
            raise DomainError("LINK_UNKNOWN", "急诊号不在该批次事件中或编号域不是ER")
        if (ip_patient, "IP") not in known:
            raise DomainError("LINK_UNKNOWN", "住院号不在该批次事件中或编号域不是IP")
    now = datetime.now(timezone.utc).isoformat()
    with connection() as db:
        init()
        # 幂等：同一对编号已有生效关联，重试不新增
        same = _row(db, "SELECT * FROM identity_links WHERE er_patient=? AND ip_patient=? AND status='active'", (er_patient, ip_patient))
        if same is not None:
            return dict(same), False
        # 该 ER 号已指向别的住院号 -> 视为更正
        old_er = _row(db, "SELECT * FROM identity_links WHERE er_patient=? AND status='active'", (er_patient,))
        old_ip = _row(db, "SELECT * FROM identity_links WHERE ip_patient=? AND status='active'", (ip_patient,))
        replaces = None
        if old_er is not None and old_ip is not None and old_er["link_id"] != old_ip["link_id"]:
            raise DomainError("LINK_CONFLICT", "两个编号各自存在不同生效对应，无法合并，请先逐条更正")
        if old_er is not None:
            replaces = old_er["link_id"]
        elif old_ip is not None:
            replaces = old_ip["link_id"]
        if replaces is not None:
            cur = db.execute("UPDATE identity_links SET status='corrected' WHERE link_id=?", (replaces,))
            if cur.rowcount != 1:
                raise DomainError("LINK_CONFLICT", "对应关系状态已被并发修改")
        link_id = _next_id(db)
        db.execute("INSERT INTO identity_links(link_id,er_patient,ip_patient,status,replaces,created,source_run,note) VALUES(?,?,?,?,?,?,?,?)",
                   (link_id, er_patient, ip_patient, "active", replaces, now, run_id, note))
        row = _row(db, "SELECT * FROM identity_links WHERE link_id=?", (link_id,))
        return dict(row), True

def _next_id(db):
    n = db.execute("SELECT COUNT(*) AS c FROM identity_links").fetchone()["c"] + 1
    # 历史行只增不删，序号天然不复用
    return f"L{n:04d}"

def active_links():
    with connection() as db:
        init()
        rows = db.execute("SELECT * FROM identity_links WHERE status='active' ORDER BY link_id").fetchall()
        return [dict(r) for r in rows]

def unlink(link_id, note=""):
    """撤销错误对应：只把生效行置为 corrected 并留痕，不删除任何原始登记。"""
    note = str(note or "").strip()
    with connection() as db:
        init()
        row = _row(db, "SELECT * FROM identity_links WHERE link_id=? AND status='active'", (link_id,))
        if row is None:
            raise DomainError("NOT_FOUND", "生效对应关系不存在或已更正", 404)
        merged = note or row["note"]
        db.execute("UPDATE identity_links SET status='corrected', note=? WHERE link_id=?", (merged, link_id))
        return dict(_row(db, "SELECT * FROM identity_links WHERE link_id=?", (link_id,)))

def all_links():
    with connection() as db:
        init()
        rows = db.execute("SELECT * FROM identity_links ORDER BY rowid").fetchall()
        return [dict(r) for r in rows]
