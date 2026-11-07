package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"

	"github.com/BullionBear/cpanel/internal/cmdtest"
	"github.com/BullionBear/cpanel/internal/platform/pgtest"
	"github.com/BullionBear/cpanel/migrations"
)

func TestUsage(t *testing.T) {
	bin := cmdtest.Build(t)
	for _, args := range [][]string{nil, {"down"}, {"up", "extra"}, {"-h"}} {
		code, _, stderr := cmdtest.RunOutput(t, bin, nil, args...)
		want := 2
		if len(args) == 1 && args[0] == "-h" {
			want = 0
		}
		if code != want || !strings.Contains(stderr, "up|status") {
			t.Errorf("%q: exit %d, stderr %q", args, code, stderr)
		}
	}
	code, _, stderr := cmdtest.RunOutput(t, bin, nil, "-log-level=loud", "up")
	if code != 2 || !strings.Contains(stderr, "-log-level") {
		t.Errorf("exit %d, stderr %q", code, stderr)
	}
	code, _, stderr = cmdtest.RunOutput(t, bin, nil, "up")
	if code != 2 || !strings.Contains(stderr, "CPANEL_DATABASE_URL") {
		t.Errorf("empty database: exit %d, stderr %q", code, stderr)
	}
}

func TestUpStatusChangedAndConcurrent(t *testing.T) {
	srv := pgtest.Start(t)
	bin := cmdtest.Build(t)
	env := []string{"CPANEL_DATABASE_URL=" + srv.DSN()}

	code, stdout, stderr := cmdtest.RunOutput(t, bin, env, "status")
	if code != 0 || !strings.Contains(stdout, "0001 schema pending") {
		t.Fatalf("status before up: exit %d\n%s\n%s", code, stdout, stderr)
	}
	if strings.Contains(stderr, srv.DSN()) {
		t.Fatalf("database URL in the log:\n%s", stderr)
	}
	var start struct{ Config map[string]string }
	first, _, _ := strings.Cut(stderr, "\n")
	if err := json.Unmarshal([]byte(first), &start); err != nil || start.Config["database"] != "***" {
		t.Fatalf("startup line %q: %v", first, err)
	}

	var wg sync.WaitGroup
	type result struct {
		code           int
		stdout, stderr string
	}
	got := make([]result, 2)
	wg.Add(2)
	for i := range got {
		go func() {
			defer wg.Done()
			got[i].code, got[i].stdout, got[i].stderr = runUp(bin, env)
		}()
	}
	wg.Wait()
	applied := 0
	for _, r := range got {
		if r.code != 0 {
			t.Fatalf("up exit %d\n%s\n%s", r.code, r.stdout, r.stderr)
		}
		applied += strings.Count(r.stdout, "applied 0001_schema\n")
		if !strings.Contains(r.stdout, "applied 0001_schema\n") && r.stdout != "up to date\n" {
			t.Fatalf("up stdout %q", r.stdout)
		}
	}
	if applied != 1 {
		t.Fatalf("applied %d times, want 1", applied)
	}

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	conn, err := pgx.Connect(ctx, srv.DSN())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = conn.Close(context.Background()) })
	var n int
	if err := conn.QueryRow(ctx, `SELECT count(*) FROM meta.schema_migrations`).Scan(&n); err != nil {
		t.Fatal(err)
	}
	if n != 2 {
		t.Fatalf("schema_migrations rows = %d", n)
	}

	code, stdout, stderr = cmdtest.RunOutput(t, bin, env, "up")
	if code != 0 || stdout != "up to date\n" {
		t.Fatalf("rerun: exit %d stdout %q stderr %q", code, stdout, stderr)
	}

	if _, err := conn.Exec(ctx, `UPDATE meta.schema_migrations SET sha256 = $1`, strings.Repeat("ab", 32)); err != nil {
		t.Fatal(err)
	}
	code, stdout, stderr = cmdtest.RunOutput(t, bin, env, "up")
	if code != 1 || !strings.Contains(stderr, "changed") || strings.Contains(stdout, "applied ") {
		t.Fatalf("changed file: exit %d\nstdout %s\nstderr %s", code, stdout, stderr)
	}
	if err := migrations.RequireCurrent(ctx, srv.DSN()); err == nil || !strings.Contains(err.Error(), "changed") {
		t.Fatalf("require current after drift: %v", err)
	}
}

// runUp runs cpanel-migrate up without using testing.T, so two of them can run together.
func runUp(bin string, env []string) (int, string, string) {
	cmd := exec.Command(bin, "up")
	for _, kv := range os.Environ() {
		if !strings.HasPrefix(kv, "CPANEL_") {
			cmd.Env = append(cmd.Env, kv)
		}
	}
	cmd.Env = append(cmd.Env, env...)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	err := cmd.Run()
	if err == nil {
		return 0, stdout.String(), stderr.String()
	}
	var ee *exec.ExitError
	if errors.As(err, &ee) {
		return ee.ExitCode(), stdout.String(), stderr.String()
	}
	return -1, stdout.String(), err.Error() + stderr.String()
}
