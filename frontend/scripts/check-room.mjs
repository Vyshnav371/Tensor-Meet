import assert from 'node:assert/strict';
import { readFile, writeFile, mkdtemp, rm } from 'node:fs/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { isAbsolute, join, relative } from 'node:path';
import ts from 'typescript';
import '@angular/compiler';

const root = fileURLToPath(new URL('../', import.meta.url));
const temporary = await mkdtemp(join(root, '.room-check-'));
const originalTimeout = globalThis.setTimeout;
const originalClearTimeout = globalThis.clearTimeout;
const pendingTimers = new Map();
let timerId = 0;
const sockets = [];
class Socket {
  static OPEN = 1;
  readyState = Socket.OPEN;
  sent = [];
  constructor(url) {
    this.url = url;
    sockets.push(this);
  }
  close() {
    this.readyState = 3;
    this.onclose?.();
  }
  send(message) {
    this.sent.push(JSON.parse(message));
  }
  emit(event, data) {
    this.onmessage?.({ data: JSON.stringify({ event, data }) });
  }
}
globalThis.WebSocket = Socket;
globalThis.location = { protocol: 'http:', host: 'localhost:8000' };
globalThis.setTimeout = (callback) => {
  pendingTimers.set(++timerId, callback);
  return timerId;
};
globalThis.clearTimeout = (id) => {
  pendingTimers.delete(id);
};

try {
  const wakeSource = await readFile(join(root, 'src/app/wake-word.ts'), 'utf8');
  const wakeOutput = join(temporary, 'wake-word.mjs');
  await writeFile(
    wakeOutput,
    ts.transpileModule(wakeSource, {
      compilerOptions: {
        target: ts.ScriptTarget.ES2022,
        module: ts.ModuleKind.ES2022,
      },
    }).outputText,
  );
  const { hasWakeWord } = await import(pathToFileURL(wakeOutput).href);
  for (const phrase of [
    'Hey, tensor.',
    'Tensor.',
    'Hey. Tensor.',
    'Hey: Tensor!',
    '“Tensor.”',
    'Hi, Tenser.',
    'Hello Tensor.',
    'Okay, Tenzor.',
    'Hey Tensar.',
    'Tensir.',
    'Hey, Ten sir.',
    'Hey ten-zer.',
    'Hey, dancer.',
  ]) {
    assert.equal(hasWakeWord(phrase), true, phrase);
    assert.equal(hasWakeWord(phrase + ' What was our success rate?'), true, phrase);
  }
  for (const phrase of [
    'The tensor library is fast',
    'We use TensorFlow',
    'Dancer, plan the tasks',
    'The dancer is here',
    'The tensorboard report is ready',
    'Sarah, go ahead',
    'We hired a dancer',
    'A dancer is joining the meeting',
  ]) {
    assert.equal(hasWakeWord(phrase), false, phrase);
  }
  const source = await readFile(join(root, 'src/app/room.service.ts'), 'utf8');
  const compiled = ts.transpileModule(source, {
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ES2022,
      experimentalDecorators: true,
    },
  });
  const output = join(temporary, 'room.mjs');
  await writeFile(
    output,
    compiled.outputText.replace(/(['"])\.\/wake-word\1/g, "'./wake-word.mjs'"),
  );
  const { Room } = await import(pathToFileURL(output).href);
  const room = new Room();
  room.join('design-review', 'Alex Morgan');
  const first = sockets.at(-1);
  assert.match(first.url, /\/ws\/design-review\?name=Alex%20Morgan$/);
  first.onopen();
  assert.equal(room.connection(), 'live');
  assert.equal(room.micOn(), false);
  first.emit('snapshot', {
    meeting: { tensor: { status: 'thinking' } },
    participants: [{ name: 'Alex Morgan', connected: true }],
    transcript: [{ id: 's1', text: 'Meeting context' }],
    chat: [],
    documents: [],
    cards: [
      { card_id: 'review', status: 'PENDING_REVIEW' },
      { card_id: 'done', status: 'EXECUTED' },
    ],
  });
  assert.equal(room.pendingCount(), 1);
  assert.equal(room.tensor().status, 'thinking');
  room.sendChat('@Tensor find the policy');
  assert.deepEqual(first.sent.at(-1), { type: 'chat', text: '@Tensor find the policy' });

  room.join('next-meeting', 'Alex Morgan');
  const second = sockets.at(-1);
  second.onopen();
  assert.equal(room.connection(), 'live');
  assert.equal(room.transcript().length, 0);
  assert.equal(room.pendingCount(), 0);
  assert.equal(room.tensor().status, 'idle');
  first.emit('chat', { id: 'stale', text: 'Previous meeting' });
  first.onclose();
  assert.equal(room.chat().length, 0);
  assert.equal(room.connection(), 'live');
  assert.equal(pendingTimers.size, 0);

  second.emit('chat', { id: 'current', text: 'Current meeting' });
  assert.equal(room.chat()[0].id, 'current');
  second.onclose();
  assert.equal(room.connection(), 'reconnecting');
  assert.equal(pendingTimers.size, 1);
  room.leave();
  assert.equal(room.connection(), 'offline');
  assert.equal(pendingTimers.size, 0);
  assert.equal(room.micOn(), false);
  console.log(
    'Room checks passed: wake variants, join, live sync, chat, state reset, stale events, reconnect cleanup.',
  );
} finally {
  globalThis.setTimeout = originalTimeout;
  globalThis.clearTimeout = originalClearTimeout;
  const cleanupPath = relative(root, temporary);
  assert.ok(
    cleanupPath && !cleanupPath.startsWith('..') && !isAbsolute(cleanupPath),
    'Cleanup must stay inside the frontend workspace',
  );
  await rm(temporary, { recursive: true, force: true });
}
