import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';

const html = readFileSync(new URL('../src/endfield_pcap/overlay_examples/effects-monitor.html', import.meta.url), 'utf8');
const script = html.split('<script>')[1].split('</script>')[0];

function pageLogic(saved = null) {
  const context = vm.createContext({ localStorage: { getItem: () => JSON.stringify(saved) } });
  // Load the page's actual migration and pure projection helpers, without
  // replacing its functions or pretending to verify browser rendering.
  vm.runInContext(script.slice(script.indexOf("'use strict';"), script.indexOf('const save=')), context);
  vm.runInContext(script.slice(script.indexOf('const shortLabel='), script.indexOf('function refreshCatalog')), context);
  vm.runInContext(script.slice(script.indexOf('function foldObservations'), script.indexOf('function tooltip')), context);
  return context;
}

test('old settings migrate to game icons while retaining target and selected IDs', () => {
  const context = pageLogic({ mode: 'all', effectIds: ['buff_a'], hideUnknown: true });
  assert.equal(vm.runInContext('config.displayMode', context), 'game-icons');
  assert.equal(vm.runInContext('config.mode', context), 'all');
  assert.equal(vm.runInContext('config.effectIds[0]', context), 'buff_a');
  assert.equal(vm.runInContext('config.hideUnknown', context), true);
  assert.equal(vm.runInContext('config.hideUnknownState', context), false);
  assert.equal(vm.runInContext('config.hideDisabled', context), true);
  assert.equal(vm.runInContext('config.displayMode', pageLogic({ displayMode: 'all' })), 'all');
});

test('enable state is explicit and closed or context-unknown rows never become disabled cards', () => {
  const context = pageLogic();
  context.rows = [
    { state: 'observed-open', enabled: true },
    { state: 'observed-open', enabled: false },
    { state: 'observed-open', enabled: null },
    { state: 'observed-open' },
    { state: 'observed-open', enabled: 1 },
    { state: 'observed-closed', enabled: false },
    { state: 'context-unknown', enabled: true },
    { state: 'not-observed' },
  ];
  assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(rows.map(effectState))', context)), [
    'active', 'disabled', 'unknown', 'unknown', 'unknown', 'unavailable', 'unavailable', 'not-observed',
  ]);
  vm.runInContext("config.displayMode='all'", context);
  assert.deepEqual(JSON.parse(vm.runInContext('JSON.stringify(rows.map(row=>visibleObservation(row,false)))', context)), [
    true, false, true, true, true, false, false, false,
  ]);
  assert.equal(vm.runInContext('visibleObservation(rows[1],true)', context), true);
});

test('unknown state and unidentified ID are independent filters, and state stays separate when folded', () => {
  const context = pageLogic();
  context.rows = [
    { namespace: 'client_buff', targetId: 'target_a', state: 'observed-open', enabled: true, canonicalId: 'buff_exact', effectId: '10' },
    { namespace: 'client_buff', targetId: 'target_a', state: 'observed-open', enabled: null, canonicalId: 'buff_exact', effectId: '10' },
    { namespace: 'client_buff', targetId: 'target_a', state: 'observed-open', enabled: false, canonicalId: 'buff_exact', effectId: '10' },
    { namespace: 'client_buff', targetId: 'target_a', state: 'observed-open', enabled: true, canonicalId: null, effectId: 'raw_id' },
  ];
  assert.equal(vm.runInContext('foldObservations(rows).length', context), 4);
  vm.runInContext("config.displayMode='all';config.hideUnknownState=true", context);
  assert.equal(vm.runInContext('visibleObservation(rows[1],true)', context), false);
  assert.equal(vm.runInContext('visibleObservation(rows[3],true)', context), true);
  vm.runInContext('config.hideUnknownState=false;config.hideUnknown=true', context);
  assert.equal(vm.runInContext('visibleObservation(rows[1],true)', context), true);
  assert.equal(vm.runInContext('visibleObservation(rows[3],true)', context), false);
});

test('icons resolve through the catalog dictionary with inline compatibility and without external URLs', () => {
  const context = pageLogic();
  vm.runInContext("catalogIcons={digest_a:'data:image/webp;base64,AAAA'};aliases.set('buff_known',{iconKey:'digest_a'})", context);
  assert.equal(vm.runInContext("effectImage({canonicalId:'buff_known'})", context), 'data:image/webp;base64,AAAA');
  assert.equal(vm.runInContext("effectImage({iconKey:'digest_a'})", context), 'data:image/webp;base64,AAAA');
  assert.equal(vm.runInContext("effectImage({icon:'data:image/png;base64,BBBB'})", context), 'data:image/png;base64,BBBB');
  assert.equal(vm.runInContext("effectImage({iconKey:'missing'})", context), null);
  assert.equal(vm.runInContext("effectImage({icon:'https://example.invalid/icon.png'})", context), null);
  assert.equal(vm.runInContext("effectImage({icon:'data:image/svg+xml;base64,AAAA'})", context), null);
});

test('card construction exposes state, while observation counts stay in details', () => {
  const context = pageLogic();
  context.document = { createElement: tagName => ({
    tagName, className: '', dataset: {}, children: [], attributes: {},
    append(child) { this.children.push(child); },
    setAttribute(name, value) { this.attributes[name] = value; },
  }) };
  // This checks actual element construction only; layout is validated in Qt.
  vm.runInContext(script.slice(script.indexOf('function tooltip'), script.indexOf('function render')), context);
  context.rows = [
    { namespace: 'client_buff', targetId: 'a', state: 'observed-open', enabled: null, canonicalId: 'buff_exact', effectId: '10', instanceId: 'uid_1', stackCount: null, remainingMs: null },
    { namespace: 'client_buff', targetId: 'a', state: 'observed-open', enabled: null, canonicalId: 'buff_exact', effectId: '10', instanceId: 'uid_2', stackCount: null, remainingMs: null },
  ];
  const card = vm.runInContext('makeCard(foldObservations(rows)[0],{label:"测试目标"})', context);
  assert.equal(card.dataset.effectState, 'unknown');
  assert.ok(card.className.includes('state-unknown'));
  assert.equal(card.children.at(-1).textContent, '状态未知');
  assert.ok(card.children.every(child => !String(child.textContent).includes('观测 2 条')));
  assert.ok(card.title.includes('观测 2 条；观测条数不代表层数'));
  assert.ok(card.title.includes('状态未知；层数未知；剩余时长未知'));
  assert.equal(card.dataset.instanceId, '');
});

test('official skill-button progress has a neutral presentation without invented progress or borrowed icons', () => {
  const context = pageLogic();
  context.document = { createElement: tagName => ({
    tagName, className: '', dataset: {}, children: [], attributes: {},
    append(child) { this.children.push(child); },
    setAttribute(name, value) { this.attributes[name] = value; },
  }) };
  vm.runInContext(script.slice(script.indexOf('function tooltip'), script.indexOf('function render')), context);
  vm.runInContext("aliases.set('buff_progress',{canonicalId:'buff_progress',iconPresentation:'skill-button-progress'})", context);
  context.rows = [{ namespace: 'client_buff', targetId: 'a', state: 'observed-open', enabled: null, canonicalId: 'buff_progress', effectId: '10', instanceId: 'uid_1' }];
  const card = vm.runInContext('makeCard(foldObservations(rows)[0],{label:"测试目标"})', context);
  assert.equal(card.children[0].tagName, 'div');
  assert.equal(card.children[0].textContent, '进度');
  assert.ok(card.children.some(child => child.textContent === '技能按钮进度'));
  assert.ok(card.title.includes('未提供独立效果图标；当前进度未知'));
  assert.ok(card.children.every(child => !String(child.textContent).includes('%')));
  vm.runInContext("aliases.get('buff_progress').label='技能按钮进度'", context);
  const namedProgress = vm.runInContext('makeCard(foldObservations(rows)[0],null)', context);
  assert.equal(namedProgress.children.filter(child => child.textContent === '技能按钮进度').length, 1);
  vm.runInContext('rows[0].canonicalId="buff_without_icon"', context);
  const missing = vm.runInContext('makeCard(foldObservations(rows)[0],null)', context);
  assert.equal(missing.children[0].textContent, '?');
});

test('folding requires the same exact identity kind, namespace and target', () => {
  const context = pageLogic();
  context.rows = [
    { namespace: 'client_buff', targetId: 'target_a', canonicalId: 'buff_exact', effectId: '10', instanceId: 'uid_a', stackCount: null },
    { namespace: 'client_buff', targetId: 'target_a', canonicalId: 'buff_exact', effectId: 'buff_exact', instanceId: 'uid_b', stackCount: null },
    { namespace: 'server_buff', targetId: 'target_a', canonicalId: 'buff_exact', effectId: '10', instanceId: 'uid_c', stackCount: null },
    { namespace: 'client_buff', targetId: 'target_b', canonicalId: 'buff_exact', effectId: '10', instanceId: 'uid_d', stackCount: null },
    { namespace: 'client_buff', targetId: 'target_a', canonicalId: null, effectId: 'buff_exact', instanceId: 'uid_e', stackCount: null },
  ];
  assert.equal(vm.runInContext('foldObservations(rows).length', context), 4);
  assert.equal(vm.runInContext('foldObservations(rows)[0].rows.length', context), 2);
  assert.equal(vm.runInContext('foldObservations(rows)[0].rows.every(row=>row.stackCount===null)', context), true);
});

test('icon scope and positive group need confirmed metadata rather than ID text', () => {
  const context = pageLogic();
  vm.runInContext("aliases.set('buff_known', {canonicalId:'buff_known',hasIcon:true,uiGroup:'CommonCharBuff',classificationSource:'official-icon-priority-group',label:null})", context);
  assert.equal(vm.runInContext("gameIcon({canonicalId:'buff_known',effectId:'10'})", context), true);
  assert.equal(vm.runInContext("iconGroup({canonicalId:'buff_known',effectId:'10'})", context), 'buff');
  assert.equal(vm.runInContext("gameIcon({canonicalId:null,effectId:'buff_known',hasIcon:true})", context), false);
  assert.equal(vm.runInContext("iconGroup({canonicalId:null,effectId:'buff_atk_up',classification:'buff'})", context), 'other');
  assert.equal(vm.runInContext("iconGroup({canonicalId:'buff_other',effectId:'20',uiGroup:'AttentionBuff'})", context), 'other');
});

test('saved exact IDs remain bounded and malformed values do not reach subscriptions', () => {
  const context = pageLogic({ effectIds: ['buff_a', 'buff_a', '', 'bad\nvalue', 'x'.repeat(257), 123, ' raw_id '] });
  const actual = JSON.parse(vm.runInContext('JSON.stringify(config.effectIds)', context));
  assert.deepEqual(actual, ['buff_a', 'raw_id']);
});
