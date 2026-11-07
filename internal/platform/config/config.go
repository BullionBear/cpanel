// Package config binds a command's settings to flags whose defaults come from environment variables, as the mftiko
// commands do: a flag on the command line wins, then the environment variable, then the built-in default. Every
// value goes through its flag's parser, so a bad environment variable fails startup the way a bad flag does.
//
// A secret can also come from a file: when neither the flag nor $NAME is set, $NAME_FILE names a file whose contents,
// without surrounding whitespace, are the value. That is how compose's secrets reach a container (C2-D008): the
// environment carries a path, never the secret. Setting both $NAME and $NAME_FILE is an error.
//
// Dict lists the effective settings for the startup log. A secret that is set is shown as Masked; one that is not
// set is shown as "", so the log still tells the two apart.
package config

import (
	"flag"
	"fmt"
	"os"
	"strings"
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

// Secret registers a string setting that Dict never shows. It has no default. $env_FILE may name a file that holds it.
func (s *Set) Secret(p *string, name, env, usage string) {
	s.fs.StringVar(p, name, "", fmt.Sprintf("%s ($%s or $%s_FILE)", usage, env, env))
	s.add(name, env, true)
}

// Duration registers a setting parsed by time.ParseDuration.
func (s *Set) Duration(p *time.Duration, name, env string, value time.Duration, usage string) {
	s.fs.DurationVar(p, name, value, withEnv(usage, env))
	s.add(name, env, false)
}

// Int registers a setting parsed by strconv.ParseInt with base 0.
func (s *Set) Int(p *int, name, env string, value int, usage string) {
	s.fs.IntVar(p, name, value, withEnv(usage, env))
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
// returns flag.ErrHelp after printing the usage. Positional arguments are rejected; ParseRemaining keeps them.
func (s *Set) Parse(args []string) error {
	if _, err := s.ParseRemaining(args); err != nil {
		return err
	}
	if s.fs.NArg() > 0 {
		return fmt.Errorf("unexpected argument %q", s.fs.Arg(0))
	}
	return nil
}

// ParseRemaining is Parse that returns the positional arguments. cpanel-migrate uses it for up and status.
func (s *Set) ParseRemaining(args []string) ([]string, error) {
	if err := s.fs.Parse(args); err != nil {
		return nil, err
	}
	given := map[string]bool{}
	s.fs.Visit(func(f *flag.Flag) { given[f.Name] = true })
	for _, st := range s.settings {
		v := s.getenv(st.env)
		if st.secret {
			file := s.getenv(st.env + "_FILE")
			if v != "" && file != "" {
				return nil, fmt.Errorf("$%s and $%s_FILE are both set; set one", st.env, st.env)
			}
			if file != "" && !given[st.name] {
				b, err := os.ReadFile(file)
				if err != nil {
					return nil, fmt.Errorf("$%s_FILE: %w", st.env, err)
				}
				if v = strings.TrimSpace(string(b)); v == "" {
					return nil, fmt.Errorf("$%s_FILE: %s is empty", st.env, file)
				}
			}
		}
		if given[st.name] || v == "" {
			continue
		}
		if err := s.fs.Set(st.name, v); err != nil {
			if st.secret {
				return nil, fmt.Errorf("$%s: invalid value: %w", st.env, err)
			}
			return nil, fmt.Errorf("$%s: invalid value %q: %w", st.env, v, err)
		}
	}
	return s.fs.Args(), nil
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
