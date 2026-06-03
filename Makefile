.PHONY: dashboard
dashboard:
	@uv run vivarium-dashboard serve --port 1111 --host 0.0.0.0
