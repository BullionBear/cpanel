package deploy_test

import (
	"regexp"
	"strings"
	"testing"
)

// The host stack (C2-07): images by digest through CPANEL_IMAGE, secrets only as files, nothing published.
func TestHostCompose(t *testing.T) {
	body := string(read(t, "host/compose.yml"))
	for _, want := range []string{
		"name: cpanel",
		"image: ${CPANEL_IMAGE:?",
		"external: true",
		"traefik.http.routers.cpanel.rule=Host(`cpanel.lynxlinkage.com`)",
		"CPANEL_DATABASE_URL_FILE: /run/secrets/database_url",
		"CPANEL_MFTIKO_TOKEN_FILE",
		"CPANEL_SYM_TOKEN_FILE",
		"CPANEL_DISCORD_CLIENT_SECRET_FILE",
		"CPANEL_DISCORD_BOT_TOKEN_FILE",
		"CPANEL_SEALBOX_PUBLIC_KEY_FILE",
		"CPANEL_SEALBOX_PRIVATE_KEY_FILE",
	} {
		if !strings.Contains(body, want) {
			t.Errorf("host/compose.yml missing %q", want)
		}
	}
	for _, banned := range []string{"ports:", ":latest", "bullionbear:", "privileged"} {
		if strings.Contains(body, banned) {
			t.Errorf("host/compose.yml contains %q", banned)
		}
	}
	// A secret's value never goes in the environment: every CPANEL_ variable naming one ends in _FILE.
	secretEnv := regexp.MustCompile(`CPANEL_(DATABASE_URL|MFTIKO_TOKEN|SYM_TOKEN|DISCORD_CLIENT_SECRET|DISCORD_BOT_TOKEN|SEALBOX_PUBLIC_KEY|SEALBOX_PRIVATE_KEY)(_FILE)?:`)
	for _, m := range secretEnv.FindAllStringSubmatch(body, -1) {
		if m[2] == "" {
			t.Errorf("host/compose.yml sets %s directly; use %s_FILE", m[0], strings.TrimSuffix(m[0], ":"))
		}
	}
	worker := body[strings.Index(body, "  cpanel-worker:"):strings.Index(body, "\nsecrets:")]
	if strings.Contains(worker, "traefik") || strings.Contains(worker, "web_proxy") {
		t.Errorf("cpanel-worker is reachable from Traefik:\n%s", worker)
	}
	api := body[strings.Index(body, "  cpanel-api:"):strings.Index(body, "  cpanel-worker:")]
	if strings.Contains(api, "sealbox_private_key") || strings.Contains(api, "SEALBOX_PRIVATE") {
		t.Error("cpanel-api mounts the sealbox private key")
	}
	if strings.Contains(worker, "sealbox_public_key") || strings.Contains(worker, "SEALBOX_PUBLIC") {
		t.Error("cpanel-worker mounts the sealbox public key")
	}
}

func TestDeployScript(t *testing.T) {
	body := string(read(t, "host/deploy.sh"))
	for _, want := range []string{"set -euo pipefail", "cpanel-migrate", "/readyz", "state/previous", "flock", `^sha256:[0-9a-f]{64}$`} {
		if !strings.Contains(body, want) {
			t.Errorf("host/deploy.sh missing %q", want)
		}
	}
	// migrate runs before the switch.
	if strings.Index(body, "compose run --rm --no-deps cpanel-migrate") > strings.Index(body, `switch_to "$digest"`) {
		t.Error("deploy.sh switches before it migrates")
	}
}
