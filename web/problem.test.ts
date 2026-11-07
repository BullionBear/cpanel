import assert from 'node:assert/strict';
import { test } from 'node:test';
import { failureMessage, isProblem } from './src/lib/problem.ts';

test('reads a problem detail', () => {
	const body = { type: 'about:blank', title: 'Forbidden', status: 403, detail: 'origin is not allowed', code: 'forbidden' };
	assert.equal(isProblem(body), true);
	assert.equal(failureMessage(body, { ok: false, status: 403 }), 'origin is not allowed');
});

test('ignores a successful response', () => {
	assert.equal(failureMessage(undefined, { ok: true, status: 200 }), undefined);
});

test('falls back when the body is not a problem', () => {
	assert.equal(isProblem('nope'), false);
	assert.equal(failureMessage('nope', { ok: false, status: 500 }), 'HTTP 500');
});
