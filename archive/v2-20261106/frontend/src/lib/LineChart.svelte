<script lang="ts">
	// A dependency-free time-series area/line chart with a hover readout.
	import type { Point } from '$lib/charts';

	export let points: Point[] = [];
	export let unit = '';
	export let height = 220;
	export let color = '#111111';

	// Fixed internal coordinate system; the SVG scales to its container width.
	const W = 640;
	const padL = 8;
	const padR = 8;
	const padT = 12;
	const padB = 22;

	const fmt = (n: number) =>
		n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
	const fmtDate = (t: number) =>
		new Date(t).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
	const fmtDateTime = (t: number) => new Date(t).toLocaleString();

	$: sorted = [...points].sort((a, b) => a.t - b.t);
	$: values = sorted.map((p) => p.value);
	$: tMin = sorted.length ? sorted[0].t : 0;
	$: tMax = sorted.length ? sorted[sorted.length - 1].t : 1;
	$: vMin = values.length ? Math.min(...values) : 0;
	$: vMax = values.length ? Math.max(...values) : 1;
	// Pad the value range so a near-flat series still reads as a band, not a wall.
	$: pad = vMax - vMin || Math.abs(vMax) || 1;
	$: yLo = vMin - pad * 0.08;
	$: yHi = vMax + pad * 0.08;

	const innerW = W - padL - padR;
	$: innerH = height - padT - padB;

	$: xOf = (t: number) => padL + (tMax === tMin ? innerW / 2 : ((t - tMin) / (tMax - tMin)) * innerW);
	$: yOf = (v: number) => padT + (yHi === yLo ? innerH / 2 : (1 - (v - yLo) / (yHi - yLo)) * innerH);

	$: coords = sorted.map((p) => ({ ...p, x: xOf(p.t), y: yOf(p.value) }));
	$: linePath = coords.map((c, i) => `${i ? 'L' : 'M'}${c.x.toFixed(1)},${c.y.toFixed(1)}`).join(' ');
	$: areaPath = coords.length
		? `${linePath} L${coords[coords.length - 1].x.toFixed(1)},${(height - padB).toFixed(1)} ` +
			`L${coords[0].x.toFixed(1)},${(height - padB).toFixed(1)} Z`
		: '';

	let hover: number | null = null;
	let svgEl: SVGSVGElement;

	function onMove(e: PointerEvent) {
		if (!svgEl || coords.length === 0) return;
		const rect = svgEl.getBoundingClientRect();
		const x = ((e.clientX - rect.left) / rect.width) * W;
		let best = 0;
		let bestD = Infinity;
		for (let i = 0; i < coords.length; i++) {
			const d = Math.abs(coords[i].x - x);
			if (d < bestD) {
				bestD = d;
				best = i;
			}
		}
		hover = best;
	}
	const onLeave = () => (hover = null);
</script>

<div class="chart" style="height:{height}px">
	{#if coords.length === 0}
		<div class="empty">No history yet.</div>
	{:else}
		<!-- svelte-ignore a11y_no_static_element_interactions -->
		<svg
			bind:this={svgEl}
			viewBox="0 0 {W} {height}"
			preserveAspectRatio="none"
			role="img"
			aria-label="Asset value history"
			on:pointermove={onMove}
			on:pointerleave={onLeave}
		>
			<defs>
				<linearGradient id="areaGrad" x1="0" y1="0" x2="0" y2="1">
					<stop offset="0%" stop-color={color} stop-opacity="0.18" />
					<stop offset="100%" stop-color={color} stop-opacity="0" />
				</linearGradient>
			</defs>

			{#if coords.length > 1}
				<path d={areaPath} fill="url(#areaGrad)" stroke="none" />
				<path d={linePath} fill="none" stroke={color} stroke-width="2" vector-effect="non-scaling-stroke" />
			{/if}

			{#each coords as pt}
				<circle cx={pt.x} cy={pt.y} r={coords.length === 1 ? 4 : 0} fill={color} />
			{/each}

			{#if hover !== null}
				<line
					x1={coords[hover].x}
					y1={padT}
					x2={coords[hover].x}
					y2={height - padB}
					stroke="var(--text-dim)"
					stroke-width="1"
					stroke-dasharray="3 3"
					vector-effect="non-scaling-stroke"
				/>
				<circle cx={coords[hover].x} cy={coords[hover].y} r="4" fill={color} stroke="var(--white)" stroke-width="1.5" />
			{/if}
		</svg>

		<div class="y-hi">{fmt(yHi)}</div>
		<div class="y-lo">{fmt(yLo)}</div>
		<div class="x-start">{fmtDate(tMin)}</div>
		<div class="x-end">{fmtDate(tMax)}</div>

		{#if hover !== null}
			<div class="tip" style="left:{(coords[hover].x / W) * 100}%">
				<span class="tip-val">{fmt(coords[hover].value)} {unit}</span>
				<span class="tip-date">{fmtDateTime(coords[hover].t)}</span>
			</div>
		{/if}
	{/if}
</div>

<style>
	.chart {
		position: relative;
		width: 100%;
	}
	svg {
		width: 100%;
		height: 100%;
		display: block;
		overflow: visible;
	}
	.empty {
		color: var(--text-muted);
		font-size: 0.875rem;
		padding: 1.5rem 0;
	}
	.y-hi,
	.y-lo,
	.x-start,
	.x-end {
		position: absolute;
		font-size: 0.6875rem;
		color: var(--text-dim);
		font-variant-numeric: tabular-nums;
		pointer-events: none;
	}
	.y-hi {
		top: 0;
		left: 0;
	}
	.y-lo {
		bottom: 1.1rem;
		left: 0;
	}
	.x-start {
		bottom: 0;
		left: 0;
	}
	.x-end {
		bottom: 0;
		right: 0;
	}
	.tip {
		position: absolute;
		top: 0;
		transform: translateX(-50%);
		background: var(--text);
		color: var(--white);
		padding: 0.3rem 0.5rem;
		border-radius: var(--radius-xs);
		font-size: 0.6875rem;
		white-space: nowrap;
		display: flex;
		flex-direction: column;
		gap: 0.05rem;
		pointer-events: none;
		z-index: 2;
	}
	.tip-val {
		font-family: var(--mono);
		font-weight: 700;
	}
	.tip-date {
		opacity: 0.7;
	}
</style>
