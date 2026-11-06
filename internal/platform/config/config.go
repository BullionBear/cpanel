// Package config binds a command's settings to flags whose defaults come from environment variables, as the mftiko
// commands do: a flag on the command line wins, then the environment variable, then the built-in default. Every
// value goes through its flag's parser, so a bad environment variable fails startup the way a bad flag does.
//
// Dict lists the effective settings for the startup log. A secret that is set is shown as Masked; one that is not
// set is shown as "", so the log still tells the two apart.
package config

import (
	"flag"
	"fmt"
	"time"

	"github.com/rs/zerolog"
)

// Masked stands in for a secret that is set.
const Masked = "***"

// Set is one command's settings.
type Set struct {
	fs       *flag.FlagSet
	getenv   func(string) string
	settings []setting
}

type setting struct {
	name, env string
	secret    bool
}

// New returns an empty Set for the command name. getenv is os.Getenv outside tests; nil reads no environment.
func New(name string, getenv func(string) string) *Set {
	if getenv == nil {
		getenv = func(string) string { return "" }
	}
	return &Set{fs: flag.NewFlagSet(name, flag.ContinueOnError), getenv: getenv}
}

// String registers a string setting.
func (s *Set) String(p *string, name, env, value, usage string) {
	s.fs.StringVar(p, name, value, withEnv(usage, env))
	s.add(name, env, false)
}

// Secret registers a string setting that Dict never shows. It has no default.
func (s *Set) Secret(p *string, name, env, usage string) {
	s.fs.StringVar(p, name, "", withEnv(usage, env))
	s.add(name, env, true)
}

// Duration registers a setting parsed by time.ParseDuration.
func (s *Set) Duration(p *time.Duration, name, env string, value time.Duration, usage string) {
	s.fs.DurationVar(p, name, value, withEnv(usage, env))
	s.add(name, env, false)
}

// Var registers a setting of any flag.Value; its current value is the default.
func (s *Set) Var(v flag.Value, name, env, usage string) {
	s.fs.Var(v, name, withEnv(usage, env))
	s.add(name, env, false)
}

func (s *Set) add(name, env string, secret bool) {
	s.settings = append(s.settings, setting{name: name, env: env, secret: secret})
}

func withEnv(usage, env string) string {
	return fmt.Sprintf("%s ($%s)", usage, env)
}

// Parse reads args, then fills every setting not given on the command line from its environment variable. -h
// returns flag.ErrHelp after printing the usage. The commands take no positional arguments.
func (s *Set) Parse(args []string) error {
	if err := s.fs.Parse(args); err != nil {
		return err
	}
	if s.fs.NArg() > 0 {
		return fmt.Errorf("unexpected argument %q", s.fs.Arg(0))
	}
	given := map[string]bool{}
	s.fs.Visit(func(f *flag.Flag) { given[f.Name] = true })
	for _, st := range s.settings {
		v := s.getenv(st.env)
		if given[st.name] || v == "" {
			continue
		}
		if err := s.fs.Set(st.name, v); err != nil {
			return fmt.Errorf("$%s: invalid value %q: %w", st.env, v, err)
		}
	}
	return nil
}

// Dict returns the effective settings in registration order, keyed by flag name, with secrets masked.
func (s *Set) Dict() *zerolog.Event {
	d := zerolog.Dict()
	for _, st := range s.settings {
		v := s.fs.Lookup(st.name).Value.String()
		if st.secret && v != "" {
			v = Masked
		}
		d.Str(st.name, v)
	}
	return d
}
