import pytest
from app.config import SAMPLES
from app.pipeline import run
from app import identity
from app.errors import DomainError
from app.main import create_app

RAW = (SAMPLES / 'identity.csv').read_bytes()

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    return create_app().test_client()

def _link_count():
    return len(identity.all_links())

def test_idempotent_same_pair_not_duplicated(isolate):
    a, created_a = identity.submit_link('E1001', 'I2001')
    b, created_b = identity.submit_link('E1001', 'I2001')
    assert created_a is True and created_b is False
    assert a['link_id'] == b['link_id']
    assert _link_count() == 1 and len(identity.active_links()) == 1

def test_er_unique_active_constraint(isolate):
    identity.submit_link('E1001', 'I2001')
    # 同一 ER 号改指另一 IP => 更正，旧行 corrected
    new, created = identity.submit_link('E1001', 'I2005', note='改指')
    assert created is True and new['replaces'] is not None
    active = identity.active_links()
    assert len(active) == 1 and active[0]['ip_patient'] == 'I2005'
    history = identity.all_links()
    assert len(history) == 2  # 原始行未删除
    assert {x['status'] for x in history} == {'active', 'corrected'}

def test_ip_unique_active_constraint(isolate):
    identity.submit_link('E1001', 'I2001')
    new, _ = identity.submit_link('E1005', 'I2001', note='改指')
    assert new['replaces'] is not None
    assert identity.active_links()[0]['er_patient'] == 'E1005'

def test_cross_conflict_rejected(isolate):
    identity.submit_link('E1001', 'I2001')
    identity.submit_link('E1005', 'I2005')
    # E1001->I2005 同时撞上两条不同生效对应
    with pytest.raises(DomainError) as exc:
        identity.submit_link('E1001', 'I2005')
    assert exc.value.code == 'LINK_CONFLICT'
    assert _link_count() == 2  # 失败不留半条

def test_unknown_patient_rejected_with_run(isolate):
    # 先落一个批次拿到 run_id
    run_id = run(RAW, persist=True)['manifest']['run_id']
    with pytest.raises(DomainError) as exc:
        identity.submit_link('E9999', 'I2001', run_id=run_id)
    assert exc.value.code == 'LINK_UNKNOWN'
    with pytest.raises(DomainError):
        identity.submit_link('E1001', 'E1001', run_id=run_id)  # 住院号域不符

def test_unlink_keeps_history(isolate):
    link, _ = identity.submit_link('E1001', 'I2001')
    out = identity.unlink(link['link_id'], note='录错撤销')
    assert out['status'] == 'corrected'
    assert identity.active_links() == [] and len(identity.all_links()) == 1
    with pytest.raises(DomainError):
        identity.unlink(link['link_id'])

def test_pipeline_uses_active_links_only(raw, isolate):
    identity.submit_link('E1001', 'I2001')
    result = run(RAW, stop=8, persist=False)
    assert len(result['stages']) == 8
    assert result['identity_balance']['balanced'] is True
    persons = {s['person'] for s in result['identity_stays']}
    assert 'ER:E1001' in persons
    # 未关联编号自成一人
    assert 'IP:I2004' in persons

def test_wrong_then_corrected_link_restores_conflict(isolate):
    # 错并 E1003 -> I2003：真实双占被吞
    identity.submit_link('E1003', 'I2003', note='录错')
    wrong = run(RAW, stop=8, persist=False)
    assert len(wrong['identity_conflicts']) == 0
    # 更正：I2003 真实归属 E1005
    identity.submit_link('E1005', 'I2003', note='更正')
    fixed = run(RAW, stop=8, persist=False)
    assert len(fixed['identity_conflicts']) == 1
    assert fixed['identity_conflicts'][0]['bed'] == '104'
    persons = {s['person'] for s in fixed['identity_stays']}
    assert 'ER:E1003' in persons and 'ER:E1005' in persons  # 回到两条链
    # 原始事件未删除，守恒仍成立
    assert fixed['identity_balance'] == {'m2_events': 18, 'replayed': 16, 'quarantined': 2, 'balanced': True}

def test_http_endpoints(client):
    r = client.get('/api/m8')
    assert r.status_code == 200 and len(r.json['stages']) == 8
    # 提交
    r = client.post('/api/identity/links', json={'er_patient': 'E1001', 'ip_patient': 'I2001'})
    assert r.status_code == 201 and r.json['created'] is True
    # 幂等重试
    r2 = client.post('/api/identity/links', json={'er_patient': 'E1001', 'ip_patient': 'I2001'})
    assert r2.status_code == 200 and r2.json['created'] is False
    # 列表
    listing = client.get('/api/identity/links').json
    assert len(listing['active']) == 1 and len(listing['all']) == 1
    # 撤销
    lid = listing['active'][0]['link_id']
    assert client.post(f'/api/identity/links/{lid}/unlink', json={'note': '错'}).status_code == 200
    assert client.get('/api/identity/links').json['active'] == []

def test_page_renders_board(client):
    saved = client.post('/api/runs', json={'text': RAW.decode('utf-8-sig')}).json
    client.post('/api/identity/links', json={'er_patient': 'E1001', 'ip_patient': 'I2001'})
    page = client.get('/m/8?run=' + saved['manifest']['run_id'])
    assert page.status_code == 200
    assert '床位占用看板'.encode() in page.data
    assert 'L0001'.encode() in page.data
    assert '占用依据'.encode() in page.data

def test_legacy_csv_without_scope_defaults_ip(raw, isolate):
    result = run(raw, stop=8, persist=False)
    assert all(x.get('scope') in ('ER', 'IP') for x in result['identity_intervals'])
    # 旧样例无 ER 事件，所有区间均为无对应
    assert all(x['link_id'] == '' for x in result['identity_intervals'])

def test_identity_evidence_csv(client):
    client.post('/api/identity/links', json={'er_patient': 'E1001', 'ip_patient': 'I2001'})
    r = client.get('/api/identity/evidence.csv')
    assert r.status_code == 200
    assert 'M8-OCCUPY' in r.text and '原因码' in r.text
