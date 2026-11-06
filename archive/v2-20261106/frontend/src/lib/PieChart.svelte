<script lang="ts">
	// A dependency-free donut chart. Each slice is drawn as an arc of a single
	// circle using stroke-dasharray, rotated so the ring starts at 12 o'clock.
	import type { Slice } from '$lib/charts';

	export let slices: Slice[] = [];
	export let size = 200;
	export let thickness = 26;
	export let unit = '';

	const PALETTE = [
		'#111111',
		'#d4ff00',
		'#4f8cff',
		'#ff8a5b',
		'#7c5cff',
		'#16a34a',
		'#e5484d',
		'#f2b705',
		'#00b3a4',
		'#b06ab3'
	];

	const fmt = (n: number) =>
		n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

	$: total = slices.reduce((s, sl) => s + Math.max(0, sl.value), 0);
	$: r = (size - thickness) / 2;
	$: c = size / 2;
	$: circumference = 2 * Math.PI * r;

	// Precompute each arc's dash length and starting offset around the ring.
	$: segments = (() => {
		let acc = 0;
		return slices
			.filter((sl) => sl.value > 0)
			.map((sl, i) => {
				const frac = total > 0 ? sl.value / total : 0;
				const seg = {
					...sl,
					color: sl.color ?? PALETTE[i % PALETTE.length],
					dash: frac * circumference,
					offset: acc * circumference,
					pct: frac * 100
				};
				acc += frac;
				return seg;
			});
	})();
</script>

<div class="pie">
	{#if total <= 0}
		<div class="empty">No priced holdings to chart.</div>
	{:else}
		<svg width={size} height={size} viewBox="0 0 {size} {size}" role="img" aria-label="Portfolio composition">
			<g transform="rotate(-90 {c} {c})">
				<circle cx={c} cy={c} {r} fill="none" stroke="var(--border-light)" stroke-width={thickness} />
				{#each segments as seg}
					<circle
						cx={c}
						cy={c}
						{r}
						fill="none"
						stroke={seg.color}
						stroke-width={thickness}
						stroke-dasharray="{seg.dash} {circumference - seg.dash}"
						stroke-dashoffset={-seg.offset}
					/>
				{/each}
			</g>
			<text x={c} y={c - 4} text-anchor="middle" class="center-value">{fmt(total)}</text>
			<text x={c} y={c + 14} text-anchor="middle" class="center-unit">{unit}</text>
		</svg>

		<ul class="legend">
			{#each segments as seg}
				<li>
					<span class="swatch" style="background:{seg.color}"></span>
					<span class="l-label" title={seg.label}>{seg.label}</span>
					<span class="l-pct">{seg.pct.toFixed(1)}%</span>
					<span class="l-val">{fmt(seg.value)}</span>
				</li>
			{/each}
		</ul>
	{/if}
</div>

<style>
	.pie {
		display: flex;
		align-items: center;
		gap: 1.25rem;
		flex-wrap: wrap;
	}
	svg {
		flex: 0 0 auto;
	}
	.center-value {
		font-family: var(--mono);
		font-size: 0.95rem;
		font-weight: 700;
		fill: var(--text);
	}
	.center-unit {
		font-size: 0.6875rem;
		fill: var(--text-dim);
	}
	.empty {
		color: var(--text-muted);
		font-size: 0.875rem;
		padding: 1.5rem 0;
	}
	.legend {
		list-style: none;
		margin: 0;
		padding: 0;
		flex: 1;
		min-width: 12rem;
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
	}
	.legend li {
		display: grid;
		grid-template-columns: 0.75rem 1fr auto auto;
		align-items: center;
		gap: 0.5rem;
		font-size: 0.8125rem;
	}
	.swatch {
		width: 0.75rem;
		height: 0.75rem;
		border-radius: 3px;
	}
	.l-label {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.l-pct {
		color: var(--text-dim);
		font-variant-numeric: tabular-nums;
	}
	.l-val {
		font-family: var(--mono);
		font-variant-numeric: tabular-nums;
		color: var(--text-muted);
	}
</style>
