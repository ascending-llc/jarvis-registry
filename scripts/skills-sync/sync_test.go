package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

type fakeRegistry struct {
	metadata     skillMetadata
	content      skillContent
	cursor       string
	contentCalls int
}

func TestSkillSyncAddModifyLocalDriftAndDelete(t *testing.T) {
	guideV1 := "guide v1"
	remote := &fakeRegistry{cursor: "2026-08-05T10:00:00Z"}
	remote.setSkill("skill-1", "demo-skill", skillDefinition{
		Name:          "demo-skill",
		Description:   "demo",
		AlwaysApply:   false,
		UserInvocable: true,
		Category:      "testing",
		Frontmatter:   map[string]any{"model": "test-model"},
		Body:          "# Demo\n",
		Files:         []skillFile{{RelativePath: "references/guide.md", Content: &guideV1}},
	})
	server := httptest.NewServer(http.HandlerFunc(remote.serveHTTP))
	t.Cleanup(server.Close)

	workspace := t.TempDir()
	syncer := &skillSyncer{
		api:          &apiClient{baseURL: server.URL, token: "test-token", http: server.Client()},
		skillsRoot:   filepath.Join(workspace, "skills"),
		manifestPath: filepath.Join(workspace, "manifest.json"),
	}

	t.Run("add remote skill", func(t *testing.T) {
		result, err := syncer.sync(context.Background(), false)
		if err != nil {
			t.Fatal(err)
		}
		if result.added != 1 || remote.contentCalls != 1 {
			t.Fatalf("unexpected result: %+v contentCalls=%d", result, remote.contentCalls)
		}
		assertFileContent(t, filepath.Join(syncer.skillsRoot, "demo-skill", "references", "guide.md"), guideV1)
	})

	t.Run("unchanged skill does not download", func(t *testing.T) {
		result, err := syncer.sync(context.Background(), true)
		if err != nil {
			t.Fatal(err)
		}
		if result.unchanged != 1 || remote.contentCalls != 1 {
			t.Fatalf("unexpected result: %+v contentCalls=%d", result, remote.contentCalls)
		}
	})

	t.Run("remote modification replaces local skill", func(t *testing.T) {
		guideV2 := "guide v2"
		remote.cursor = "2026-08-05T10:01:00Z"
		remote.setSkill("skill-1", "demo-skill", skillDefinition{
			Name:          "demo-skill",
			Description:   "updated demo",
			AlwaysApply:   true,
			UserInvocable: true,
			Category:      "updated",
			Frontmatter:   map[string]any{"model": "updated-model"},
			Body:          "# Updated demo\n",
			Files:         []skillFile{{RelativePath: "references/guide.md", Content: &guideV2}},
		})
		result, err := syncer.sync(context.Background(), false)
		if err != nil {
			t.Fatal(err)
		}
		if result.updated != 1 || remote.contentCalls != 2 {
			t.Fatalf("unexpected result: %+v contentCalls=%d", result, remote.contentCalls)
		}
		assertFileContent(t, filepath.Join(syncer.skillsRoot, "demo-skill", "references", "guide.md"), guideV2)
	})

	t.Run("full sync repairs local drift", func(t *testing.T) {
		filePath := filepath.Join(syncer.skillsRoot, "demo-skill", "references", "guide.md")
		if err := os.WriteFile(filePath, []byte("locally changed"), 0o644); err != nil {
			t.Fatal(err)
		}
		result, err := syncer.sync(context.Background(), true)
		if err != nil {
			t.Fatal(err)
		}
		if result.updated != 1 || remote.contentCalls != 3 {
			t.Fatalf("unexpected result: %+v contentCalls=%d", result, remote.contentCalls)
		}
		assertFileContent(t, filePath, "guide v2")
	})

	t.Run("remote tombstone deletes only managed skill", func(t *testing.T) {
		deletedAt := "2026-08-05T10:02:00Z"
		remote.cursor = deletedAt
		remote.metadata.DeletedAt = &deletedAt
		result, err := syncer.sync(context.Background(), false)
		if err != nil {
			t.Fatal(err)
		}
		if result.deleted != 1 || remote.contentCalls != 3 {
			t.Fatalf("unexpected result: %+v contentCalls=%d", result, remote.contentCalls)
		}
		if _, err := os.Stat(filepath.Join(syncer.skillsRoot, "demo-skill")); !os.IsNotExist(err) {
			t.Fatalf("expected managed skill directory to be deleted, got %v", err)
		}
		manifest, err := loadManifest(syncer.manifestPath)
		if err != nil {
			t.Fatal(err)
		}
		if len(manifest.Skills) != 0 {
			t.Fatalf("expected empty manifest, got %+v", manifest.Skills)
		}
	})
}

func TestFullSyncPrunesPhysicallyDeletedSkills(t *testing.T) {
	// When a skill is physically deleted on the server (no tombstone), a full
	// sync should detect the orphaned manifest entry and remove it locally.
	guideContent := "guide content"
	remote := &fakeRegistry{cursor: "2026-08-05T10:00:00Z"}
	remote.setSkill("skill-1", "demo-skill", skillDefinition{
		Name: "demo-skill", Description: "demo", UserInvocable: true,
		Category: "testing", Frontmatter: map[string]any{},
		Body: "# Demo\n", Files: []skillFile{{RelativePath: "guide.md", Content: &guideContent}},
	})
	server := httptest.NewServer(http.HandlerFunc(remote.serveHTTP))
	t.Cleanup(server.Close)

	workspace := t.TempDir()
	syncer := &skillSyncer{
		api:          &apiClient{baseURL: server.URL, token: "test-token", http: server.Client()},
		skillsRoot:   filepath.Join(workspace, "skills"),
		manifestPath: filepath.Join(workspace, "manifest.json"),
	}

	// Initial sync adds the skill.
	result, err := syncer.sync(context.Background(), false)
	if err != nil {
		t.Fatal(err)
	}
	if result.added != 1 {
		t.Fatalf("expected 1 added, got %+v", result)
	}
	skillDir := filepath.Join(syncer.skillsRoot, "demo-skill")
	if _, err := os.Stat(skillDir); err != nil {
		t.Fatalf("skill directory should exist: %v", err)
	}

	// Simulate physical deletion: server returns an empty skill list.
	emptyServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer test-token" {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		if r.URL.Path == "/proxy/skills" {
			cursor := "2026-08-05T11:00:00Z"
			_ = json.NewEncoder(w).Encode(skillListResponse{Skills: []skillMetadata{}, Cursor: &cursor})
			return
		}
		http.NotFound(w, r)
	}))
	t.Cleanup(emptyServer.Close)
	syncer.api = &apiClient{baseURL: emptyServer.URL, token: "test-token", http: emptyServer.Client()}

	// Delta sync also detects the physical deletion via a full-list probe.
	result, err = syncer.sync(context.Background(), false)
	if err != nil {
		t.Fatal(err)
	}
	if result.deleted != 1 {
		t.Fatalf("delta sync should prune 1 orphan, got %+v", result)
	}
	if _, err := os.Stat(skillDir); !os.IsNotExist(err) {
		t.Fatalf("skill directory should be deleted after full sync, got %v", err)
	}
	manifest, err := loadManifest(syncer.manifestPath)
	if err != nil {
		t.Fatal(err)
	}
	if len(manifest.Skills) != 0 {
		t.Fatalf("expected empty manifest, got %+v", manifest.Skills)
	}
}

func TestHashChangesForEveryLocalDefinitionField(t *testing.T) {
	base := skillDefinition{
		Name: "demo", Description: "description", UserInvocable: true,
		Category: "category", Frontmatter: map[string]any{}, Body: "body",
	}
	baseHash := mustHash(t, base)

	cases := []struct {
		field  string
		mutate func(d *skillDefinition)
	}{
		{"AlwaysApply", func(d *skillDefinition) { d.AlwaysApply = true }},
		{"Category", func(d *skillDefinition) { d.Category = "other" }},
		{"DisableModelInvocation", func(d *skillDefinition) { d.DisableModelInvocation = true }},
		{"UserInvocable", func(d *skillDefinition) { d.UserInvocable = false }},
		{"AllowedTools", func(d *skillDefinition) { d.AllowedTools = []string{"bash"} }},
	}
	for _, tc := range cases {
		t.Run(tc.field, func(t *testing.T) {
			changed := base
			tc.mutate(&changed)
			if mustHash(t, changed) == baseHash {
				t.Fatalf("%s must affect the hash", tc.field)
			}
		})
	}
}

func TestHashMatchesPythonVector(t *testing.T) {
	guide := "guide v1"
	definition := skillDefinition{
		Name: "demo", Description: "description", UserInvocable: true, Category: "testing",
		Frontmatter: map[string]any{"model": "test-model"}, Body: "# Demo\n",
		Files: []skillFile{{RelativePath: "references/guide.md", Content: &guide}},
	}
	const pythonHash = "sha256:39c205b84a2f3f047bcaaf5c0dcd1f08050b85bc3c837ac3c36c647291e312cf"
	if actual := mustHash(t, definition); actual != pythonHash {
		t.Fatalf("Python/Go hash mismatch: expected %s, got %s", pythonHash, actual)
	}
}

func TestEmptyFrontmatterRoundTripPreservesHash(t *testing.T) {
	definition := skillDefinition{
		Name: "demo", Description: "description", UserInvocable: true, Category: "testing",
		Frontmatter: map[string]any{}, Body: "# Demo\n",
	}
	skillDirectory := t.TempDir()
	if err := writeSkill(skillDirectory, definition); err != nil {
		t.Fatal(err)
	}
	expected := mustHash(t, definition)
	actual, err := hashLocalSkill(skillDirectory)
	if err != nil {
		t.Fatal(err)
	}
	if actual != expected {
		t.Fatalf("empty frontmatter round-trip changed hash: expected %s, got %s", expected, actual)
	}
}

func TestJSONNumberFrontmatterRoundTripPreservesHash(t *testing.T) {
	definition := skillDefinition{
		Name: "demo", Description: "description", UserInvocable: true, Category: "testing",
		Frontmatter: map[string]any{
			"priority": json.Number("10"),
			"config": map[string]any{
				"temperature": json.Number("0.7"),
				"nested": []any{
					json.Number("42"),
					map[string]any{"maxTokens": json.Number("4096")},
				},
			},
		},
		Body: "# Demo\n",
	}
	skillDirectory := t.TempDir()
	if err := writeSkill(skillDirectory, definition); err != nil {
		t.Fatal(err)
	}
	expected := mustHash(t, definition)
	actual, err := hashLocalSkill(skillDirectory)
	if err != nil {
		t.Fatal(err)
	}
	if actual != expected {
		t.Fatalf("JSON number frontmatter round-trip changed hash: expected %s, got %s", expected, actual)
	}
}

func TestDefaultManifestPathIsNextToSkillsDirectory(t *testing.T) {
	skillsRoot := filepath.Join("home", ".jarvis", "skills")
	expected := filepath.Join("home", ".jarvis", "manifest.json")
	if actual := defaultManifestPath(skillsRoot); actual != expected {
		t.Fatalf("expected manifest path %q, got %q", expected, actual)
	}
}

func TestEnvLogLevelDefaultsToInfo(t *testing.T) {
	t.Setenv(logLevelEnvName, "")
	level, err := envLogLevel(logLevelEnvName)
	if err != nil {
		t.Fatal(err)
	}
	if level != logLevelInfo {
		t.Fatalf("expected default info level, got %d", level)
	}
}

func TestParseLogLevelAcceptsSupportedValuesAndRejectsInvalidValues(t *testing.T) {
	testCases := map[string]logLevel{
		"debug": logLevelDebug,
		"INFO":  logLevelInfo,
		"warn":  logLevelWarn,
		"error": logLevelError,
	}
	for value, expected := range testCases {
		actual, err := parseLogLevel(value)
		if err != nil {
			t.Fatalf("parse %q: %v", value, err)
		}
		if actual != expected {
			t.Fatalf("parse %q: expected %d, got %d", value, expected, actual)
		}
	}
	if _, err := parseLogLevel("verbose"); err == nil {
		t.Fatal("expected invalid log level to fail")
	}
}

func TestSyncLoggerFiltersBelowMinimumLevel(t *testing.T) {
	var output bytes.Buffer
	logger := newSyncLogger(logLevelInfo, &output)
	debugf(logger, "hidden detail")
	infof(logger, "visible summary")

	logs := output.String()
	if strings.Contains(logs, "hidden detail") {
		t.Fatal("debug log must be filtered at info level")
	}
	if !strings.Contains(logs, "INFO visible summary") {
		t.Fatalf("expected info log, got %q", logs)
	}
}

func (fake *fakeRegistry) setSkill(id string, skillPath string, definition skillDefinition) {
	hash := mustHashValue(definition)
	fake.metadata = skillMetadata{
		ID: id, Path: skillPath, Name: definition.Name, ContentHash: hash, UpdatedAt: fake.cursor,
	}
	fake.content = skillContent{
		ID: id, Name: definition.Name, Description: definition.Description, AlwaysApply: definition.AlwaysApply,
		DisableModelInvocation: definition.DisableModelInvocation, UserInvocable: definition.UserInvocable,
		AllowedTools: definition.AllowedTools, Category: definition.Category,
		Frontmatter: definition.Frontmatter, Body: definition.Body,
		ContentHash: hash, Files: definition.Files,
	}
}

func (fake *fakeRegistry) serveHTTP(response http.ResponseWriter, request *http.Request) {
	if request.Header.Get("Authorization") != "Bearer test-token" {
		http.Error(response, "unauthorized", http.StatusUnauthorized)
		return
	}
	response.Header().Set("Content-Type", "application/json")
	if request.URL.Path == "/proxy/skills" {
		_ = json.NewEncoder(response).Encode(skillListResponse{Skills: []skillMetadata{fake.metadata}, Cursor: &fake.cursor})
		return
	}
	if request.URL.Path == "/proxy/skills/skill-1/content" {
		fake.contentCalls++
		_ = json.NewEncoder(response).Encode(fake.content)
		return
	}
	http.NotFound(response, request)
}

func assertFileContent(t *testing.T, filePath string, expected string) {
	t.Helper()
	content, err := os.ReadFile(filePath)
	if err != nil {
		t.Fatal(err)
	}
	if string(content) != expected {
		t.Fatalf("expected %q, got %q", expected, string(content))
	}
}

func mustHash(t *testing.T, definition skillDefinition) string {
	t.Helper()
	hash, err := computeSkillHash(definition)
	if err != nil {
		t.Fatal(err)
	}
	return hash
}

func mustHashValue(definition skillDefinition) string {
	hash, err := computeSkillHash(definition)
	if err != nil {
		panic(err)
	}
	return hash
}
