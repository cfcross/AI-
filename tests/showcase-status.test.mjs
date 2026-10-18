import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const page = fs.readFileSync(new URL('../web/showcase.html', import.meta.url), 'utf8');
const script = page.match(/<script>([\s\S]*?)<\/script>/)[1].replace(/poll\(\);\s*$/, '');

test('showcase displays failure/timeout instead of stale understanding progress', () => {
  const elements = new Map();
  const element = () => ({textContent: '', children: [], append() {}, replaceChildren() {}});
  const document = {getElementById(id) {
    if (!elements.has(id)) elements.set(id, element());
    return elements.get(id);
  }, createElement: element};
  const context = vm.createContext({document});
  vm.runInContext(script, context);
  for (const state of ['failed', 'timed_out', 'submission_unknown', 'waiting_desktop']) {
    context.snapshot = {phase: 'ready', bridge_alive: true, history: [], pipeline: {tasks: [{
      state, message: 'specific failure', progress: {stage: 'understanding'},
    }]}};
    vm.runInContext('render(snapshot)', context);
    assert.match(elements.get('progress').textContent, /specific failure/);
    assert.doesNotMatch(elements.get('progress').textContent, /正在整理需求/);
  }
});

test('both production pages contain valid JavaScript', () => {
  for (const file of ['index.html', 'showcase.html']) {
    const html = fs.readFileSync(new URL('../web/' + file, import.meta.url), 'utf8');
    for (const match of html.matchAll(/<script>([\s\S]*?)<\/script>/g)) new vm.Script(match[1]);
  }
});

test('new empty recording does not inherit old generation, ringing or printing', () => {
  const elements = new Map();
  const element = () => ({textContent: '', children: [], append() {}, replaceChildren() {}});
  const document = {getElementById(id) {
    if (!elements.has(id)) elements.set(id, element());
    return elements.get(id);
  }, createElement: element};
  const context = vm.createContext({document, snapshot: {
    phase: 'ready', bridge_alive: true,
    history: [{id: 'new', time: '2026-10-18T20:30:46+08:00', text: '', seconds: 3.38}],
    pipeline: {tasks: [{id: 'old-task', source_id: 'old', state: 'completed',
      created_at: '2026-10-18T12:26:41+00:00', files: [], timing: {elapsed_seconds: 58},
      notification: {message: 'old ring'}, printing: {state: 'submission_unknown', message: 'old error'}}]},
  }});
  vm.runInContext(script, context);
  vm.runInContext('render(snapshot)', context);
  assert.match(elements.get('progress').textContent, /未创建生图任务/);
  assert.match(elements.get('transcript').textContent, /未识别到有效语音/);
  assert.equal(elements.get('elapsed').textContent, '');
  assert.doesNotMatch(elements.get('printing').textContent, /old error/);
  assert.doesNotMatch(elements.get('ring').textContent, /old ring/);
  assert.doesNotMatch(elements.get('step3').className, /done/);
});
