# cpanel v3. `make help` lists the targets. CI (.github/workflows/ci.yml) runs `make check` on every pull request and
# every push to main (C2-D001).

SHELL := /bin/bash
.DEFAULT_GOAL := build

GO  ?= go
BIN := bin

.PHONY: help fmt vet test build check dev-db

help: ## List targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-8s %s\n",$$1,$$2}'

fmt: ## Format every Go file in place
	$(GO) fmt ./...

vet: ## go vet
	$(GO) vet ./...

# -count=1: internal/archtest reads the source through go list, and the test cache does not see those files change.
test: ## go test with the race detector, uncached; includes internal/archtest (I1, I5, I6)
	$(GO) test -race -count=1 ./...

build: ## Build cpanel-api, cpanel-worker and cpanel-migrate into bin/
	$(GO) build -o $(BIN)/ ./cmd/...

dev-db: ## Start the local Postgres 17 (deploy/dev/compose.yml)
	docker compose -f deploy/dev/compose.yml up -d --wait

check: ## What CI runs: gofmt, go mod tidy, vet, build, test
	@files=$$(git ls-files --cached --others --exclude-standard '*.go'); \
	unformatted=$$(gofmt -l $$files); \
	if [ -n "$$unformatted" ]; then echo "not gofmt-ed, run make fmt:" >&2; echo "$$unformatted" >&2; exit 1; fi
	$(GO) mod tidy -diff
	$(MAKE) --no-print-directory vet build test
