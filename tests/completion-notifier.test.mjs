import {test} from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {CompletionNotifier} from '../sip/completion-notifier.mjs';

function setup() {
  const phone = new EventEmitter(), events = [];
  phone.dial = () => (phone.call = {});
  phone.play = async () => {};
  const notifier = new CompletionNotifier(phone, (event, value) => events.push(value), Buffer.from([1]), 10);
  phone.hangup = reason => {const call = phone.call; phone.call = null; notifier.ended(call, reason);};
  return {phone, events, notifier};
}
test('busy call is not interrupted, reminder can be retried later', () => {
  const {phone, events, notifier} = setup();
  phone.call = {incoming: true};
  notifier.request('id', true);
  assert.equal(events[0].state, 'pending');
  assert.ok(phone.call.incoming);
  phone.call = null;
  notifier.request('id', true);
  assert.equal(phone.call.purpose, 'completion');
  phone.call.ringStarted = true;
  phone.emit('signal', {code:180});
  phone.hangup('confirmation_complete');
  notifier.request('id', true);
  assert.equal(phone.call, null);
  assert.equal(events.at(-1).state, 'done');
});
test('answering notification is handled separately from incoming recording', async () => {
  const {phone, events, notifier} = setup();
  assert.equal(notifier.connected({}), false);
  notifier.request('id', true);
  phone.call.connected = true;
  assert.equal(notifier.connected(phone.call), true);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(phone.call, null);
  assert.equal(events.at(-1).state, 'done');
});
test('no ring acknowledgement is reported as failed, not successful', () => {
  const {phone, events, notifier} = setup();
  notifier.request('id', true);
  phone.hangup('unanswered');
  assert.equal(events.at(-1).state, 'failed');
});
