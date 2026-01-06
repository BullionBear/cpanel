<footer class="footer">
	<div class="footer-content">
		<p>&copy; {new Date().getFullYear()} CPanel. All rights reserved.</p>
	</div>
</footer>

<style>
	.footer {
		background: white;
		border-top: 1px solid #e5e7eb;
		padding: 16px 24px;
		height: 60px;
		display: flex;
		align-items: center;
		justify-content: center;
		width: 100%;
		max-width: 100%;
		box-sizing: border-box;
		flex-shrink: 0;
	}

	.footer-content {
		text-align: center;
	}

	.footer-content p {
		margin: 0;
		color: #6b7280;
		font-size: 14px;
	}

	@media (max-width: 768px) {
		.footer {
			padding: 12px 16px;
			height: auto;
			min-height: 60px;
		}

		.footer-content p {
			font-size: 12px;
		}
	}

	@media (max-width: 480px) {
		.footer {
			padding: 10px 12px;
		}
	}
</style>

