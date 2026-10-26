"""Exercise the actual example script's basic projections, without imitating Qt."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_minimal_html_projects_availability_loading_pagination_and_clear():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is unavailable; the actual host is covered by Qt acceptance")
    page = Path(__file__).resolve().parents[1] / "src/endfield_pcap/overlay_examples/data-api-minimal.html"
    driver = r"""
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const scripts = [...fs.readFileSync(process.argv[1], 'utf8').matchAll(/<script>([\s\S]*?)<\/script>/g)].map(x=>x[1]);
class Element {
  constructor(){this.children=[];this.text='';}
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=items;this.text='';}
  set textContent(value){this.text=String(value);this.children=[];}
  get textContent(){return this.text+this.children.map(x=>x.textContent).join(' ');}
  set innerHTML(_){throw new Error('innerHTML is forbidden');}
}
const flush = () => new Promise(resolve=>setImmediate(resolve));
async function setup(query='', effectIds=[]) {
  const elements={status:new Element(),note:new Element(),effects:new Element()}, subscriptions=[];
  const api={getTopics:async()=>['effects.actual.v1','effects.definitions.v1'].map(topic=>({topic})),
    subscribe:(spec,callback)=>{const entry={spec,callback,active:true};subscriptions.push(entry);return()=>{entry.active=false;};}};
  const context=vm.createContext({window:{endfieldOverlay:api,addEventListener(){}},
    document:{getElementById:id=>elements[id],createElement:()=>new Element()},queueMicrotask,console});
  vm.runInContext(scripts[0],context);vm.runInContext('CONFIG.definitionQuery='+JSON.stringify(query),context);
  vm.runInContext('CONFIG.effectIds='+JSON.stringify(effectIds),context);
  vm.runInContext(scripts[1],context);await flush();return {elements,subscriptions};
}
const effect = () => ({effectId:'effect',canonicalId:'canonical',targetId:'9007199254740999',state:'observed-open',
  staticConfig:{maxStack:{configuredValue:99}},initializationParameters:{assignedItems:[{numeric_value:77}]},
  runtimeValues:{stackCount:null,remainingMs:null,variables:{rate:{value:0.125}}},
  fieldAvailability:{stackCount:{state:'unsupported'},remainingMs:{state:'unsupported'},variables:{state:'known'},sourceId:{state:'unknown'}}});
const ready = effects => ({state:'ready',data:{effects,selectionState:'ready'}});
(async()=>{
  const first=await setup();assert.equal(first.subscriptions.length,1);assert.equal(first.subscriptions[0].spec.topic,'effects.actual.v1');
  assert.equal(first.subscriptions[0].spec.filter.target,'party');
  const actual=first.subscriptions[0];actual.callback(ready([effect()]));
  assert(first.elements.effects.textContent.includes('层数：不支持'));
  assert(first.elements.effects.textContent.includes('倍率参数 rate：0.125'));
  assert(!first.elements.effects.textContent.includes('层数：99'));assert(!first.elements.effects.textContent.includes('层数：77'));
  const closed=effect();closed.state='observed-closed';closed.lifecycle={state:'observed-closed'};
  closed.runtimeValues.enabled=true;closed.fieldAvailability.enabled={state:'known'};
  actual.callback(ready([closed]));assert.equal(first.elements.effects.children.length,0);
  const unknown=effect();unknown.fieldAvailability.stackCount.state='unknown';unknown.fieldAvailability.variables.state='unknown';
  actual.callback(ready([unknown]));assert(first.elements.effects.textContent.includes('层数：未知'));
  const zero=effect();zero.runtimeValues.stackCount=0;zero.fieldAvailability.stackCount.state='known';
  actual.callback(ready([zero]));assert(first.elements.effects.textContent.includes('层数：0'));
  actual.callback(ready(Array.from({length:61},effect)));assert.equal(first.elements.effects.children.length,60);
  assert(first.elements.note.textContent.includes('结果已截断'));
  actual.callback({state:'ready',data:{effects:[effect()],selectionState:'ready',matchingEffectCount:500,
    deliveredEffectCount:1,hasMore:true,nextOffset:1,projectionReason:'payload_budget',
    projectionOmittedOpenCount:499,storageOmittedClosedCount:3,omittedStackingGroupCount:1}});
  assert(first.elements.note.textContent.includes('当前显示不完整'));
  assert(first.elements.note.textContent.includes('服务端消息预算'));
  assert(first.elements.note.textContent.includes('效果分组或未解析观测也有省略'));
  actual.callback({state:'ready',data:{effects:[],selectionState:'ready',matchingEffectCount:1,
    deliveredEffectCount:0,hasMore:true,nextOffset:null,projectionReason:'payload_budget'}});
  assert(first.elements.status.textContent.includes('结果不完整'));
  actual.callback({state:'unavailable',reason:'overlay-request-limit',data:null});assert.equal(first.elements.effects.children.length,0);
  assert(first.elements.status.textContent.includes('overlay-request-limit'));
  assert(!first.elements.status.textContent.includes('采集连接'));
  actual.callback(ready([]));assert(first.elements.status.textContent.includes('仍未知'));
  const queried=await setup('generic source');let pending=queried.subscriptions[0];
  assert.equal(pending.spec.topic,'effects.definitions.v1');
  pending.callback({state:'unavailable',reason:'definitions_loading',data:null});await flush();
  assert.equal(queried.subscriptions.length,1);assert(queried.elements.status.textContent.includes('加载'));
  pending.callback({state:'ready',data:{definitions:[{canonicalId:'one'}],hasMore:true,nextOffset:1}});await flush();
  pending=queried.subscriptions[1];assert.equal(pending.spec.filter.offset,1);
  pending.callback({state:'ready',data:{definitions:[{canonicalId:'two'}],hasMore:false,nextOffset:null}});await flush();
  assert.equal(queried.subscriptions[2].spec.topic,'effects.actual.v1');
  assert.deepEqual(Array.from(queried.subscriptions[2].spec.filter.effectIds),['one','two']);
  const empty=await setup('not present');empty.subscriptions[0].callback({state:'ready',data:{definitions:[],hasMore:false}});await flush();
  assert.equal(empty.subscriptions.length,1);assert(empty.elements.status.textContent.includes('未找到'));
  const broken=await setup('generic source');broken.subscriptions[0].callback({state:'unavailable',reason:'definition fingerprint mismatch',data:null});
  assert(broken.elements.status.textContent.includes('definition fingerprint mismatch'));assert.equal(broken.subscriptions.length,1);
  // Reproduce a broad query with real-sized pages, then reject its oversized
  // actual subscription before the bridge silently drops it and times out.
  const broad=await setup('buff'), all=Array.from({length:3422},(_,i)=>({canonicalId:'buff_'+String(i).padStart(4,'0')+'x'.repeat(28)}));
  for(let offset=0;offset<all.length;offset+=100){
    const end=Math.min(offset+100,all.length);
    broad.subscriptions.at(-1).callback({state:'ready',data:{definitions:all.slice(offset,end),hasMore:end<all.length,nextOffset:end<all.length?end:null}});
    await flush();
  }
  assert(broad.subscriptions.every(row=>row.spec.topic==='effects.definitions.v1'));
  assert(broad.elements.status.textContent.includes('超过 32768'));
  assert(broad.elements.status.textContent.includes('缩小 CONFIG 查询'));
  // A spec alone can fit while the complete op/options/IDs wrapper cannot.
  const near=Array.from({length:126},(_,i)=>'buff_'+String(i).padStart(3,'0')+'x'.repeat(248));
  const bare={topic:'effects.actual.v1',filter:{target:'party',effectIds:near,onlyGameIcons:false}};
  assert(JSON.stringify(bare).length<=32768);
  const edge=await setup('',near);assert.equal(edge.subscriptions.length,0);
  assert(edge.elements.status.textContent.includes('超过 32768'));
  console.log('actual HTML script: current values, pagination, full request budget, error reasons, truncation and clear verified');
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run([node, "-e", driver, str(page)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
