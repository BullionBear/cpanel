import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
	blankChoices,
	formFromUnmatched,
	kindsToSubmit,
	type KindChoice,
	type UnmatchedRow
} from './src/lib/accounts.ts';

test('binance blank form submits only checked in-scope kinds', () => {
	const choices = blankChoices('binance');
	assert.deepEqual(kindsToSubmit('binance', choices), []);
	const checked = choices.map((choice) =>
		choice.kind === 'BinanceUM' ? { ...choice, checked: true } : choice
	);
	assert.deepEqual(kindsToSubmit('binance', checked), ['BinanceUM']);
});

test('bybit and okx omit kinds', () => {
	assert.equal(kindsToSubmit('bybit', []), null);
	assert.equal(kindsToSubmit('okx', []), null);
	assert.deepEqual(blankChoices('bybit'), []);
	assert.deepEqual(blankChoices('okx'), []);
});

test('closed kinds are visible and not submitted', () => {
	const identity: UnmatchedRow = {
		exchange: 'binance',
		exch_account_uid: 'uid-mix',
		api_ids: [{ kind: 'Binance' }, { kind: 'BinanceUM' }],
		missing_kinds: []
	};
	const closed = (kind: string): UnmatchedRow => ({
		exchange: 'binance',
		exch_account_uid: 'uid-mix',
		api_ids: [{ kind }],
		missing_kinds: []
	});
	const rows = [identity, closed('BinancePM'), closed('BinanceEO'), closed('BinanceCM')];
	const form = formFromUnmatched(identity, rows);
	assert.ok(form);
	assert.equal(form.exchange, 'binance');
	assert.equal(form.uid, 'uid-mix');
	assert.deepEqual(
		form.choices.map((choice) => [choice.kind, choice.checked, choice.disabled]),
		[
			['Binance', true, false],
			['BinanceUM', true, false],
			['BinanceCM', false, true],
			['BinanceEO', false, true],
			['BinancePM', false, true]
		]
	);
	const forced: KindChoice[] = form.choices.map((choice) => ({ ...choice, checked: true }));
	assert.deepEqual(kindsToSubmit('binance', forced), ['Binance', 'BinanceUM']);
});

test('kind_not_enabled prechecks only missing in-scope kinds', () => {
	const row: UnmatchedRow = {
		exchange: 'binance',
		exch_account_uid: 'uid-1',
		api_ids: [{ kind: 'Binance' }, { kind: 'BinanceUM' }],
		missing_kinds: ['BinanceUM']
	};
	const form = formFromUnmatched(row, [row]);
	assert.ok(form);
	assert.deepEqual(
		form.choices.filter((choice) => choice.checked).map((choice) => choice.kind),
		['BinanceUM']
	);
});

test('missing identity cannot be brought in', () => {
	const row: UnmatchedRow = {
		exchange: null,
		exch_account_uid: null,
		api_ids: [{ kind: 'Binance' }],
		missing_kinds: []
	};
	assert.equal(formFromUnmatched(row, [row]), null);
});

test('bybit bring-in has no checkbox', () => {
	const row: UnmatchedRow = {
		exchange: 'bybit',
		exch_account_uid: 'uid-b',
		api_ids: [{ kind: 'Bybit' }],
		missing_kinds: []
	};
	const form = formFromUnmatched(row, [row]);
	assert.ok(form);
	assert.deepEqual(form.choices, []);
	assert.equal(kindsToSubmit(form.exchange, form.choices), null);
});
