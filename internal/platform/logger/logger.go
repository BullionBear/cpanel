// Package logger builds the zerolog logger every cpanel process uses: JSON, one object per line, each line carrying
// the component, the command's name. The commands write to stderr, as mftiko's do.
package logger

import (
	"errors"
	"io"
	"strings"
	"time"

	"github.com/rs/zerolog"
)

func init() {
	zerolog.TimeFieldFormat = time.RFC3339Nano
	zerolog.TimestampFunc = func() time.Time { return time.Now().UTC() }
}

// Level is a log level that can be set from a flag: trace, debug, info, warn or error.
type Level zerolog.Level

// DefaultLevel is info.
const DefaultLevel = Level(zerolog.InfoLevel)

func (l Level) String() string { return zerolog.Level(l).String() }

// Set parses one of trace, debug, info, warn or error, in any case.
func (l *Level) Set(s string) error {
	for _, lvl := range []zerolog.Level{zerolog.TraceLevel, zerolog.DebugLevel, zerolog.InfoLevel, zerolog.WarnLevel, zerolog.ErrorLevel} {
		if strings.EqualFold(strings.TrimSpace(s), lvl.String()) {
			*l = Level(lvl)
			return nil
		}
	}
	return errors.New("want trace, debug, info, warn or error")
}

// New returns the logger for component, writing to w at level.
func New(w io.Writer, component string, level Level) zerolog.Logger {
	return zerolog.New(w).
		Level(zerolog.Level(level)).
		With().
		Timestamp().
		Str("component", component).
		Logger()
}
