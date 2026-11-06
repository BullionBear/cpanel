// Command cpanel-migrate will apply cpanel's embedded SQL migrations before a new release starts (ARCHITECTURE
// §4.8, C2-D003). The migrations, the database settings and the subcommands come with C2-02; until then it reads
// its settings, logs them, and exits 0.
//
// Settings are flags whose defaults come from environment variables; a flag wins over its variable:
//
//	-log-level  $CPANEL_LOG_LEVEL  trace, debug, info, warn or error, default info
package main

import (
	"errors"
	"flag"
	"fmt"
	"os"

	"github.com/BullionBear/cpanel/internal/platform/config"
	"github.com/BullionBear/cpanel/internal/platform/logger"
)

const name = "cpanel-migrate"

func main() {
	level := logger.DefaultLevel
	set := config.New(name, os.Getenv)
	set.Var(&level, "log-level", "CPANEL_LOG_LEVEL", "trace, debug, info, warn or error")
	err := set.Parse(os.Args[1:])
	if errors.Is(err, flag.ErrHelp) {
		return
	}
	if err != nil {
		fmt.Fprintf(os.Stderr, "%s: %v\n", name, err)
		os.Exit(2)
	}
	log := logger.New(os.Stderr, name, level)
	log.Info().Dict("config", set.Dict()).Msg("starting")
	log.Info().Msg("no migrations yet")
}
