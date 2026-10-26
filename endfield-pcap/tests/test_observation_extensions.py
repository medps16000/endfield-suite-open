from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from infra.runtime_probe import validate_observation_extensions as validator

ROOT=Path(__file__).resolve().parents[1]
FIXTURE=Path(__file__).with_name('fixtures')/'runtime_observation_extensions_v1.jsonl'


def rows():
    return [json.loads(line) for line in FIXTURE.read_text(encoding='utf-8').splitlines()]


def run(tmp_path,records,renumber=False):
    if renumber:
        for i,row in enumerate(records,1):row['recordSequence']=i
    path=tmp_path/'observation.jsonl';path.write_text(''.join(json.dumps(r)+'\n' for r in records),encoding='utf-8')
    return validator.validate(path)


def first(records,kind):
    return next(r for r in records if r['kind']==kind)


def test_frozen_native_fixture_is_explicitly_synthetic_and_byte_bound():
    manifest=json.loads(FIXTURE.with_suffix('.manifest.json').read_text(encoding='utf-8'))
    assert manifest['synthetic'] is True
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest()==manifest['fixtureSha256']
    report=validator.validate(FIXTURE)
    assert report['conditionalObservationOk'] is True
    assert report['damageIdentityCount']==2
    assert [r['identityKey'][-2] for r in report['damageIdentities']]==[0,7]
    assert all(r['identityKey'][10]==0 for r in report['damageIdentities'])
    assert report['attributeQueryCount']==2
    assert report['buffAttributeQueryCount']==3
    assert report['attributeLoaderCount']==report['recycleCount']==report['unchangedUnprocessedHandleCount']==1
    assert report['strictRdpsOk'] is report['attributeFormulaVerified'] is report['packetHitBindingVerified'] is False


@pytest.mark.parametrize('container,field,value',[
    ('action','actionId',None),('action','actionId',True),('action','actionType',0),
    ('action','implementationCase',4),('action','ownerId',9007199254740993),
    ('action','environmentInstId',str(2**64)),('action','templateIdKind',0),
    ('action','templateId',False),('detail','damageUnitIndex',None),
    ('detail','damageUnitIndex',True),('detail','damageUnitIndex',-1),
    ('detail','partInstId',None),('detail','targetBattleInstId','9007199254740000')])
def test_invalid_detail_fields_cannot_be_guessed(tmp_path,container,field,value):
    records=rows();first(records,'damage_detail_observation')[container][field]=value
    assert run(tmp_path,records)['conditionalObservationOk'] is False


@pytest.mark.parametrize('field,value',[('calculationSeq',999),('threadId',999),('dpd','ffff'),('seq',None)])
def test_wrong_detail_scope_is_not_repaired_by_equal_values(tmp_path,field,value):
    records=rows();first(records,'damage_detail_observation')[field]=value
    assert run(tmp_path,records)['conditionalObservationOk'] is False


def test_disagreeing_getter_returns_in_one_calculation_block(tmp_path):
    records=rows();first(records,'damage_detail_observation')['detail']['damageUnitIndex']=3
    report=run(tmp_path,records)
    assert 'conflicting_detail_observations' in report['blockerReasons']


def test_missing_observation_does_not_use_neighboring_call(tmp_path):
    records=rows();calc=first(records,'damage_detail_observation')['calculationSeq']
    records=[r for r in records if not (r['kind']=='damage_detail_observation' and r['calculationSeq']==calc)]
    report=run(tmp_path,records,renumber=True)
    assert report['damageIdentityCount']==1
    assert 'missing_direct_detail_observation' in report['blockerReasons']


def test_string_template_and_integer_template_stay_distinct(tmp_path):
    records=rows()
    for r in records:
        if r['kind']=='damage_detail_observation':r['action'].update(templateIdKind=1,templateId='77')
    report=run(tmp_path,records)
    assert report['conditionalObservationOk'] is True
    assert report['damageIdentities'][0]['identityKey'][8:10]==[1,'77']


@pytest.mark.parametrize('value',[None,0,True,'xyz','3ff00000','7ff8000000000000'])
def test_invalid_or_nonfinite_attribute_result_blocks(tmp_path,value):
    records=rows();first(records,'attribute_query_leave')['resultBits']=value
    assert run(tmp_path,records)['conditionalObservationOk'] is False


def test_false_buff_query_cannot_contribute_leftover_output(tmp_path):
    records=rows();failed=next(r for r in records if r['kind']=='buff_attribute_query_leave' and r['returned'] is False)
    failed['valueBits']={'addition':'3ff0000000000000'}
    report=run(tmp_path,records)
    assert 'failed_query_output_must_not_be_used' in report['blockerReasons']


@pytest.mark.parametrize('operation',['missing_channel','short_array','wrong_count','wrong_parent','bad_mask'])
def test_attribute_structure_and_query_links_are_required(tmp_path,operation):
    records=rows();load=first(records,'buff_attribute_load_enter')['loader']
    if operation=='missing_channel':load.pop('additions')
    elif operation=='short_array':load['additions'].pop()
    elif operation=='wrong_count':load['attributeCount']=100
    elif operation=='bad_mask':load['lowerMask']=4
    else:first(records,'buff_attribute_query_enter')['attributeQuerySeq']=999
    assert run(tmp_path,records)['conditionalObservationOk'] is False


def test_null_loader_channel_is_not_imputed_as_zero(tmp_path):
    records=rows();first(records,'buff_attribute_load_enter')['loader']['multipliers']=None
    report=run(tmp_path,records)
    assert report['conditionalObservationOk'] is True
    load=report['attributeLoads'][0]
    assert 'multipliers' in load['nullTransitions']
    assert not any(r['channel']=='multipliers' for r in load['changedSlots'])


@pytest.mark.parametrize('operation',['missing_recycle','wrong_uid','wrong_parent','return_live','source_reused'])
def test_zero_uid_requires_explicit_recycle_evidence(tmp_path,operation):
    records=rows()
    if operation=='missing_recycle':records=[r for r in records if not r['kind'].startswith('buff_recycle_')]
    elif operation=='wrong_uid':first(records,'buff_recycle_enter')['identity']['runtimeUid']='7'
    elif operation=='wrong_parent':first(records,'buff_recycle_enter')['parentLifecycleSeq']=0
    elif operation=='return_live':first(records,'buff_recycle_leave')['identity']['runtimeUid']='999'
    else:first(records,'buff_state_leave')['source']=deepcopy(first(records,'buff_state_enter')['source'])
    assert run(tmp_path,records,renumber=operation=='missing_recycle')['conditionalObservationOk'] is False


@pytest.mark.parametrize('operation',['missing_before','changed_after','numeric_uid'])
def test_unprocessed_handle_is_an_observation_not_an_attribution(tmp_path,operation):
    records=rows()
    if operation=='missing_before':first(records,'modifier_source_enter').pop('handle')
    elif operation=='changed_after':first(records,'modifier_source_leave')['handle']['buffInstId']='12346'
    else:first(records,'modifier_source_enter')['handle']['buffInstId']=12345
    assert run(tmp_path,records)['conditionalObservationOk'] is False


@pytest.mark.parametrize('operation',['bad_json','missing_ready','gap','write_error','read_error'])
def test_incomplete_or_failed_capture_blocks(tmp_path,operation):
    records=rows()
    if operation=='missing_ready':records=records[1:]
    elif operation=='gap':records[2]['recordSequence']+=10
    elif operation=='write_error':records[3]['writeErrors']=1
    elif operation=='read_error':records[3]['observationReadErrors']=1
    report=run(tmp_path,records)
    if operation=='bad_json':
        with (tmp_path/'observation.jsonl').open('a',encoding='utf-8') as f:f.write('{"kind":')
        report=validator.validate(tmp_path/'observation.jsonl')
    assert report['conditionalObservationOk'] is False


@pytest.mark.skipif(os.name!='nt',reason='C observation harness uses Windows/MSVC')
def test_live_c_observer_in_its_own_process(tmp_path):
    exe=tmp_path/'observations.exe';output=tmp_path/'native.jsonl'
    build=subprocess.run(['powershell','-NoProfile','-File',str(ROOT/'infra/runtime_probe/build_probe.ps1'),'-OfflineTest','-Output',str(exe)],cwd=ROOT,capture_output=True,text=True)
    assert build.returncode==0,build.stdout[-2000:]+build.stderr[-2000:]
    test=subprocess.run([str(exe),str(output),'extensions'],capture_output=True,text=True,timeout=30)
    assert test.returncode==0,(test.returncode,test.stdout,test.stderr)
    records=[json.loads(l) for l in output.read_text(encoding='utf-8').splitlines()]
    end=next(i for i,r in enumerate(records) if r['kind']=='test_valid_extension_end')
    report=run(tmp_path,records[:end+1])
    assert report['conditionalObservationOk'] is True
    assert report['damageIdentityCount']==2
    assert report['recycleCount']==report['unchangedUnprocessedHandleCount']==1
    assert {q['result'] for q in report['attributeQueries']}=={900.125,123.5}
    values=[q['values'] for q in report['buffAttributeQueries'] if q['queryKind']=='regular' and q['returned']]
    assert values==[{'addition':50.0,'multiplier':0.25,'finalAddition':10.0,'finalScalar':1.1}]
    assert any(r['channel']=='multipliers' and r['attributeType']==2 for r in report['attributeLoads'][0]['changedSlots'])
    bad={r['kind']:r for r in records[end+1:]}
    assert bad['test_bad_box']['action'] is None
    assert bad['test_bad_detail_layout']['detail'] is None
    assert bad['test_short_attribute_array']['loader'] is None
    assert validator.validate(output)['conditionalObservationOk'] is False
