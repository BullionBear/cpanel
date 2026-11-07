# cpanel v3. `make help` lists the targets. CI (.github/workflows/ci.yml) runs `make check` on every pull request and
# every push to main (C2-D001).

SHELL := /bin/bash
.DEFAULT_GOAL := build

GO       ?= go
PACKAGE  := github.com/BullionBear/cpanel
BIN      := bin
GEN      := contract/gen
TOOLS    := $(abspath $(BIN)/tools)
NODE_BIN := $(abspath contract/openapi/node_modules/.bin)
SPEC     := contract/openapi/cpanel.v1.yaml

VERSION := $(shell git describe --tags --always --dirty --match='v[0-9]*.[0-9]*.[0-9]*' 2>/dev/null || echo dev)
COMMIT  := $(shell git rev-parse --short=12 HEAD 2>/dev/null || echo "")
LDFLAGS := -X '$(PACKAGE)/internal/platform/buildinfo.Version=$(VERSION)' \
           -X '$(PACKAGE)/internal/platform/buildinfo.Commit=$(COMMIT)'

# Same tool versions as mftiko (C2-03).
OAPI_CODEGEN_VERSION := v2.8.0
OASDIFF_VERSION      := v1.34.1

# What openapi-breaking compares against. CI points it at the pull request's base.
OAS_BASE ?= origin/main
OASDIFF_FAIL_ON ?= ERR

export PATH := $(TOOLS):$(NODE_BIN):$(PATH)

.PHONY: help fmt vet test build check dev-db tools openapi openapi-lint openapi-breaking gen-check

help: ## List targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-18s %s\n",$$1,$$2}'

fmt: ## Format every Go file in place
	$(GO) fmt ./...

vet: ## go vet
	$(GO) vet ./...

# -count=1: internal/archtest reads the source through go list, and the test cache does not see those files change.
test: ## go test with the race detector, uncached; includes internal/archtest (I1, I5, I6)
	$(GO) test -race -count=1 ./...

build: ## Build cpanel-api, cpanel-worker and cpanel-migrate into bin/
	$(GO) build -ldflags "$(LDFLAGS)" -o $(BIN)/ ./cmd/...

dev-db: ## Start the local Postgres 17 (deploy/dev/compose.yml)
	docker compose -f deploy/dev/compose.yml up -d --wait

tools: $(TOOLS)/oapi-codegen $(TOOLS)/oasdiff ## Install oapi-codegen and oasdiff into bin/tools

$(TOOLS)/oapi-codegen:
	mkdir -p $(TOOLS)
	GOBIN=$(TOOLS) $(GO) install github.com/oapi-codegen/oapi-codegen/v2/cmd/oapi-codegen@$(OAPI_CODEGEN_VERSION)

$(TOOLS)/oasdiff:
	mkdir -p $(TOOLS)
	GOBIN=$(TOOLS) $(GO) install github.com/oasdiff/oasdiff@$(OASDIFF_VERSION)

$(NODE_BIN)/redocly: contract/openapi/package-lock.json contract/openapi/package.json
	npm ci --prefix contract/openapi

openapi-lint: $(NODE_BIN)/redocly ## Redocly lint for cpanel.v1
	redocly lint --config contract/openapi/redocly.yaml $(SPEC)

openapi: tools $(NODE_BIN)/redocly ## Lint cpanel.v1 and regenerate Go; commit the result
	$(MAKE) --no-print-directory openapi-lint
	rm -rf $(GEN)/cpanel
	mkdir -p $(GEN)/cpanel/v1
	oapi-codegen --config contract/openapi/oapi-codegen-types.yaml $(SPEC)
	oapi-codegen --config contract/openapi/oapi-codegen-server.yaml $(SPEC)
	$(GO) mod tidy

# A missing spec on the base is skipped. A base that is not a commit is an error.
openapi-breaking: tools ## oasdiff breaking against OAS_BASE; fails on a breaking change
	@set -euo pipefail; \
	base='$(OAS_BASE)'; \
	spec='$(SPEC)'; \
	if ! git rev-parse --verify --quiet "$${base}^{commit}" >/dev/null 2>&1; then \
	  echo "openapi-breaking: $$base is not a commit" >&2; \
	  exit 1; \
	fi; \
	if ! git cat-file -e "$$base:$$spec" 2>/dev/null; then \
	  echo "openapi-breaking: $$base has no $$spec; skipping oasdiff"; \
	  exit 0; \
	fi; \
	args=(breaking --color never "$$base:$$spec" "$$spec"); \
	if [ -n '$(OASDIFF_FAIL_ON)' ]; then \
	  args+=(--fail-on '$(OASDIFF_FAIL_ON)'); \
	fi; \
	oasdiff "$${args[@]}"

# Regenerates, then compares with what was committed. The fresh output stays in place.
gen-check: tools $(NODE_BIN)/redocly ## Fail if contract/gen, go.mod or go.sum is stale
	@before=$$(mktemp -d) && trap 'rm -rf "$$before"' EXIT && \
	cp -R $(GEN) "$$before/gen" && cp go.mod go.sum "$$before/" && \
	$(MAKE) --no-print-directory openapi >/dev/null && \
	if diff -r "$$before/gen" $(GEN) >/dev/null && \
	   cmp -s "$$before/go.mod" go.mod && cmp -s "$$before/go.sum" go.sum; then \
	  echo "generated code is up to date"; \
	else \
	  echo "generated code is stale: run make openapi, then commit the result" >&2; \
	  diff -ru "$$before/gen" $(GEN) | head -80 >&2; \
	  diff -u "$$before/go.mod" go.mod >&2; \
	  exit 1; \
	fi

check: ## What CI runs: gofmt, tidy, OpenAPI lint, oasdiff, gen-check, vet, build, test
	@files=$$(git ls-files --cached --others --exclude-standard '*.go'); \
	unformatted=$$(gofmt -l $$files); \
	if [ -n "$$unformatted" ]; then echo "not gofmt-ed, run make fmt:" >&2; echo "$$unformatted" >&2; exit 1; fi
	$(GO) mod tidy -diff
	$(MAKE) --no-print-directory openapi-lint openapi-breaking gen-check vet build test
