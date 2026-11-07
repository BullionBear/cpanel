package deploy_test

import (
	"os"
	"strings"
	"testing"
)

func TestReleaseWorkflowStaysOffTheHost(t *testing.T) {
	body := string(read(t, "../.github/workflows/release.yml"))
	for _, want := range []string{
		"ghcr.io/bullionbear/cpanel",
		"packages: write",
		"secrets.GITHUB_TOKEN",
		"secrets.DISCORD_WEBHOOK",
		"cpanel-backend",
	} {
		if want == "cpanel-backend" {
			if strings.Contains(body, want) || strings.Contains(body, "cpanel-frontend") {
				t.Fatalf("release workflow uses a v2 image name:\n%s", body)
			}
			continue
		}
		if !strings.Contains(body, want) {
			t.Fatalf("release workflow missing %q", want)
		}
	}
	lower := strings.ToLower(body)
	for _, banned := range []string{"ssh", "172.237.19.123", "appleboy", "ghcr_token", "docker compose"} {
		if strings.Contains(lower, banned) {
			t.Fatalf("release workflow contains %q", banned)
		}
	}
}

func TestImageIsDistrolessAndNonRoot(t *testing.T) {
	body := string(read(t, "../Dockerfile"))
	for _, want := range []string{
		"CGO_ENABLED=0",
		"gcr.io/distroless/static-debian12",
		"USER nonroot:nonroot",
		"/usr/local/bin/cpanel-api",
		"/out/cpanel-worker",
		"/out/cpanel-migrate",
		"buildinfo.BuiltAt",
	} {
		if !strings.Contains(body, want) {
			t.Fatalf("Dockerfile missing %q", want)
		}
	}
}

func read(t *testing.T, path string) []byte {
	t.Helper()
	body, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return body
}
