package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/joho/godotenv"
)

const (
	defaultRegistryURL = "https://jarvis-demo.ascendingdc.com/gateway"
	logLevelEnvName    = "SKILLS_SYNC_LOG_LEVEL"
	manifestFileName   = "manifest.json"
)

func main() {
	if err := loadEnvironment(); err != nil {
		fmt.Fprintf(os.Stderr, "ERROR: load .env: %v\n", err)
		os.Exit(1)
	}
	configuredLogLevel, err := envLogLevel(logLevelEnvName)
	if err != nil {
		fmt.Fprintf(os.Stderr, "ERROR: %v\n", err)
		os.Exit(1)
	}
	logger := newSyncLogger(configuredLogLevel, os.Stderr)

	root, err := defaultSkillsRoot()
	if err != nil {
		fmt.Fprintf(os.Stderr, "ERROR: %v\n", err)
		os.Exit(1)
	}

	registryURL := flag.String("registry-url", envOrDefault("REGISTRY_URL", defaultRegistryURL), "Registry URL")
	token := flag.String("token", os.Getenv("REGISTRY_TOKEN"), "Bearer token with skills-proxy-ops")
	skillsRoot := flag.String("skills-dir", root, "Local skills directory")
	manifestPath := flag.String("manifest", "", "Manifest path (default: next to skills directory)")
	full := flag.Bool("full", false, "Ignore cursor and reconcile every remote skill")
	flag.Parse()

	if *token == "" {
		fmt.Fprintln(os.Stderr, "ERROR: set REGISTRY_TOKEN or pass -token")
		os.Exit(1)
	}
	if *manifestPath == "" {
		*manifestPath = defaultManifestPath(*skillsRoot)
	}
	infof(
		logger,
		"configuration registry_url=%q skills_dir=%q manifest=%q full=%t",
		strings.TrimRight(*registryURL, "/"),
		*skillsRoot,
		*manifestPath,
		*full,
	)

	syncer := &skillSyncer{
		api: &apiClient{
			baseURL: strings.TrimRight(*registryURL, "/"),
			token:   *token,
			http:    &http.Client{Timeout: 15 * time.Second},
			logger:  logger,
		},
		skillsRoot:   *skillsRoot,
		manifestPath: *manifestPath,
		logger:       logger,
	}
	result, err := syncer.sync(context.Background(), *full)
	if err != nil {
		fmt.Fprintf(os.Stderr, "ERROR: %v\n", err)
		os.Exit(1)
	}
	fmt.Printf(
		"sync complete: added=%d updated=%d deleted=%d unchanged=%d\n",
		result.added,
		result.updated,
		result.deleted,
		result.unchanged,
	)
}

func loadEnvironment() error {
	err := godotenv.Load()
	if err == nil || errors.Is(err, os.ErrNotExist) {
		return nil
	}
	return err
}

func defaultManifestPath(skillsRoot string) string {
	return filepath.Join(filepath.Dir(skillsRoot), manifestFileName)
}

func defaultSkillsRoot() (string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", fmt.Errorf("resolve home directory: %w", err)
	}
	return filepath.Join(home, ".jarvis", "skills"), nil
}

func envOrDefault(name string, fallback string) string {
	if value := os.Getenv(name); value != "" {
		return value
	}
	return fallback
}
