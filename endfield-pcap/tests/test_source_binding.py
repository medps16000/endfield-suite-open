from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess

import pytest

from infra.runtime_probe import validate_source_binding as validator

ROOT = Path(__file__).resolve().parents[1]
UID = '9223372036854775901'


def observations():
    actor = dict(object='1100', cachedUid='4', instanceUid='4', entityUid='5',
                 serverCreated=True, serverId='9007199254740993', serverGeneration='0', battleInstId='9007199254740995')
    source = dict(object='2200', runtimeUid='6', templateId='same_buff', hasServerId=True,
                  serverId=UID, clientId='0', enabled=True, finished=False, duration=20, lifeTime=20,
                  passedTime=0, enhanceCount=1, owner=deepcopy(actor), source=deepcopy(actor))
    mod = dict(seq=2, parentModifierSeq=0, calculationSeq=1, threadId=10, timing=0, side=0,
               dpd='3000', modifierObject='4000', selfIndex=3, source=source, modifierOwner=deepcopy(actor))
    body = dict(seq=3, modifierSeq=2, calculationSeq=1, threadId=10, timing=0, dpd='3000', modifierObject='4000')
    proc = dict(seq=4, modifierSeq=2, modifierProcessSeq=3, calculationSeq=1, processorSeq=4,
                threadId=10, timing=0, snapshot={'dpd':'3000','attacker':deepcopy(actor),'defender':deepcopy(actor)})
    zone = {**proc, 'seq':5}
    calc = dict(seq=1, threadId=10, snapshot={'dpd':'3000'})
    return [
        dict(kind='probe_ready', module='GameAssembly.dll', sourceBindingSchemaVersion=1),
        dict(kind='calculate_enter', **deepcopy(calc)),
        dict(kind='modifier_source_enter', **deepcopy(mod)),
        dict(kind='modifier_process_enter', **deepcopy(body)),
        dict(kind='processor_enter', **deepcopy(proc)),
        dict(kind='zone_enter', **deepcopy(zone)), dict(kind='zone_leave', **deepcopy(zone)),
        dict(kind='processor_leave', **deepcopy(proc)),
        dict(kind='modifier_process_leave', **deepcopy(body)),
        dict(kind='modifier_source_leave', handle=dict(buffInstId=UID,index=3), **deepcopy(mod)),
        dict(kind='calculate_leave', **deepcopy(calc)),
    ]


def check(tmp_path, rows, number=True):
    if number:
        for i,row in enumerate(rows,1):row['recordSequence']=i
    path=tmp_path/'source.jsonl'
    path.write_text(''.join(json.dumps(r)+'\n' for r in rows),encoding='utf-8')
    return validator.validate(path)


def test_exact_source_chain_is_conditional_not_rdps_acceptance(tmp_path):
    report=check(tmp_path,observations())
    assert report['sourceModifierCount']==report['boundProcessorCount']==report['boundZoneWriteCount']==1
    assert report['formulaLinkedModifierCount']==1
    assert report['conditionalSourceBindingOk'] is True
    assert report['strictRdpsOk'] is False


@pytest.mark.parametrize('value',[None,True,9223372036854775901,'0','-1','1.5','01',str(2**64)])
def test_uid_requires_exact_uint64_string(tmp_path,value):
    rows=observations()
    rows[2]['source']['serverId']=value
    rows[9]['source']['serverId']=value
    rows[9]['handle']['buffInstId']=value
    assert check(tmp_path,rows)['conditionalSourceBindingOk'] is False


@pytest.mark.parametrize('field,value',[('buffInstId','9223372036854775902'),('index',4),('index',True)])
def test_protocol_handle_must_agree_with_actual_modifier(tmp_path,field,value):
    rows=observations();rows[9]['handle'][field]=value
    assert check(tmp_path,rows)['sourceModifierCount']==0


@pytest.mark.parametrize('field,value',[('instanceUid','3'),('serverCreated',None),('serverId',None),('object','0'),('serverGeneration',-1)])
def test_stale_or_incomplete_actor_identity_blocks(tmp_path,field,value):
    rows=observations()
    for index in (2,9):rows[index]['source']['source'][field]=value
    assert check(tmp_path,rows)['conditionalSourceBindingOk'] is False


def test_client_created_entity_can_have_explicit_server_identity(tmp_path):
    rows=observations()
    for row in rows:
        if isinstance(row.get('source'),dict):
            row['source']['owner']['serverCreated']=False
            row['source']['source']['serverCreated']=False
        if isinstance(row.get('modifierOwner'),dict):row['modifierOwner']['serverCreated']=False
        for actor in row.get('snapshot',{}).values():
            if isinstance(actor,dict):actor['serverCreated']=False
    report=check(tmp_path,rows)
    assert report['conditionalSourceBindingOk'] is True
    assert report['strictRdpsOk'] is False


@pytest.mark.parametrize('operation',['missing_leave','duplicate','out_of_order','wrong_thread','missing_seq','gap','wrong_parent'])
def test_broken_sequence_or_parentage_is_not_repaired(tmp_path,operation):
    rows=observations()
    if operation=='missing_leave':rows.pop(7)
    elif operation=='duplicate':rows.insert(8,deepcopy(rows[7]))
    elif operation=='out_of_order':rows[6],rows[7]=rows[7],rows[6]
    elif operation=='wrong_thread':rows[7]['threadId']=99
    elif operation=='missing_seq':rows[4].pop('seq')
    elif operation=='wrong_parent':rows[4]['modifierSeq']=99
    if operation=='gap':
        for i,r in enumerate(rows,1):r['recordSequence']=i+(i>5)
    assert check(tmp_path,rows,number=operation!='gap')['conditionalSourceBindingOk'] is False


def test_same_template_does_not_replace_instance_identity(tmp_path):
    rows=observations();rows[9]['source']['serverId']='9223372036854775902'
    report=check(tmp_path,rows)
    assert 'modifier_source_identity_changed' in report['blockerReasons']


@pytest.mark.parametrize('field,value',[('timing',None),('timing',True),('side',2),('side',False),('calculationSeq',99)])
def test_invalid_scope_fields_block(tmp_path,field,value):
    rows=observations()
    for i in (2,9):rows[i][field]=value
    assert check(tmp_path,rows)['conditionalSourceBindingOk'] is False


def test_modifier_recipient_must_match_the_observed_damage_side(tmp_path):
    rows=observations()
    for i in (4,7):rows[i]['snapshot']['attacker']['serverId']='9007199254740999'
    assert check(tmp_path,rows)['conditionalSourceBindingOk'] is False


@pytest.mark.parametrize('index,field',[(4,'modifierProcessSeq'),(5,'processorSeq')])
def test_missing_explicit_link_is_not_recovered_from_containment(tmp_path,index,field):
    rows=observations();rows[index].pop(field)
    assert check(tmp_path,rows)['conditionalSourceBindingOk'] is False


def test_no_formula_link_stays_an_explicit_gap(tmp_path):
    rows=observations()
    for row in rows:
        if 'calculationSeq' in row:row['calculationSeq']=0
    rows=[r for r in rows if not r['kind'].startswith('calculate_')]
    report=check(tmp_path,rows)
    assert report['conditionalSourceBindingOk'] is True
    assert report['formulaLinkedModifierCount']==0
    assert 'some_modifiers_not_explicitly_bound_to_formula_call' in report['evidenceGaps']


@pytest.mark.parametrize('handle',[None,{'buffInstId':'0','index':3},{'buffInstId':'12345','index':99}])
def test_unchanged_handle_without_processing_is_not_credited(tmp_path,handle):
    rows=observations();rows[0]['observationExtensionVersion']=1
    a,b=deepcopy(rows[2]),deepcopy(rows[9]);a['seq']=b['seq']=20
    a['handle']=deepcopy(handle);b['handle']=deepcopy(handle)
    rows[-1:-1]=[a,b]
    result=check(tmp_path,rows)
    assert result['sourceModifierCount']==1
    assert result['unchangedUnprocessedModifierCount']==1
    assert result['conditionalSourceBindingOk'] is True and result['strictRdpsOk'] is False


def test_legacy_missing_before_handle_is_not_backfilled(tmp_path):
    rows=observations();rows[0]['observationExtensionVersion']=1
    a,b=deepcopy(rows[2]),deepcopy(rows[9]);a['seq']=b['seq']=20
    rows[-1:-1]=[a,b]
    assert check(tmp_path,rows)['conditionalSourceBindingOk'] is False


def test_explicit_recycle_invalidates_only_the_same_runtime_instance(tmp_path):
    rows=observations();rows[0]['observationExtensionVersion']=1
    source=deepcopy(rows[2]['source']);identity={'object':source['object'],'runtimeUid':source['runtimeUid']}
    ending={'object':source['object'],'runtimeUid':'0'}
    extra=[{'kind':'buff_state_enter','seq':20,'threadId':10,'lifecycleEvent':'finish','source':source,'identity':identity},
           {'kind':'buff_recycle_enter','seq':21,'threadId':10,'parentLifecycleSeq':20,'identity':identity},
           {'kind':'buff_recycle_leave','seq':21,'threadId':10,'parentLifecycleSeq':20,'identity':ending},
           {'kind':'buff_state_leave','seq':20,'threadId':10,'lifecycleEvent':'finish','source':None,'identity':ending}]
    result=check(tmp_path,rows+extra)
    assert result['conditionalSourceBindingOk'] is True and result['recycledLifecycleCount']==1
    extra[2]['identity']={**ending,'runtimeUid':'7'}
    assert check(tmp_path,rows+extra)['conditionalSourceBindingOk'] is False


@pytest.mark.parametrize('mutation',['write_error','invalid_json','empty'])
def test_capture_failures_never_pass(tmp_path,mutation):
    rows=observations()
    if mutation=='write_error':rows[6]['writeErrors']=1
    if mutation=='empty':rows=[]
    report=check(tmp_path,rows)
    if mutation=='invalid_json':
        with (tmp_path/'source.jsonl').open('a',encoding='utf-8') as f:f.write('{"kind":')
        report=validator.validate(tmp_path/'source.jsonl')
    assert report['conditionalSourceBindingOk'] is False


@pytest.mark.skipif(os.name!='nt',reason='Native observation harness requires Windows and MSVC')
def test_native_c_observer_with_owned_fixture_memory(tmp_path):
    exe=tmp_path/'probe_test.exe';output=tmp_path/'native.jsonl'
    build=subprocess.run(['powershell','-NoProfile','-File',str(ROOT/'infra/runtime_probe/build_probe.ps1'),
                          '-OfflineTest','-Output',str(exe)],cwd=ROOT,capture_output=True,text=True)
    assert build.returncode==0,build.stdout[-2500:]+build.stderr[-2500:]
    run=subprocess.run([str(exe),str(output)],cwd=tmp_path,capture_output=True,text=True,timeout=30)
    assert run.returncode==0,(run.returncode,run.stdout,run.stderr)
    rows=[json.loads(line) for line in output.read_text(encoding='utf-8').splitlines()]
    report=validator.validate(output)
    assert report['sourceModifierCount']==report['modifierProcessCount']==report['boundProcessorCount']==report['boundZoneWriteCount']==4
    assert report['buffLifecyclePairCount']==7 and report['conditionalSourceBindingOk'] is True
    assert report['strictRdpsOk'] is False and report['formulaLinkedModifierCount']==0
    modifiers=[r for r in rows if r['kind']=='modifier_source_enter']
    assert {r['source']['serverId'] for r in modifiers}=={UID,'9223372036854775902'}
    assert len({r['threadId'] for r in modifiers})==3
    assert any(r['parentModifierSeq'] for r in modifiers)
    assert any(r['source']['owner']['serverId']!=r['source']['source']['serverId'] for r in modifiers)
    for row in rows:
        if row['kind']=='test_invalid_layout':assert row['source'] is None
        if row['kind']=='test_stale_object_ptr':assert row['source']['source'] is None
        if row['kind']=='test_client_only':assert row['source']['serverId'] is None and row['source']['hasServerId'] is False
        if row['kind']=='test_invalid_nullable':assert row['source'] is None
        if row['kind']=='test_escaped_name':assert row['source']['templateId']=='buff_"quoted\\name'
        if 'snapshot' in row:
            assert row['snapshot']['rawServerDamageParams']=='5a'*72
        if row['kind']=='test_complete':assert row['openModifiers']==row['openProcessors']==0
    lifecycle=[r for r in rows if r['kind']=='buff_state_leave']
    assert [r['lifecycleEvent'] for r in lifecycle]==['start','disable','enable','set_lifetime','enhance','finish','start']
    assert lifecycle[1]['source']['enabled'] is False and lifecycle[2]['source']['enabled'] is True
    assert lifecycle[3]['source']['lifeTime']==30.5 and lifecycle[4]['source']['enhanceCount']==2
    assert lifecycle[-2]['source']['finished'] is True and lifecycle[-1]['source']['finished'] is False
    assert lifecycle[-2]['source']['serverId']!=lifecycle[-1]['source']['serverId']
