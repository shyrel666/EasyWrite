"""补遗版本、人工生效、冲突与撤回，以及企业承诺隔离。"""
import pytest

from app.services.project_store import project_store
from app.services.proposals import compute_manifest
from app.services.proposals import propose
from app.services.generator.evidence_set import EvidenceSet
from app.models.schemas import DeviationItem


@pytest.fixture
def setup(client, project_id):
    root = f'/api/v1/project/{project_id}'
    analysis = {'target_package': '包1', 'duration_requirement': '90日历天', 'scoring_items': [
        {'id': 's1', 'package': '包1', 'name': '服务方案', 'criteria': '提供驻场服务', 'points': 10},
        {'id': 's2', 'package': '包2', 'name': '服务方案', 'criteria': '异包条款', 'points': 20}]}
    assert client.post(root + '/tender/apply', json={'analysis': analysis, 'tender_text': '工期90日历天\n提供驻场服务'}).status_code == 200
    client.put(root + '/outline', json={'outline': [{'id': 'n', 'title': '服务方案', 'content': '既有响应', 'status': 'reviewed', 'scoring_item_ids': ['s1']}]})
    return root


def document(client, root, package='包1', text='改为远程服务；另加演练；撤销驻场要求'):
    req = {'name': '补遗1', 'package': package, 'published_at': '2026-10-26', 'text': text}
    res = client.post(root + '/tender/documents', json=req)
    assert res.status_code == 200, res.text
    return res.json()


def suggestion(client, root, doc, clause='scoring:s1', action='replace', text='改为远程服务'):
    row = next(c for c in client.get(root + '/tender/versions').json()['clauses'] if c['id'] == clause)
    res = client.post(root + '/tender/overrides', json={'document_id': doc['id'], 'clause_id': clause,
        'package': '包1', 'basis_hash': row['basis_hash'], 'action': action, 'text': text,
        'source_excerpt': doc['text'], 'source_page': 2})
    return res


def decide(client, root, ov, state='confirmed'):
    return client.put(root + '/tender/overrides/' + ov['id'], json={'state': state, 'basis_hash': ov['basis_hash']})


def test_confirmed_override_and_withdraw_restore_requirements(client, setup, project_id):
    before = project_store.get(project_id)
    manifest = compute_manifest(before, before.outline[0])
    doc = document(client, setup)
    ov = suggestion(client, setup, doc).json()
    assert project_store.get(project_id).tender_analysis.scoring_items[0].criteria == '提供驻场服务'
    assert decide(client, setup, ov).status_code == 200
    p = project_store.get(project_id)
    assert p.tender_analysis.scoring_items[0].criteria == '改为远程服务'
    assert p.facts == before.facts
    assert p.outline[0].reviewed_revision is None
    assert compute_manifest(p, p.outline[0]) != manifest
    assert '提供驻场服务' not in project_store.get_tender_text(project_id)
    assert p.tender_versions.documents[0].text == '工期90日历天\n提供驻场服务'
    assert decide(client, setup, ov, 'withdrawn').status_code == 200
    assert project_store.get(project_id).tender_analysis.scoring_items[0].criteria == '提供驻场服务'


@pytest.mark.parametrize('action,expected', [('append', '提供驻场服务\n另加演练'), ('revoke', None)])
def test_append_and_revoke(client, setup, project_id, action, expected):
    doc = document(client, setup)
    ov = suggestion(client, setup, doc, action=action, text='另加演练').json()
    assert decide(client, setup, ov).status_code == 200
    items = project_store.get(project_id).tender_analysis.scoring_items
    assert (next((i.criteria for i in items if i.id == 's1'), None)) == expected


def test_same_day_conflict_and_dependent_withdraw(client, setup):
    doc = document(client, setup)
    first, second = [suggestion(client, setup, doc).json() for _ in range(2)]
    assert decide(client, setup, first).status_code == 200
    assert decide(client, setup, second).status_code == 409
    third = suggestion(client, setup, doc, action='append', text='另加演练').json()
    assert decide(client, setup, third).status_code == 200
    assert decide(client, setup, first, 'withdrawn').status_code == 409
    assert decide(client, setup, third, 'withdrawn').status_code == 200
    assert decide(client, setup, first, 'withdrawn').status_code == 200


def test_wrong_package_and_idempotent_documents(client, setup):
    doc = document(client, setup)
    assert document(client, setup)['id'] == doc['id']
    assert suggestion(client, setup, doc, clause='scoring:s2').status_code == 400
    wrong = document(client, setup, package='包2')
    assert suggestion(client, setup, wrong).status_code == 400


def test_requirements_recomputed_and_whole_apply_blocked(client, setup, project_id):
    doc = document(client, setup, text='工期变更为60日历天')
    ov = suggestion(client, setup, doc, clause='field:duration_requirement:0', text='60日历天').json()
    assert decide(client, setup, ov).status_code == 200
    p = project_store.get(project_id)
    assert p.tender_analysis.commitment_requirements[0].value == '60'
    assert p.facts.delivery_guarantee == ''
    assert client.post(setup + '/tender/apply', json={'analysis': {}}).status_code == 409
    assert decide(client, setup, ov).status_code == 200


def test_source_excerpt_must_exist_and_cross_project_rejected(client, setup):
    doc = document(client, setup)
    row = client.get(setup + '/tender/versions').json()['clauses'][0]
    req = {'document_id': doc['id'], 'clause_id': row['id'], 'package': '包1', 'basis_hash': row['basis_hash'],
           'text': '变更', 'source_excerpt': '原件没有此句'}
    assert client.post(setup + '/tender/overrides', json=req).status_code == 400
    req['document_id'] = 'another-project-doc'
    assert client.post(setup + '/tender/overrides', json=req).status_code == 400


def test_old_proposal_rejected_and_deviation_reset_on_confirm_and_withdraw(client, setup, project_id):
    project_store.update(project_id, lambda p: p.deviation_matrix.append(DeviationItem(index=1, clause_title='提供驻场服务', response_status='完全满足')))
    p = project_store.get(project_id)
    prop = propose(p, p.outline[0], '候选方案', EvidenceSet(), origin='batch')
    ov = suggestion(client, setup, document(client, setup)).json()
    assert decide(client, setup, ov).status_code == 200
    res = client.post(setup + f'/section/n/proposals/{prop["id"]}/apply')
    assert res.status_code == 409
    d = project_store.get(project_id).deviation_matrix[0]
    assert d.clause_title == '改为远程服务' and d.response_status == '待核实'
    assert decide(client, setup, ov, 'withdrawn').status_code == 200
    assert project_store.get(project_id).deviation_matrix[0].clause_title == '提供驻场服务'


def test_rule_suggestion_is_read_only(client, setup, project_id):
    document(client, setup, text='原“提供驻场服务”修改为“提供远程服务”')
    state = client.get(setup + '/tender/versions').json()
    assert state['suggestions'][0]['text'] == '提供远程服务'
    assert state['overrides'] == []
    assert project_store.get(project_id).tender_analysis.scoring_items[0].criteria == '提供驻场服务'
