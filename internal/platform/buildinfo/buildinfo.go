// Package buildinfo is the version, commit, and build time /healthz reports.
// `make build` and the image Dockerfile stamp Version, Commit, and BuiltAt with
// -ldflags. A build without those flags keeps whatever is already set and fills
// the rest from the module's build info.
package buildinfo

import (
	"runtime/debug"
	"time"
)

// Set at build time. Empty means init fills them in.
var (
	// Version is the build version. `make build` sets it from git describe.
	Version = ""
	// Commit is the git commit. `make build` sets the 12-character hash, which is also the image tag on main.
	Commit = ""
	// BuiltAt is the UTC build time, RFC3339. The image workflow stamps it.
	BuiltAt = ""
)

func init() {
	info, ok := debug.ReadBuildInfo()
	if Version == "" {
		Version = "dev"
		if ok && info.Main.Version != "" && info.Main.Version != "(devel)" {
			Version = info.Main.Version
		}
	}
	if !ok {
		return
	}
	for _, s := range info.Settings {
		switch s.Key {
		case "vcs.revision":
			if Commit == "" && s.Value != "" {
				Commit = s.Value
				if len(Commit) > 12 {
					Commit = Commit[:12]
				}
			}
		case "vcs.time":
			// Commit time, used only when the build did not stamp a build time.
			if BuiltAt == "" && s.Value != "" {
				BuiltAt = s.Value
			}
		}
	}
}

// Time parses BuiltAt. An empty or unparseable value is the zero time.
func Time() time.Time {
	if BuiltAt == "" {
		return time.Time{}
	}
	parsed, err := time.Parse(time.RFC3339, BuiltAt)
	if err != nil {
		return time.Time{}
	}
	return parsed.UTC()
}
