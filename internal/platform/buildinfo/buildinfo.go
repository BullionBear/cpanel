// Package buildinfo is the version and commit /healthz reports. `make build` stamps
// Version and Commit with -ldflags. A build without those flags keeps whatever is
// already set and fills the rest from the module's build info.
package buildinfo

import (
	"runtime/debug"
)

// Set at build time. Empty means init fills them in.
var (
	// Version is the build version. `make build` sets it from git describe.
	Version = ""
	// Commit is the git commit. `make build` sets the short hash.
	Commit = ""
)

func init() {
	info, ok := debug.ReadBuildInfo()
	if Version == "" {
		Version = "dev"
		if ok && info.Main.Version != "" && info.Main.Version != "(devel)" {
			Version = info.Main.Version
		}
	}
	if Commit == "" && ok {
		for _, s := range info.Settings {
			if s.Key == "vcs.revision" && s.Value != "" {
				Commit = s.Value
				if len(Commit) > 12 {
					Commit = Commit[:12]
				}
				break
			}
		}
	}
}
