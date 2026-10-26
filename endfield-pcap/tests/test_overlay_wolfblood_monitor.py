"""Run the real Wolf HTML script against public API replay and fault variants."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path
import shutil
import subprocess

import pytest


def test_real_wolf_html_replay_and_fail_closed_boundaries(tmp_path):
    node = shutil.which("node")
    fixture = Path(r"C:\path\to\local-test\endfield-wolfblood-20261019\captures\rodan-public-operation-fixture.json")
    indexed_fixture = Path(r"C:\path\to\local-test\endfield-wolfblood-20261020-fix\controlled-indexed-public-fixture.json")
    if not node or not fixture.is_file() or not indexed_fixture.is_file():
        pytest.skip("Requires Node and the pinned local public-operation replay fixture")
    capture = json.loads(fixture.read_text(encoding="utf-8"))
    original = Path(capture["source"]["path"])
    assert original.is_file(), "The controlled replay must remain bound to its real capture"
    with original.open("rb") as stream:
        prefix = stream.read(capture["source"]["pinnedPrefixBytes"])
    assert hashlib.sha256(prefix).hexdigest() == capture["source"]["prefixSha256"]
    assert [op["seqId"] for op in capture["operations"]] == list(range(capture["seqFirst"], capture["seqLast"] + 1))
    indexed_capture = json.loads(indexed_fixture.read_text(encoding="utf-8"))
    assert indexed_capture["sourceIndexIsSynthetic"] and indexed_capture["captureIntegrityIsSyntheticZeroBaseline"]
    assert indexed_capture["operationDigestsAreSyntheticReconstructedProtos"] and not indexed_capture["provesOriginalRawTransportIntegrity"]
    assert indexed_capture["positiveGameSeqSkips"] == 21
    assert hashlib.sha256(Path(indexed_capture["source"]["copied"]).read_bytes()).hexdigest() == indexed_capture["source"]["sha256"]
    from endfield_pcap.overlay_effect_catalog import load_effect_catalog, load_effect_definitions

    root = Path(__file__).resolve().parents[1]
    names = {"buff_wpn_sword_0022_layer", "buff_wpn_sword_0022_final"}
    definitions = [row for row in load_effect_definitions(root) if row["canonicalId"] in names]
    assert len(definitions) == 2
    catalog = {row["canonicalId"]: row for row in load_effect_catalog(root)}
    icons = {row["iconKey"]: catalog[row["canonicalId"]]["icon"] for row in definitions
             if catalog[row["canonicalId"]].get("icon")}
    data = tmp_path / "wolf-api-input.json"
    data.write_text(json.dumps({"definitions": definitions, "icons": icons, "indexedFixture": indexed_capture}, ensure_ascii=False), encoding="utf-8")
    html = root / "src/endfield_pcap/overlay_examples/wolfblood-monitor.html"
    driver = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const scripts=[...fs.readFileSync(process.argv[1],'utf8').matchAll(/<script>([\s\S]*?)<\/script>/g)].map(x=>x[1]);
const fixture=JSON.parse(fs.readFileSync(process.argv[2],'utf8')),metadata=JSON.parse(fs.readFileSync(process.argv[3],'utf8'));
const indexedFixture=metadata.indexedFixture;
const clone=value=>JSON.parse(JSON.stringify(value)),flush=()=>new Promise(resolve=>setImmediate(resolve));
class Element{constructor(){this.children=[];this.text='';this.dataset={};}append(...items){this.children.push(...items);}
 replaceChildren(...items){this.children=items;this.text='';}set textContent(value){this.text=String(value);this.children=[];}
 get textContent(){return this.text+this.children.map(x=>x.textContent).join(' ');}set innerHTML(_){throw new Error('innerHTML forbidden');}}
const base=fixture.frames[0],TARGET='100637';
async function setup({badMetadata=null,targets=[],targetNames={}}={}){
 const elements={status:new Element(),cards:new Element()},subscriptions=[],definitions=clone(metadata.definitions);
 if(badMetadata)badMetadata(definitions);
 const api={getTopics:async()=>['effects.definitions.v1','capture.context.v1','effects.operations.v1','targets.current.v1'].map(topic=>({topic})),
  subscribe:(spec,callback)=>{const row={spec,callback,active:true};subscriptions.push(row);
   if(spec.topic==='effects.definitions.v1')queueMicrotask(()=>callback({state:'ready',data:{definitions:definitions.filter(x=>spec.filter.effectIds.includes(x.canonicalId)),icons:metadata.icons}}));
   return()=>{row.active=false;};}};
 assert.deepEqual(Object.keys(api).sort(),['getTopics','subscribe']);
 const context=vm.createContext({window:{endfieldOverlay:api,addEventListener(){}},document:{getElementById:id=>elements[id],createElement:()=>new Element()},queueMicrotask,console});
 vm.runInContext(scripts[0],context);vm.runInContext('CONFIG.targetIds='+JSON.stringify(targets)+';CONFIG.targetNames='+JSON.stringify(targetNames),context);vm.runInContext(scripts[1],context);
 await flush();await flush();
 const callback=topic=>subscriptions.find(row=>row.spec.topic===topic)?.callback;
 const count=(target=TARGET)=>elements.cards.children.find(row=>row.dataset.targetId===target)?.dataset.count||'unknown';
 const gate=(overrides={})=>callback('capture.context.v1')?.({state:'ready',lifecycleEvidence:{clientOperationSequenceExact:{state:'ready'},clientFinishExact:{state:'ready'},clientActionFinishExact:{state:'ready'},...(overrides.evidence||{})},data:{context:overrides.context||base.context,nativeBinding:overrides.nativeBinding||base.nativeBinding}});
 return {elements,subscriptions,count,gate,send:data=>callback('effects.operations.v1')?.({state:'ready',data}),callback};
}
const finalCreate=clone(fixture.operations[0]),clear=clone(fixture.operations.find(x=>x.index===fixture.clearCandidateIndex));
const create=clone(fixture.operations.find(x=>x.index>fixture.clearCandidateIndex&&x.payload?.action?.create_buff_action?.details.some(y=>y.buff_num_id===2394)));
const finish=clone(fixture.operations.find(x=>x.type==='BattleOpFinishBuff'&&x.payload?.force===true&&x.payload?.reduce_layer===1));
assert(finalCreate&&clear&&create&&finish);
const countEvents=[];
function freshCreate(n,target=TARGET){const row=clone(create),uid=(2305843009213900000n+BigInt(n)).toString();
 row.payload.operation_owner_id=target;row.payload.owner_id=target;row.payload.trigger_owner_id=target;
 row.payload.inst_id=target===TARGET?'100664':'200664';row.payload.trigger_inst_id=row.payload.inst_id;
 const detail=row.payload.action.create_buff_action.details.find(x=>x.buff_num_id===2394);
 detail.target_id=target;detail.source_id=target;detail.buff_inst_id=uid;return row;}
function freshFinal(n,target=TARGET){const row=clone(finalCreate),uid=(2305843009214000000n+BigInt(n)).toString();
 row.payload.operation_owner_id=target;row.payload.owner_id=target;row.payload.trigger_owner_id=target;
 row.payload.inst_id=target===TARGET?'100664':'200664';row.payload.trigger_inst_id=row.payload.inst_id;
 const detail=row.payload.action.create_buff_action.details.find(x=>x.buff_num_id===2393);
 detail.target_id=target;detail.source_id=target;detail.buff_inst_id=uid;return row;}
function clearFor(final,target=TARGET){const row=clone(clear),uid=final.payload.action.create_buff_action.details.find(x=>x.buff_num_id===2393).buff_inst_id;
 row.payload.operation_owner_id=target;row.payload.owner_id=target;row.payload.trigger_owner_id=target;
 row.payload.trigger_inst_id=uid;row.payload.inst_id=uid;row.payload.action.finish_buff_action.finish_buffs.forEach(x=>{
  x.target_id=target;if(target!==TARGET)x.buff_inst_id=(BigInt(x.buff_inst_id)+500000n).toString();});return row;}
function series(rows,start=1,seq=1000){return rows.map((row,i)=>({...clone(row),index:start+i,seqId:seq+i,sequenceRelation:start+i===1?'first_observation':'next',payloadRevision:row.payloadRevision||0}));}
let revision=100000;
function frame(rows,extra={}){const first=rows[0]?.index||1,last=rows.at(-1)?.index||first;
 return {...clone(base),operations:rows,firstIndex:first,lastIndex:last,lastObservedIndex:last,bufferFirstIndex:first,bufferLastIndex:last,
  bufferDroppedCount:0,projectionOmittedOperationCount:0,omittedBeforeCount:first-1,gapRevision:0,payloadOmittedCount:0,
  lastPayloadOmissionIndex:null,contentRevision:++revision,continuity:{state:'contiguous',lastGapIndex:null,lastSeqId:rows.at(-1)?.seqId,initialGameState:'unknown'},...extra};}
function payloadUid(row){return row.payload.action.create_buff_action.details.find(x=>x.buff_num_id===2394).buff_inst_id;}
function ending(uid){const row=clone(finish);row.payload.buff_inst_id=uid;row.payload.operation_owner_id=TARGET;return row;}
const HEALTH_KEYS=['packetsDroppedQueue','decompressionErrors','protobufDecodeErrors','framingErrors','pendingMultipacks','pendingTransportSegments','currentFrameErrors'];
const healthy=()=>Object.fromEntries(HEALTH_KEYS.map(key=>[key,0]));
function indexedRows(rows){return rows.map((row,i)=>({...clone(row),sourceIndex:row.index,
 operationSha256:require('crypto').createHash('sha256').update(JSON.stringify([row.seqId,row.type,row.payload])).digest('hex'),
 captureSequenceRelation:row.index===1?'first_observation':'next',captureIntegrity:healthy(),captureIntegrityState:'stable',captureIntegrityRevision:0,
 sequenceRelation:row.index===1?'first_observation':i&&row.seqId>rows[i-1].seqId+1?'forward_skip':'next'}));}
function indexedFrame(rows,extra={}){return frame(rows,{sourceMode:'raw-operation-header',
 continuity:{state:'contiguous',basis:'source-index-and-capture-integrity',lastGapIndex:null,lastSeqId:rows.at(-1)?.seqId,lastSourceIndex:rows.at(-1)?.sourceIndex},
 captureIntegrity:{state:'stable',baseline:healthy(),current:healthy(),changedFields:[],revision:0,afterIndex:null},...extra});}
async function calibrated(mode=base.sourceMode){const page=await setup();page.gate();const f=freshFinal(1);const rows=series([f,clearFor(f)]);
 if(mode==='raw-operation-header')rows.forEach(row=>row.payloadRevision=1);
 page.send(frame(rows,{sourceMode:mode}));assert.equal(page.count(),'0',page.elements.status.textContent);return {page,rows};}
(async()=>{
 // The capture replay uses broker-exported public frames and actual payloads,
 // not a duplicate implementation of the counter or manufactured gap fillers.
 const real=await setup();real.gate();let zero=false,sixteen=false,preclearKnown=false;
 assert.equal(real.elements.cards.children.length,1);assert(real.elements.cards.textContent.includes('狼之绯 · 狼血'));
 for(const header of fixture.frames){const data={...clone(header),operations:fixture.operations.filter(op=>op.index>=header.firstIndex&&op.index<=header.lastIndex)};
  real.send(data);const value=real.count();countEvents.push({lastIndex:header.lastIndex,count:value});
  if(header.lastIndex<fixture.clearCandidateIndex&&value!=='unknown')preclearKnown=true;
  if(header.lastIndex===fixture.clearCandidateIndex&&value==='0')zero=true;
  if(header.lastIndex===fixture.secondFinalCreateIndex&&value==='16')sixteen=true;
 }
 assert(!preclearKnown,'Initial final/create/finish must never recover a total');
 assert(zero,'A broker-exported complete clear frame must recover zero: '+JSON.stringify({status:real.elements.status.textContent,tail:countEvents.slice(-20)}));assert(sixteen,'Actual second cycle must reach 16');
 // Native/static proof gates independently fail closed.
 for(const name of ['clientOperationSequenceExact','clientFinishExact','clientActionFinishExact']){
  const page=await setup();page.gate({evidence:{[name]:{state:'unavailable'}}});page.send(frame(series([finalCreate,clear])));assert.equal(page.count(),'unknown');}
 for(const resource of ['buff','skill','path','graph']){
  const page=await setup({badMetadata:rows=>{if(resource==='buff')rows[0].details.buffSourceSha256='0'.repeat(64);
   else if(resource==='skill')rows[0].details.sourceResourcesSha256['SkillData/sk_wpn_sword_0022']='0'.repeat(64);
   else if(resource==='graph')rows[0].details.definitionGraphBinding.skillManifestSha256='0'.repeat(64);
   else rows[0].details.sourcePaths.forEach(x=>x.steps.forEach(y=>y.serverActionIndex=99));}});
  assert(!page.subscriptions.some(x=>x.spec.topic==='effects.operations.v1'));assert.equal(page.count(),'unknown');}
 const wrongNative=await setup();wrongNative.gate({nativeBinding:{...base.nativeBinding,gameAssemblySha256:'0'.repeat(64)}});
 wrongNative.send(frame(series([finalCreate,clear])));assert.equal(wrongNative.count(),'unknown');
 {const {page,rows}=await calibrated();page.send(frame(rows,{nativeBinding:{...base.nativeBinding,metadataSha256:'0'.repeat(64)}}));assert.equal(page.count(),'unknown');}
 // This exact typed complete-clear event itself proves final source UID and
 // owner; an earlier final-create packet is not needed or guessed.
 {const page=await setup();page.gate();page.send(frame(series([clear])));assert.equal(page.count(),'0');}
 for(const mutate of [
  x=>x.payload.owner_type='2',
  x=>{x.payload.template_type='Skill';x.payload.template_id.type='Skill';},
  x=>x.payload.template_int_id=2394,
  x=>x.payload.trigger_owner_id='200637',
  x=>x.payload.operation_owner_id='200637',
  x=>x.payload.trigger_inst_id='99',
  x=>x.payload.inst_id='2305843009214999999',
  x=>x.payload.trigger_type=2,
  x=>x.payload.action.action_type='3',
  x=>x.payload.action.finish_buff_action.finish_buffs[0].target_id='200637',
  x=>x.payload.action.finish_buff_action.finish_buffs[0].buff_inst_id=x.payload.trigger_inst_id,
  x=>x.payload.action.finish_buff_action.finish_buffs.push(clone(x.payload.action.finish_buff_action.finish_buffs[0])),
  x=>x.payload.action.finish_buff_action.finish_buffs=[]
 ]){const page=await setup();page.gate();const bad=clone(clear);mutate(bad);page.send(frame(series([bad])));assert.equal(page.count(),'unknown');}
 {const page=await setup();page.gate();const one=clone(clear);one.payload.action.finish_buff_action.finish_buffs=one.payload.action.finish_buff_action.finish_buffs.slice(0,1);
  page.send(frame(series([one])));assert.equal(page.count(),'0','It is completed FinishAll proof, never list length==16, that recovers zero');}
 {const page=await setup();page.gate();const old=clone(clear),later=freshCreate(888);
  later.payload.action.create_buff_action.details[0].buff_inst_id=old.payload.action.finish_buff_action.finish_buffs[0].buff_inst_id;
  page.send(frame(series([old,later])));assert.equal(page.count(),'unknown','A UID explicitly cleared before joining cannot be resurrected by a later duplicate create');}
 // Unit UID contribution, duplicate UID, explicit finish and cap wire ordering.
 {const {page,rows}=await calibrated();const c=freshCreate(1);rows.push(...series([c],3,1002));page.send(frame(rows));assert.equal(page.count(),'1');
  rows.push(...series([c],4,1003));page.send(frame(rows));assert.equal(page.count(),'1');
  rows.push(...series([ending(payloadUid(c))],5,1004));page.send(frame(rows));assert.equal(page.count(),'0');}
 {const {page,rows}=await calibrated();for(let n=1;n<=17;n++){rows.push(...series([freshCreate(n)],n+2,1001+n));page.send(frame(rows));assert.equal(page.count(),String(Math.min(n,16)));}
  rows.push(...series([ending(payloadUid(rows[2]))],20,1019));page.send(frame(rows));assert.equal(page.count(),'16');}
 // Pending is not consumed. Enrichment of that same index/revision resumes
 // the earlier anchor, even when later headers arrived in the meantime.
 {const {page,rows}=await calibrated('raw-operation-header');const complete=series([freshCreate(1)],3,1002)[0],pending={...clone(complete),payloadState:'pending',payload:null,payloadRevision:0};
  const later={index:4,seqId:1003,type:'UnknownBattleOperation',sequenceRelation:'next',payloadState:'header-only',payload:null,payloadRevision:0};
  page.send(frame([...rows,pending,later],{sourceMode:'raw-operation-header'}));assert.equal(page.count(),'unknown');
  complete.payloadRevision=1;page.send(frame([...rows,complete,later],{sourceMode:'raw-operation-header'}));assert.equal(page.count(),'1');}
 {const {page,rows}=await calibrated('raw-operation-header');const complete=series([freshCreate(1)],3,1002)[0],pending={...clone(complete),payloadState:'pending',payload:null,payloadRevision:0};
  page.send(frame([...rows,pending],{sourceMode:'raw-operation-header'}));
  page.send(frame([...rows,complete],{sourceMode:'raw-operation-header'}));assert.equal(page.count(),'unknown','A pending payload cannot change without payloadRevision advancing');}
 for(const seq of [1001,900,1008]){const {page,rows}=await calibrated();const c=series([freshCreate(1)],3,seq)[0];page.send(frame([...rows,c]));assert.equal(page.count(),'unknown');}
 {const {page,rows}=await calibrated();const pending=series([freshCreate(1)],3,1002)[0];pending.payloadState='omitted';pending.payload=null;
  page.send(frame([...rows,pending],{payloadOmittedCount:1,lastPayloadOmissionIndex:3}));assert.equal(page.count(),'unknown');}
 {const {page}=await calibrated();const unseen=series([freshCreate(1)],6,1005);page.send(frame(unseen,{bufferDroppedCount:5}));assert.equal(page.count(),'unknown');}
 {const {page,rows}=await calibrated();const changed=clone(rows);changed[1].payloadRevision++;changed[1].payload.action.finish_buff_action.finish_layer_cnt=1;
  page.send(frame(changed));assert.equal(page.count(),'unknown');}
 {const {page,rows}=await calibrated();const initial=frame(rows);page.send(initial);page.send({...initial,contentRevision:initial.contentRevision-1});assert.equal(page.count(),'unknown');}
 {const {page,rows}=await calibrated();page.gate({context:{...base.context,generation:base.context.generation+1}});page.send(frame(rows));assert.equal(page.count(),'unknown');}
 {const {page}=await calibrated();page.send(frame(series([{type:'UnknownBattleOperation',payloadState:'header-only',payload:null,payloadRevision:0}],3,1002),{streamRevision:base.streamRevision+1}));assert.equal(page.count(),'unknown');}
 {const {page,rows}=await calibrated();const bad=freshCreate(1);bad.payload.action.create_buff_action.details[0].assigned_items.find(x=>x.target_key==='max_stack').numeric_value=10;
  rows.push(...series([bad],3,1002));page.send(frame(rows));assert.equal(page.count(),'unknown');}
 {const page=await setup();page.gate();const f1=freshFinal(1),f2=freshFinal(2,'200637');const rows=series([f1,clearFor(f1),freshCreate(1),f2,clearFor(f2,'200637'),freshCreate(2,'200637')]);
  page.send(frame(rows));assert.equal(page.count(),'1');assert.equal(page.count('200637'),'1');assert.equal(page.elements.cards.children.length,2);}
 {const page=await setup({targets:[TARGET]});page.gate();const f=freshFinal(2,'200637');page.send(frame(series([f,clearFor(f,'200637')])));assert.equal(page.elements.cards.children.length,1);assert.equal(page.count(),'unknown');}
 // A past sticky gap permits only a fresh, later complete cycle, not a
 // clear at/before that gap or an old buffered clear before target loss.
 {const page=await setup();page.gate();const f=freshFinal(20),unknown={type:'UnknownBattleOperation',payloadState:'header-only',payload:null,payloadRevision:0};
  const rows=series([unknown,unknown,f,clearFor(f)]);rows[1].sequenceRelation='gap';
  page.send(frame(rows,{gapRevision:1,continuity:{state:'gap',lastGapIndex:2,lastSeqId:1003}}));assert.equal(page.count(),'0');}
 {const page=await setup();page.gate();const f=freshFinal(30),rows=series([f,clearFor(f)]);
  const targets={[TARGET]:{revision:1,afterIndex:2,streamRevision:base.streamRevision}};
  page.send(frame(rows,{targetContextRevisions:targets}));assert.equal(page.count(),'unknown');
  const next=freshFinal(31);rows.push(...series([next,clearFor(next)],3,1002));
  page.send(frame(rows,{targetContextRevisions:targets}));assert.equal(page.count(),'0');}
 {const page=await setup();page.gate();const a=freshFinal(40),b=freshFinal(41,'200637');
  const rows=series([a,clearFor(a),freshCreate(40),b,clearFor(b,'200637'),freshCreate(41,'200637')]);page.send(frame(rows));
  const targets={'200637':{revision:1,afterIndex:6,streamRevision:base.streamRevision}};
  page.send(frame(rows,{targetContextRevisions:targets}));assert.equal(page.count(),'1');assert.equal(page.count('200637'),'unknown');
  page.send(frame(rows));assert.equal(page.count(),'1');assert.equal(page.count('200637'),'unknown');}
 {const {page,rows}=await calibrated();rows.push(...series([{type:'UnknownBattleOperation',payloadState:'header-only',payload:null,payloadRevision:0}],3,1002));
  page.send(frame(rows,{targetContextRevisionOverflow:1,targetContextRevisionMapComplete:false,targetContextRevisionOverflowAfterIndex:3,targetContextRevisionOverflowStreamRevision:base.streamRevision}));assert.equal(page.count(),'unknown');}
 {const {page,rows}=await calibrated();const f=freshFinal(50);rows.push(...series([freshCreate(50),f],3,1002));page.send(frame(rows));assert.equal(page.count(),'1','A final creation is not a full16 anchor');}
 {const {page,rows}=await calibrated();const onlyHeader={...series([freshCreate(60)],3,1002)[0],payloadState:'header-only',payload:null};
  page.send(frame([...rows,onlyHeader]));assert.equal(page.count(),'unknown');}
 {const page=await setup({targets:[TARGET],targetNames:{[TARGET]:'手工名字'}});page.gate();
  assert(page.elements.cards.textContent.includes('手工名字'));assert(!page.elements.cards.textContent.includes(TARGET));
  page.callback('targets.current.v1')({state:'ready',data:{context:base.context,targets:[{id:TARGET,label:'准确角色名'}]}});
  assert(page.elements.cards.textContent.includes('准确角色名'));assert(page.elements.cards.children[0].title.includes(TARGET));}
 // Child Buffs belong to this exact passive Skill instance. Unrelated detach
 // events are not permission to invalidate every actor's counter.
 {const {page,rows}=await calibrated();rows.push(...series([freshCreate(70)],3,1002));page.send(frame(rows));assert.equal(page.count(),'1');
  const other={type:'BattleOpSkillDetach',payloadState:'whitelisted-fields',payload:{skill_inst_id:'999777'},payloadRevision:0};
  rows.push(...series([other],4,1003));page.send(frame(rows));assert.equal(page.count(),'1');
  const own={...clone(other),payload:{skill_inst_id:'100664'}};rows.push(...series([own],5,1004));page.send(frame(rows));assert.equal(page.count(),'unknown');
  rows.push(...series([clearFor(rows[0])],6,1005));page.send(frame(rows));assert.equal(page.count(),'unknown','Old final clear cannot cross its parent detach');
  rows.push(...series([freshCreate(71)],7,1006));page.send(frame(rows));assert.equal(page.count(),'unknown','Late old-parent create cannot resurrect detached parent');
  const next=freshFinal(72);next.payload.inst_id='777001';next.payload.trigger_inst_id='777001';
  rows.push(...series([next,clearFor(next)],8,1007));page.send(frame(rows));assert.equal(page.count(),'0');}
 // Synthetic source metadata is intentional for these controlled fault cases;
 // it is never evidence that the original capture contained these headers.
 {const page=await setup();page.gate();const sparse=series([finalCreate,clear,freshCreate(90)]);sparse[2].seqId+=20;const rows=indexedRows(sparse);
  page.send(indexedFrame(rows));assert.equal(page.count(),'1','A healthy captured source sequence must permit a sparse game allocation number');}
 for(const field of ['sourceIndex','operationSha256','captureSequenceRelation']){const page=await setup();page.gate();const rows=indexedRows(series([finalCreate,clear]));
  delete rows[1][field];page.send(indexedFrame(rows));assert.equal(page.count(),'unknown');}
 for(const key of HEALTH_KEYS){const page=await setup();page.gate();const rows=indexedRows(series([finalCreate,clear]));const data=indexedFrame(rows);
  delete data.captureIntegrity.current[key];page.send(data);assert.equal(page.count(),'unknown','All seven health fields are required: '+key);}
 for(const key of HEALTH_KEYS){const page=await setup();page.gate();const rows=indexedRows(series([finalCreate,clear]));
  delete rows[1].captureIntegrity[key];page.send(indexedFrame(rows));assert.equal(page.count(),'unknown','Every operation needs its own health snapshot: '+key);}
 for(const value of [1,0,5]){const page=await setup();page.gate();const rows=indexedRows(series([finalCreate,clear]));rows[1].sourceIndex=value;
  page.send(indexedFrame(rows));assert.equal(page.count(),'unknown','Captured source index repeated/backward/gap must fail');}
 {const page=await setup();page.gate();const rows=indexedRows(series([finalCreate,clear]));rows[0].sourceIndex=10;rows[1].sourceIndex=9;
  page.send(indexedFrame(rows));assert.equal(page.count(),'unknown');assert(page.elements.status.textContent.includes('接收操作序号回退'));}
 for(const seq of [1000,999]){const page=await setup();page.gate();const rows=indexedRows(series([finalCreate,clear]));rows[1].seqId=seq;
  rows[1].sequenceRelation=seq===1000?'repeated':'backward';page.send(indexedFrame(rows));assert.equal(page.count(),'unknown');}
 {const page=await setup();page.gate();const rows=indexedRows(series([finalCreate,clear]));page.send(indexedFrame(rows));assert.equal(page.count(),'0');
  const data=indexedFrame(rows);data.captureIntegrity.current.framingErrors=1;page.send(data);assert.equal(page.count(),'unknown','Health cannot change silently within a revision');}
 {const page=await setup();page.gate();const rows=indexedRows(series([finalCreate,clear]));page.send(indexedFrame(rows));assert.equal(page.count(),'0');
  const data=indexedFrame(rows);data.captureIntegrity.state='changed';data.captureIntegrity.revision=1;data.captureIntegrity.afterIndex=2;
  data.captureIntegrity.current.protobufDecodeErrors=1;page.send(data);assert.equal(page.count(),'unknown');}
 {const page=await setup();page.gate();const rows=indexedRows(series([finalCreate,clear]));page.send(indexedFrame(rows));assert.equal(page.count(),'0');
  const changed=indexedFrame(rows);changed.captureIntegrity.state='changed';changed.captureIntegrity.revision=1;changed.captureIntegrity.afterIndex=2;
  changed.captureIntegrity.current.framingErrors=1;page.send(changed);assert.equal(page.count(),'unknown');
  const f=freshFinal(92),newRows=indexedRows(series([f,clearFor(f)],3,1002)),nextHealth=healthy();nextHealth.framingErrors=1;
  newRows.forEach(op=>{op.captureIntegrity=clone(nextHealth);op.captureIntegrityRevision=1;});rows.push(...newRows);
  const stable={state:'stable',baseline:clone(nextHealth),current:clone(nextHealth),changedFields:[],revision:1,afterIndex:2};
  page.send(indexedFrame(rows,{captureIntegrity:stable}));assert.equal(page.count(),'0','A new stable health segment may recover only after its later complete clear');
  page.send(indexedFrame(rows,{captureIntegrity:stable}));assert.equal(page.count(),'0');}
 for(const key of ['pendingMultipacks','pendingTransportSegments','currentFrameErrors']){const page=await setup();page.gate();const rows=indexedRows(series([finalCreate,clear]));
  const data=indexedFrame(rows);data.captureIntegrity.current[key]=1;data.captureIntegrity.state='pending';page.send(data);assert.equal(page.count(),'unknown');}
 {const page=await setup();page.gate();const rows=indexedRows(series([finalCreate,clear])),bad=healthy();bad.framingErrors=1;bad.currentFrameErrors=1;
  rows.forEach((op,i)=>{op.captureIntegrity=clone(bad);op.captureIntegrityState='pending';op.captureIntegrityRevision=i+1;op.captureSequenceRelation='pending';});
  const data=indexedFrame(rows);data.captureIntegrity={state:'pending',baseline:clone(bad),current:clone(bad),changedFields:[],revision:2,afterIndex:2};
  page.send(data);assert.equal(page.count(),'unknown','Whitelisted clear inside the same bad compound frame cannot recover after the cumulative counter rebases');
  const falselyStable=indexedFrame(rows);falselyStable.captureIntegrity={...clone(data.captureIntegrity),state:'stable'};
  page.send(falselyStable);assert.equal(page.count(),'unknown','currentFrameErrors blocks recovery even if a sender incorrectly labels the segment stable');
  const fresh=healthy();fresh.framingErrors=1;const f=freshFinal(94),next=indexedRows(series([f,clearFor(f)],3,1002));
  next.forEach(op=>{op.captureIntegrity=clone(fresh);op.captureIntegrityRevision=2;});rows.push(...next);
  page.send(indexedFrame(rows,{captureIntegrity:{state:'stable',baseline:clone(fresh),current:clone(fresh),changedFields:[],revision:2,afterIndex:2}}));
  assert.equal(page.count(),'0','Only a later healthy frame and later completed clear may recover');}
 {const page=await setup();page.gate();const rows=indexedRows(series([finalCreate,clear]));page.send(indexedFrame(rows));assert.equal(page.count(),'0');
  page.send(frame(rows,{sourceMode:'raw-operation-header'}));assert.equal(page.count(),'unknown','Indexed source must not downgrade to legacy after missing metadata');}
 {const page=await setup();page.gate();const rows=indexedRows(series([finalCreate,clear]));page.send(indexedFrame(rows));assert.equal(page.count(),'0');
  const next=indexedRows(series([freshCreate(93)],3,1002))[0],pending={...clone(next),payload:null,payloadState:'pending',payloadRevision:0};
  page.send(indexedFrame([...rows,pending]));assert.equal(page.count(),'unknown');next.payloadRevision=1;
  page.send(indexedFrame([...rows,next]));assert.equal(page.count(),'1','Payload completion remains separate from captured-header continuity');}
 {const page=await setup();page.gate();const rows=series([finalCreate,clear,freshCreate(91)]);rows[2].seqId+=20;rows[2].sequenceRelation='gap';
  page.send(frame(rows));assert.equal(page.count(),'unknown','Legacy without source index must still reject game-number jumps');}
 // Fixed real 2026-10-20 normalized payloads, but explicitly SYNTHETIC source
 // index, zero-health baseline and reconstructed-proto digests. This cannot
 // establish the original live transport's completeness or dedup identity.
 const indexedPage=await setup(),indexedMessages=[],indexedSkipsCovered=new Set();
 for(const snapshot of indexedFixture.snapshots){
  indexedPage.callback('capture.context.v1')(snapshot.captureContextEnvelope);
  indexedPage.callback('effects.operations.v1')(snapshot.operationsEnvelope);
  indexedMessages.push(indexedPage.elements.status.textContent);
  if(snapshot.captureContextEnvelope.state==='ready'&&snapshot.operationsEnvelope.state==='ready'){
   const data=snapshot.operationsEnvelope.data;
   for(const op of data.operations)if(op.sequenceRelation==='forward_skip')indexedSkipsCovered.add(JSON.stringify([data.context,op.index]));
  }
  assert(indexedPage.elements.cards.children.every(card=>card.dataset.count==='unknown'),
    'The new real payload prefix has no completed Wolf clear; never fill 16 from the screenshot or game allocation numbers');
 }
 assert.equal(indexedFixture.positiveGameSeqSkips,21);
 assert.equal(indexedSkipsCovered.size,21,'All 21 actual forward skips must be covered by ready public API frames');
 assert(indexedMessages.every(text=>!text.includes('跳号')&&!text.includes('接收操作窗口不连续')&&!text.includes('接收顺序资料不完整')&&!text.includes('游戏编号信息与接收依据不一致')),
   'Healthy synthetic captured indices must not report the 21 real forward allocation skips as received-data gaps: '+JSON.stringify([...new Set(indexedMessages)]));
 console.log(JSON.stringify({realReplay:true,independentNewLiveGameplay:false,sourcePrefixSha256:fixture.source.prefixSha256,
  operationCount:fixture.operationCount,frameCount:fixture.frames.length,zero,sixteen,preclearKnown,countEvents,
  indexedMetadataReplay:{normalizedPayloads:indexedFixture.normalizedOperationCount,snapshots:indexedFixture.snapshots.length,
   positiveGameSeqSkips:indexedFixture.positiveGameSeqSkips,sourceIndexIsSynthetic:true,captureIntegrityIsSynthetic:true,
   forwardSkipsCoveredByReadyFrames:indexedSkipsCovered.size,
   operationDigestsAreSyntheticReconstructedProtos:true,provesOriginalRawTransportIntegrity:false,allCountsRemainUnknown:true,
   statusMessages:[...new Set(indexedMessages)]},
  checks:'native/static/full-graph gates; self-identified clear and identity counterexamples; UID unit/dedup/finish; cap17 rollover; pending revision enrichment; gap/dup/backward; omission/window loss; revision conflict; context/stream; max parameter; target loss/overflow/isolation; exact parent vs unrelated detach'}));
})().catch(error=>{console.error(error.stack||error);process.exitCode=1;});
"""
    result = subprocess.run([node, "-e", driver, str(html), str(fixture), str(data)],
                            capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["realReplay"] and report["zero"] and report["sixteen"]
    output = Path(r"C:\path\to\local-test\endfield-wolfblood-20261019\html-tests")
    output.mkdir(parents=True, exist_ok=True)
    (output / "wolfblood-script-verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
