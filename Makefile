.PHONY: all sync frontend

all: sync

sync:
	uv sync

# Dev server: serves UI on :5173 with hot-reload, proxies /api and /ws to the
# mleash daemon on :7612. Run `uv run mleash start --foreground` first.
frontend:
	cd frontend && npm run dev
