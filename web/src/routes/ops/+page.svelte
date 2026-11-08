<script lang="ts">
	import { onMount } from 'svelte';
	import {
		api,
		failureMessage,
		type ErrorClass,
		type Job,
		type JobRun,
		type JobRunStatus,
		type JobStatus,
		type MirrorSource,
		type Stream,
		type StreamStatus
	} from '#lib/api.ts';

	const errorClasses = [
		'auth',
		'rate_limited',
		'ip_banned',
		'unavailable',
		'bad_request',
		'not_found',
		'internal'
	] as const satisfies readonly ErrorClass[];

	const streamLabels: Record<StreamStatus, string> = {
		ok: '正常',
		backing_off: '退避中',
		paused: '暫停'
	};
	const jobLabels: Record<JobStatus | JobRunStatus, string> = {
		pending: '等待',
		running: '執行中',
		succeeded: '成功',
		failed: '失敗',
		cancelled: '取消'
	};

	let leaseKey = $state('');
	let kind = $state('');
	let errorClass = $state<'' | ErrorClass>('');
	let streamStatus = $state<'' | StreamStatus>('');
	let jobStatus = $state<'' | JobStatus>('');
	let runStatus = $state<'' | JobRunStatus>('');
	let from = $state('');
	let to = $state('');

	let streams = $state<Stream[]>([]);
	let jobs = $state<Job[]>([]);
	let runs = $state<JobRun[]>([]);
	let mirrors = $state<MirrorSource[]>([]);
	let nextCursor = $state<string | null>(null);
	let error = $state<string | null>(null);
	let busy = $state(false);

	function text(value: string): string | undefined {
		const trimmed = value.trim();
		return trimmed === '' ? undefined : trimmed;
	}

	function iso(value: string): string | undefined {
		if (!value) return undefined;
		const date = new Date(value);
		if (Number.isNaN(date.getTime())) return undefined;
		return date.toISOString();
	}

	function when(value: string | null | undefined): string {
		if (!value) return '';
		const date = new Date(value);
		if (Number.isNaN(date.getTime())) return value;
		return date.toLocaleString('zh-Hant');
	}

	function shared() {
		return {
			lease_key: text(leaseKey),
			kind: text(kind),
			error_class: errorClass === '' ? undefined : errorClass
		};
	}

	async function load(event?: SubmitEvent): Promise<void> {
		event?.preventDefault();
		error = null;
		busy = true;
		nextCursor = null;
		try {
			const common = shared();
			const [streamResult, jobResult, runResult, mirrorResult] = await Promise.all([
				api.GET('/streams', {
					params: { query: { ...common, status: streamStatus === '' ? undefined : streamStatus } }
				}),
				api.GET('/jobs', {
					params: { query: { ...common, status: jobStatus === '' ? undefined : jobStatus } }
				}),
				api.GET('/job-runs', {
					params: {
						query: {
							...common,
							status: runStatus === '' ? undefined : runStatus,
							from: iso(from),
							to: iso(to)
						}
					}
				}),
				api.GET('/mirror/status')
			]);
			const message =
				failureMessage(streamResult.error, streamResult.response) ??
				failureMessage(jobResult.error, jobResult.response) ??
				failureMessage(runResult.error, runResult.response) ??
				failureMessage(mirrorResult.error, mirrorResult.response);
			if (message || !streamResult.data || !jobResult.data || !runResult.data || !mirrorResult.data) {
				error = message ?? '沒有內容';
				return;
			}
			streams = streamResult.data;
			jobs = jobResult.data;
			runs = runResult.data.runs;
			mirrors = mirrorResult.data.sources;
			nextCursor = runResult.data.next_cursor ?? null;
		} catch {
			error = '連不到 API';
		} finally {
			busy = false;
		}
	}

	async function more(): Promise<void> {
		if (!nextCursor) return;
		error = null;
		busy = true;
		try {
			const result = await api.GET('/job-runs', {
				params: {
					query: {
						...shared(),
						status: runStatus === '' ? undefined : runStatus,
						from: iso(from),
						to: iso(to),
						cursor: nextCursor
					}
				}
			});
			const message = failureMessage(result.error, result.response);
			if (message || !result.data) {
				error = message ?? '沒有內容';
				return;
			}
			runs = [...runs, ...result.data.runs];
			nextCursor = result.data.next_cursor ?? null;
		} catch {
			error = '連不到 API';
		} finally {
			busy = false;
		}
	}

	async function resume(id: number): Promise<void> {
		error = null;
		busy = true;
		try {
			const result = await api.POST('/streams/{id}/resume', { params: { path: { id } } });
			const message = failureMessage(result.error, result.response);
			if (message || !result.data) {
				error = message ?? '沒有內容';
				return;
			}
			await load();
		} catch {
			error = '連不到 API';
		} finally {
			busy = false;
		}
	}

	onMount(() => {
		void load();
	});
</script>

<svelte:head>
	<title>Ops · cpanel</title>
</svelte:head>

<h1>Ops</h1>
<p class="muted">stream、未完成的 job、job 歷史，以及 mirror。恢復暫停的 stream 會寫進 audit。</p>

<form class="row" onsubmit={load}>
	<label>
		lease key
		<input name="lease_key" bind:value={leaseKey} autocomplete="off" />
	</label>
	<label>
		kind
		<input name="kind" bind:value={kind} autocomplete="off" />
	</label>
	<label>
		錯誤類別
		<select bind:value={errorClass}>
			<option value="">全部</option>
			{#each errorClasses as item (item)}
				<option value={item}>{item}</option>
			{/each}
		</select>
	</label>
	<label>
		stream 狀態
		<select bind:value={streamStatus}>
			<option value="">全部</option>
			<option value="ok">正常</option>
			<option value="backing_off">退避中</option>
			<option value="paused">暫停</option>
		</select>
	</label>
	<label>
		job 狀態
		<select bind:value={jobStatus}>
			<option value="">全部</option>
			<option value="pending">等待</option>
			<option value="running">執行中</option>
		</select>
	</label>
	<label>
		歷史狀態
		<select bind:value={runStatus}>
			<option value="">全部</option>
			<option value="succeeded">成功</option>
			<option value="failed">失敗</option>
			<option value="cancelled">取消</option>
		</select>
	</label>
	<label>
		從
		<input type="datetime-local" bind:value={from} />
	</label>
	<label>
		到
		<input type="datetime-local" bind:value={to} />
	</label>
	<button class="primary" type="submit" disabled={busy}>套用</button>
</form>

{#if error}
	<p class="error" role="alert">{error}</p>
{/if}

<h2>Mirror</h2>
{#if mirrors.length === 0}
	<p class="muted">還沒有 mirror 來源。</p>
{:else}
	<div class="scroll">
		<table>
			<thead>
				<tr>
					<th>來源</th>
					<th>最後成功</th>
					<th>載入完成</th>
					<th>筆數</th>
					<th>revision</th>
					<th>remote</th>
					<th>錯誤類別</th>
					<th>錯誤</th>
				</tr>
			</thead>
			<tbody>
				{#each mirrors as source (source.source)}
					<tr>
						<td>{source.source}</td>
						<td>{when(source.last_ok_at)}</td>
						<td>{when(source.last_complete_at)}</td>
						<td>{source.items ?? ''}</td>
						<td>{source.revision ?? ''}</td>
						<td>{source.remote_revision ?? ''}</td>
						<td>{source.last_error_class ?? ''}</td>
						<td class="wrap">{source.last_error ?? ''}</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{/if}

<h2>Streams</h2>
{#if streams.length === 0}
	<p class="muted">沒有符合的 stream。</p>
{:else}
	<div class="scroll">
		<table>
			<thead>
				<tr>
					<th>id</th>
					<th>kind</th>
					<th>lease key</th>
					<th>key</th>
					<th>狀態</th>
					<th>連續失敗</th>
					<th>錯誤類別</th>
					<th>上次成功</th>
					<th></th>
				</tr>
			</thead>
			<tbody>
				{#each streams as stream (stream.id)}
					<tr>
						<td>{stream.id}</td>
						<td>{stream.kind}</td>
						<td>{stream.lease_key}</td>
						<td>{stream.key}</td>
						<td>{streamLabels[stream.status]}</td>
						<td>{stream.consecutive_failures}</td>
						<td>{stream.last_error_class ?? ''}</td>
						<td>{when(stream.last_ok_at)}</td>
						<td>
							{#if stream.status === 'paused'}
								<button type="button" disabled={busy} onclick={() => resume(stream.id)}>恢復</button>
							{/if}
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{/if}

<h2>未完成的 job</h2>
{#if jobs.length === 0}
	<p class="muted">沒有符合的 job。</p>
{:else}
	<div class="scroll">
		<table>
			<thead>
				<tr>
					<th>id</th>
					<th>kind</th>
					<th>lease key</th>
					<th>狀態</th>
					<th>attempt</th>
					<th>入隊</th>
					<th>錯誤類別</th>
				</tr>
			</thead>
			<tbody>
				{#each jobs as job (job.id)}
					<tr>
						<td>{job.id}</td>
						<td>{job.kind}</td>
						<td>{job.lease_key}</td>
						<td>{jobLabels[job.status]}</td>
						<td>{job.attempt}</td>
						<td>{when(job.enqueued_at)}</td>
						<td>{job.last_error_class ?? ''}</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{/if}

<h2>Job 歷史</h2>
{#if runs.length === 0}
	<p class="muted">沒有符合的歷史。</p>
{:else}
	<div class="scroll">
		<table>
			<thead>
				<tr>
					<th>完成</th>
					<th>kind</th>
					<th>lease key</th>
					<th>狀態</th>
					<th>錯誤類別</th>
					<th>錯誤</th>
				</tr>
			</thead>
			<tbody>
				{#each runs as run (`${run.finished_at}:${run.id}`)}
					<tr>
						<td>{when(run.finished_at)}</td>
						<td>{run.kind}</td>
						<td>{run.lease_key}</td>
						<td>{jobLabels[run.status]}</td>
						<td>{run.error_class ?? ''}</td>
						<td class="wrap">{run.error ?? ''}</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
	{#if nextCursor}
		<button type="button" disabled={busy} onclick={more}>下一頁</button>
	{/if}
{/if}

<style>
	h2 {
		font-size: 1.1rem;
		margin: 1.5rem 0 0.5rem;
	}

	.scroll {
		overflow-x: auto;
	}

	td.wrap {
		white-space: normal;
		max-width: 16rem;
	}

	label {
		display: flex;
		flex-direction: column;
		gap: 0.2rem;
		font-size: 0.85rem;
	}
</style>
