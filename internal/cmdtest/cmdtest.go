// Package cmdtest builds a cpanel command and runs it as a child process, for the commands' end-to-end tests. The
// child's stderr is read as the JSON log lines the commands write.
package cmdtest

import (
	"bufio"
	"bytes"
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

// timeout bounds each wait on the child.
const timeout = 30 * time.Second

// Build compiles the command in the current directory, the test's package, and returns the binary's path.
func Build(t *testing.T) string {
	t.Helper()
	wd, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	bin := filepath.Join(t.TempDir(), filepath.Base(wd))
	goBin, err := exec.LookPath("go")
	if err != nil {
		goBin = filepath.Join(runtime.GOROOT(), "bin", "go")
	}
	if out, err := exec.Command(goBin, "build", "-o", bin, ".").CombinedOutput(); err != nil {
		t.Fatalf("go build: %v\n%s", err, out)
	}
	return bin
}

// command is bin with args, in this process's environment without its CPANEL_ variables plus env, so the test sees
// only the settings it gives.
func command(bin string, env []string, args ...string) *exec.Cmd {
	cmd := exec.Command(bin, args...)
	for _, kv := range os.Environ() {
		if !strings.HasPrefix(kv, "CPANEL_") {
			cmd.Env = append(cmd.Env, kv)
		}
	}
	cmd.Env = append(cmd.Env, env...)
	return cmd
}

func exitCode(t *testing.T, err error) int {
	t.Helper()
	var ee *exec.ExitError
	if errors.As(err, &ee) {
		return ee.ExitCode()
	}
	if err != nil {
		t.Fatal(err)
	}
	return 0
}

// Run runs bin to completion and returns its exit code and stderr.
func Run(t *testing.T, bin string, env []string, args ...string) (int, string) {
	t.Helper()
	code, _, stderr := RunOutput(t, bin, env, args...)
	return code, stderr
}

// RunOutput is Run that also returns stdout.
func RunOutput(t *testing.T, bin string, env []string, args ...string) (int, string, string) {
	t.Helper()
	cmd := command(bin, env, args...)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	done := make(chan error, 1)
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	go func() { done <- cmd.Wait() }()
	select {
	case err := <-done:
		return exitCode(t, err), stdout.String(), stderr.String()
	case <-time.After(timeout):
		_ = cmd.Process.Kill()
		t.Fatalf("%s did not exit within %v", filepath.Base(bin), timeout)
		return 0, "", ""
	}
}

// Process is a running command.
type Process struct {
	cmd   *exec.Cmd
	lines chan string   // stderr, one line each; closed at EOF
	done  chan struct{} // closed once the process has exited
	err   error         // cmd.Wait's result, set before done is closed
}

// Start runs bin with args in the background. The process is killed when the test ends.
func Start(t *testing.T, bin string, env []string, args ...string) *Process {
	t.Helper()
	cmd := command(bin, env, args...)
	stderr, err := cmd.StderrPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	p := &Process{cmd: cmd, lines: make(chan string, 64), done: make(chan struct{})}
	go func() {
		sc := bufio.NewScanner(stderr)
		for sc.Scan() {
			p.lines <- sc.Text()
		}
		close(p.lines)
		p.err = cmd.Wait()
		close(p.done)
	}()
	t.Cleanup(func() {
		_ = cmd.Process.Kill()
		p.drain()
	})
	return p
}

// drain discards the rest of stderr and waits for the process to exit.
func (p *Process) drain() {
	go func() {
		for range p.lines {
		}
	}()
	<-p.done
}

// Log waits for the next log line whose message is msg and returns it, skipping the lines before it. Every line
// read must be one JSON object.
func (p *Process) Log(t *testing.T, msg string) map[string]any {
	t.Helper()
	deadline := time.After(timeout)
	for {
		select {
		case line, ok := <-p.lines:
			if !ok {
				t.Fatalf("stderr ended before a %q line", msg)
			}
			var m map[string]any
			if err := json.Unmarshal([]byte(line), &m); err != nil {
				t.Fatalf("stderr line is not JSON: %q", line)
			}
			if m["message"] == msg {
				return m
			}
		case <-deadline:
			t.Fatalf("no %q line within %v", msg, timeout)
		}
	}
}

// Signal sends sig to the process.
func (p *Process) Signal(t *testing.T, sig os.Signal) {
	t.Helper()
	if err := p.cmd.Process.Signal(sig); err != nil {
		t.Fatal(err)
	}
}

// Exit waits for the process to exit and returns its exit code. The rest of stderr is discarded.
func (p *Process) Exit(t *testing.T) int {
	t.Helper()
	exited := make(chan struct{})
	go func() {
		p.drain()
		close(exited)
	}()
	select {
	case <-exited:
		return exitCode(t, p.err)
	case <-time.After(timeout):
		t.Fatalf("%s did not exit within %v", filepath.Base(p.cmd.Path), timeout)
		return 0
	}
}
