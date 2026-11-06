package archtest

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os/exec"
	"path/filepath"
	"runtime"
	"slices"
	"strings"
	"testing"
)

const (
	module = "github.com/BullionBear/cpanel"
	mftiko = "github.com/BullionBear/mftiko"
)

// sealboxOpen opens sealed secrets; cpanel-api must not link it (I6).
const sealboxOpen = "internal/platform/sealbox/open"

// netCarriers are the only packages through which the ReconcileLoop may reach net (I5): the Postgres driver, which
// dials the database, and zerolog, which uses only net's IP types.
var netCarriers = []string{"github.com/jackc/pgx", "github.com/rs/zerolog"}

// pkg is what go list says about one package.
type pkg struct {
	ImportPath string
	Imports    []string // direct, without test imports
	Deps       []string // everything it links, transitively
	ForTest    string   // set on a package recompiled for its tests and on an external test package
	Error      *struct{ Err string }
}

// isTest reports whether p is built only for a test: a package recompiled with its tests, an external test
// package, or a generated test main.
func (p pkg) isTest() bool {
	return p.ForTest != "" || strings.HasSuffix(p.ImportPath, ".test")
}

// rel is a path inside the module relative to its root, and whether it is inside the module. A test variant,
// "p [p.test]", is p.
func rel(importPath string) (string, bool) {
	path, _, _ := strings.Cut(importPath, " ")
	if r, ok := strings.CutPrefix(path, module+"/"); ok {
		return r, true
	}
	return "", false
}

// show is how a violation names a package: relative inside the module, the full path outside it.
func show(importPath string) string {
	if r, ok := rel(importPath); ok {
		return r
	}
	return importPath
}

// under reports whether path is one of the prefixes or below one.
func under(path string, prefixes ...string) bool {
	for _, p := range prefixes {
		if path == p || strings.HasPrefix(path, p+"/") {
			return true
		}
	}
	return false
}

// outsideHandOff reports why no cpanel package, test code included, may link the package, or "" (I1).
func outsideHandOff(importPath string) string {
	switch {
	case under(importPath, mftiko) && !under(importPath, mftiko+"/contract"):
		return "cpanel uses only mftiko's contract/ (I1)"
	case under(importPath, "go.etcd.io"):
		return "cpanel does not talk to etcd (I1)"
	}
	return ""
}

// forbiddenInReconcile reports whether the ReconcileLoop must not link the package at all (I5): an HTTP, RPC or
// websocket client. A venue client is one of those.
func forbiddenInReconcile(importPath string) bool {
	return under(importPath, "net/http", "net/rpc", "net/smtp", "connectrpc.com", "google.golang.org/grpc",
		"github.com/valyala/fasthttp", "github.com/coder/websocket", "github.com/gorilla/websocket")
}

// outermost drops every path that is below another path in paths: net/http stands for net/http/httptrace.
func outermost(paths []string) []string {
	var out []string
	for _, p := range paths {
		if !slices.ContainsFunc(paths, func(q string) bool { return q != p && under(p, q) }) {
			out = append(out, p)
		}
	}
	return out
}

// pathToNet returns the import chain from start to net that passes through no netCarrier, or nil if there is none.
func pathToNet(start string, imports map[string][]string) []string {
	parent := map[string]string{start: ""}
	queue := []string{start}
	for len(queue) > 0 {
		cur := queue[0]
		queue = queue[1:]
		for _, imp := range imports[cur] {
			if _, seen := parent[imp]; seen || under(imp, netCarriers...) {
				continue
			}
			parent[imp] = cur
			if imp == "net" {
				var chain []string
				for n := imp; n != ""; n = parent[n] {
					chain = append(chain, n)
				}
				slices.Reverse(chain)
				return chain
			}
			queue = append(queue, imp)
		}
	}
	return nil
}

// check returns one line per broken rule, "<package> links <dependency>: <rule>". A test variant reports only what
// its package does not already report.
func check(pkgs []pkg) []string {
	imports := map[string][]string{}
	for _, p := range pkgs {
		if !p.isTest() {
			imports[p.ImportPath] = p.Imports
		}
	}
	var out []string
	reported := map[[2]string]bool{}
	for _, tests := range []bool{false, true} {
		for _, p := range pkgs {
			from, own := rel(p.ImportPath)
			if !own || p.isTest() != tests || strings.HasSuffix(p.ImportPath, ".test") {
				continue
			}
			label := from
			if tests {
				label += " (test)"
			}
			bad := func(dep, why string) {
				if k := [2]string{from, dep}; !reported[k] {
					reported[k] = true
					out = append(out, fmt.Sprintf("%s links %s: %s", label, show(dep), why))
				}
			}
			for _, d := range p.Deps {
				if why := outsideHandOff(d); why != "" {
					bad(d, why)
				}
			}
			if tests {
				continue
			}
			if under(from, "internal/reconcile") {
				var hits []string
				for _, d := range p.Deps {
					if forbiddenInReconcile(d) {
						hits = append(hits, d)
					}
				}
				for _, d := range outermost(hits) {
					bad(d, "the ReconcileLoop does no I/O but the database (I5)")
				}
				if chain := pathToNet(p.ImportPath, imports); chain != nil {
					how := "imported directly"
					if via := chain[1 : len(chain)-1]; len(via) > 0 {
						names := make([]string, len(via))
						for i, v := range via {
							names[i] = show(v)
						}
						how = "reached through " + strings.Join(names, " → ")
					}
					bad("net", how+"; the ReconcileLoop reaches net only through pgx and zerolog (I5)")
				}
			}
			if from == "cmd/cpanel-api" {
				for _, d := range p.Deps {
					if r, own := rel(d); own && under(r, sealboxOpen) {
						bad(d, "the public layer holds no key that opens a secret (I6)")
					}
				}
			}
		}
	}
	return out
}

// goList runs go list over the whole module and its tests, with every dependency. -e keeps a package that does not
// build, such as one importing mftiko's internal/, so its rules are still checked.
func goList(t *testing.T) []pkg {
	t.Helper()
	goBin, err := exec.LookPath("go")
	if err != nil {
		goBin = filepath.Join(runtime.GOROOT(), "bin", "go")
	}
	gomod, err := exec.Command(goBin, "env", "GOMOD").Output()
	if err != nil {
		t.Fatalf("go env GOMOD: %v", err)
	}
	cmd := exec.Command(goBin, "list", "-e", "-deps", "-test", "-json=ImportPath,Imports,Deps,ForTest,Error", "./...")
	cmd.Dir = filepath.Dir(strings.TrimSpace(string(gomod)))
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	raw, err := cmd.Output()
	if err != nil {
		t.Fatalf("go list: %v\n%s", err, stderr.String())
	}
	var pkgs []pkg
	dec := json.NewDecoder(bytes.NewReader(raw))
	for {
		var p pkg
		if err := dec.Decode(&p); errors.Is(err, io.EOF) {
			break
		} else if err != nil {
			t.Fatalf("decode go list: %v", err)
		}
		pkgs = append(pkgs, p)
	}
	return pkgs
}

func TestDependencyRules(t *testing.T) {
	pkgs := goList(t)
	var own []string
	loadErrs := map[string]bool{}
	for _, p := range pkgs {
		r, ok := rel(p.ImportPath)
		if !ok {
			continue
		}
		if !p.isTest() {
			own = append(own, r)
		}
		if p.Error != nil && !loadErrs[p.Error.Err] {
			loadErrs[p.Error.Err] = true
			t.Errorf("go list %s: %s", r, p.Error.Err)
		}
	}
	// The rules key on paths; if a package moved, they would pass by matching nothing.
	for _, want := range []string{"cmd/cpanel-api", "internal/reconcile", sealboxOpen} {
		if !slices.Contains(own, want) {
			t.Fatalf("package %s not found: update the rules in this package to the new layout", want)
		}
	}
	for _, v := range check(pkgs) {
		t.Error(v)
	}
}

// The checker itself: each kind of violation is reported, and the allowed edges are not.
func TestCheckReportsViolations(t *testing.T) {
	m := func(p string) string { return module + "/" + p }
	mf := func(p string) string { return mftiko + "/" + p }
	variant := func(p string) string { return m(p) + " [" + m(p) + ".test]" }
	pkgs := []pkg{
		// I1
		{ImportPath: m("internal/mftiko"), Deps: []string{mf("contract/gen/api/v1"), mf("contract/wire/cloid"), "github.com/oapi-codegen/runtime"}}, // allowed
		{ImportPath: m("internal/a"), Deps: []string{mf("internal/platform/buildinfo")}},
		{ImportPath: variant("internal/a"), ForTest: m("internal/a"), Deps: []string{mf("internal/platform/buildinfo")}}, // reported once, by internal/a
		{ImportPath: m("internal/b"), Deps: []string{mf("exchange"), mf("exchange/binance")}},
		{ImportPath: m("internal/c"), Deps: []string{mf("migrations"), "go.etcd.io/etcd/client/v3"}},
		{ImportPath: m("internal/d")},
		{ImportPath: variant("internal/d"), ForTest: m("internal/d"), Deps: []string{mf("sdk")}},
		{ImportPath: m("internal/d_test") + " [" + m("internal/d") + ".test]", ForTest: m("internal/d"), Deps: []string{"go.etcd.io/etcd/client/v3"}},
		{ImportPath: m("internal/d") + ".test", Deps: []string{mf("sdk"), "go.etcd.io/etcd/client/v3"}}, // the test main: its test packages report
		{ImportPath: m("internal/e"), Deps: []string{mftiko, "github.com/BullionBear/mftikox"}},
		{ImportPath: "github.com/x/y", Deps: []string{mf("internal/platform/buildinfo"), "net/http"}}, // outside the module: ignored

		// I5
		{ImportPath: m("internal/reconcile"), Imports: []string{"github.com/jackc/pgx/v5", "github.com/rs/zerolog", "log/slog"},
			Deps: []string{"github.com/jackc/pgx/v5", "github.com/jackc/pgx/v5/pgconn", "github.com/rs/zerolog", "crypto/tls", "net", "log/slog"}}, // net through pgx and zerolog: allowed
		{ImportPath: "github.com/jackc/pgx/v5", Imports: []string{"github.com/jackc/pgx/v5/pgconn"}},
		{ImportPath: "github.com/jackc/pgx/v5/pgconn", Imports: []string{"crypto/tls", "net"}},
		{ImportPath: "github.com/rs/zerolog", Imports: []string{"net"}},
		{ImportPath: "crypto/tls", Imports: []string{"net"}},
		{ImportPath: "net/http", Imports: []string{"crypto/tls", "net"}},
		{ImportPath: "log/slog", Imports: []string{"context"}},
		{ImportPath: m("internal/dial"), Imports: []string{"net"}, Deps: []string{"net"}}, // not the ReconcileLoop: free
		{ImportPath: m("internal/reconcile/rules"), Imports: []string{m("internal/dial")}, Deps: []string{m("internal/dial"), "net"}},
		{ImportPath: m("internal/reconcile/direct"), Imports: []string{"net"}, Deps: []string{"net"}},
		{ImportPath: m("internal/reconcile/tls"), Imports: []string{"crypto/tls"}, Deps: []string{"crypto/tls", "net"}},
		{ImportPath: m("internal/reconcile/web"), Imports: []string{"net/http"}, Deps: []string{"crypto/tls", "net", "net/http", "net/http/httptrace"}}, // net/http stands for httptrace
		{ImportPath: m("internal/reconcile/trace"), Deps: []string{"net/http/httptrace"}},
		{ImportPath: m("internal/reconcile/rpc"), Deps: []string{"connectrpc.com/connect", "google.golang.org/grpc", "github.com/valyala/fasthttp",
			"github.com/coder/websocket", "github.com/gorilla/websocket", "net/rpc", "net/smtp"}},
		{ImportPath: variant("internal/reconcile"), ForTest: m("internal/reconcile"), Imports: []string{"net/http/httptest"},
			Deps: []string{"net", "net/http", "net/http/httptest"}}, // test code: I5 does not apply
		{ImportPath: m("internal/reconciler"), Imports: []string{"net/http"}, Deps: []string{"net", "net/http"}}, // another package: free

		// I6
		{ImportPath: m("cmd/cpanel-api"), Deps: []string{m("internal/platform/sealbox"), m("internal/platform/sealbox/open")}},
		{ImportPath: variant("cmd/cpanel-api"), ForTest: m("cmd/cpanel-api"), Deps: []string{m("internal/platform/sealbox/open")}}, // test code: I6 does not apply
		{ImportPath: m("cmd/cpanel-worker"), Deps: []string{m("internal/platform/sealbox"), m("internal/platform/sealbox/open")}},  // the worker opens secrets
	}
	got := check(pkgs)
	want := []string{
		"internal/a links " + mf("internal/platform/buildinfo"),
		"internal/b links " + mf("exchange"),
		"internal/b links " + mf("exchange/binance"),
		"internal/c links " + mf("migrations"),
		"internal/c links go.etcd.io/etcd/client/v3",
		"internal/d (test) links " + mf("sdk"),
		"internal/d_test (test) links go.etcd.io/etcd/client/v3",
		"internal/e links " + mftiko,
		"internal/reconcile/rules links net",
		"internal/reconcile/direct links net",
		"internal/reconcile/tls links net",
		"internal/reconcile/web links net/http",
		"internal/reconcile/web links net",
		"internal/reconcile/trace links net/http/httptrace",
		"internal/reconcile/rpc links connectrpc.com/connect",
		"internal/reconcile/rpc links google.golang.org/grpc",
		"internal/reconcile/rpc links github.com/valyala/fasthttp",
		"internal/reconcile/rpc links github.com/coder/websocket",
		"internal/reconcile/rpc links github.com/gorilla/websocket",
		"internal/reconcile/rpc links net/rpc",
		"internal/reconcile/rpc links net/smtp",
		"cmd/cpanel-api links " + sealboxOpen,
	}
	if len(got) != len(want) {
		t.Errorf("%d violations, want %d:\n%s", len(got), len(want), strings.Join(got, "\n"))
	}
	for _, w := range want {
		if !slices.ContainsFunc(got, func(g string) bool { return strings.HasPrefix(g, w+":") }) {
			t.Errorf("not reported: %s", w)
		}
	}
	for path, via := range map[string]string{
		"internal/reconcile/rules":  "reached through internal/dial;",
		"internal/reconcile/direct": "imported directly;",
		"internal/reconcile/tls":    "reached through crypto/tls;",
		"internal/reconcile/web":    "reached through net/http;",
	} {
		line := path + " links net: " + via
		if !slices.ContainsFunc(got, func(g string) bool { return strings.HasPrefix(g, line) }) {
			t.Errorf("no %q in:\n%s", line, strings.Join(got, "\n"))
		}
	}
}
