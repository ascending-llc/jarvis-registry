package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"unicode/utf8"

	"gopkg.in/yaml.v3"
)

const (
	hashFormat = "jarvis-skill-content-v1"
	hashPrefix = "sha256:"
)

type skillDefinition struct {
	Name                   string
	Description            string
	AlwaysApply            bool
	DisableModelInvocation bool
	UserInvocable          bool
	AllowedTools           []string
	Category               string
	Frontmatter            map[string]any
	Body                   string
	Files                  []skillFile
}

type skillHeader struct {
	Name                   string         `yaml:"name"`
	Description            string         `yaml:"description"`
	AlwaysApply            bool           `yaml:"always-apply"`
	DisableModelInvocation bool           `yaml:"disable-model-invocation,omitempty"`
	UserInvocable          *bool          `yaml:"user-invocable,omitempty"`
	AllowedTools           []string       `yaml:"allowed-tools,omitempty"`
	Category               string         `yaml:"category"`
	Frontmatter            map[string]any `yaml:",inline"`
}

func computeSkillHash(skill skillDefinition) (string, error) {
	// The Registry's Python service hashes the same versioned logical manifest.
	// Keep field selection, validation, UTF-8 path ordering, and JSON encoding in
	// sync with that implementation; changing any of them requires a new format.
	files := append([]skillFile(nil), skill.Files...)
	sort.Slice(files, func(i int, j int) bool {
		return bytes.Compare([]byte(files[i].RelativePath), []byte(files[j].RelativePath)) < 0
	})

	manifestFiles := make([]any, 0, len(files))
	seen := make(map[string]struct{}, len(files))
	for _, file := range files {
		if err := validateRelativePath(file.RelativePath); err != nil {
			return "", err
		}
		if _, exists := seen[file.RelativePath]; exists {
			return "", fmt.Errorf("duplicate relative path %q", file.RelativePath)
		}
		seen[file.RelativePath] = struct{}{}
		if file.IsBinary != nil && *file.IsBinary {
			return "", fmt.Errorf("binary file %q cannot be synchronized", file.RelativePath)
		}
		if file.Content == nil {
			return "", fmt.Errorf("file %q has no content", file.RelativePath)
		}
		manifestFiles = append(manifestFiles, map[string]any{
			"content":      *file.Content,
			"isExecutable": file.IsExecutable,
			"relativePath": file.RelativePath,
		})
	}

	var allowedTools any
	if skill.AllowedTools != nil {
		allowedTools = skill.AllowedTools
	}

	manifest := map[string]any{
		"format": hashFormat,
		"skill": map[string]any{
			"allowedTools":           allowedTools,
			"alwaysApply":            skill.AlwaysApply,
			"body":                   skill.Body,
			"category":               skill.Category,
			"description":            skill.Description,
			"disableModelInvocation": skill.DisableModelInvocation,
			"frontmatter":            skill.Frontmatter,
			"name":                   skill.Name,
			"userInvocable":          skill.UserInvocable,
		},
		"files": manifestFiles,
	}
	canonical, err := canonicalJSON(manifest)
	if err != nil {
		return "", err
	}
	digest := sha256.Sum256(canonical)
	return hashPrefix + hex.EncodeToString(digest[:]), nil
}

func canonicalJSON(value any) ([]byte, error) {
	// json.Encoder is almost compatible with Python's canonical json.dumps call.
	// Remove Encoder.Encode's trailing newline and undo the two Unicode escapes
	// that Go emits unconditionally so both languages hash identical UTF-8 bytes.
	var output bytes.Buffer
	encoder := json.NewEncoder(&output)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(value); err != nil {
		return nil, fmt.Errorf("encode canonical manifest: %w", err)
	}
	encoded := bytes.TrimSuffix(output.Bytes(), []byte("\n"))
	encoded = bytes.ReplaceAll(encoded, []byte("\\u2028"), []byte(" "))
	encoded = bytes.ReplaceAll(encoded, []byte("\\u2029"), []byte(" "))
	return encoded, nil
}

func hashLocalSkill(skillDir string) (string, error) {
	// Rebuild the logical definition from disk instead of trusting manifest.json.
	// This makes missing files and user edits visible during reconciliation.
	skill, err := readLocalSkill(skillDir)
	if err != nil {
		return "", err
	}
	return computeSkillHash(skill)
}

func readLocalSkill(skillDir string) (skillDefinition, error) {
	// SKILL.md contains the primary fields and body; every other regular UTF-8
	// file belongs to the supporting-file set included in the content hash.
	skillMD, err := os.ReadFile(filepath.Join(skillDir, "SKILL.md"))
	if err != nil {
		return skillDefinition{}, err
	}
	header, body, err := parseSkillMD(skillMD)
	if err != nil {
		return skillDefinition{}, err
	}
	files, err := readSupportingFiles(skillDir)
	if err != nil {
		return skillDefinition{}, err
	}
	frontmatter := header.Frontmatter
	if frontmatter == nil {
		frontmatter = map[string]any{}
	}
	userInvocable := true
	if header.UserInvocable != nil {
		userInvocable = *header.UserInvocable
	}
	return skillDefinition{
		Name:                   header.Name,
		Description:            header.Description,
		AlwaysApply:            header.AlwaysApply,
		DisableModelInvocation: header.DisableModelInvocation,
		UserInvocable:          userInvocable,
		AllowedTools:           header.AllowedTools,
		Category:               header.Category,
		Frontmatter:            frontmatter,
		Body:                   body,
		Files:                  files,
	}, nil
}

func parseSkillMD(content []byte) (skillHeader, string, error) {
	// Parse only the file shape produced by writeSkill. Strict delimiters avoid
	// silently hashing a malformed document as a different logical Skill.
	if !bytes.HasPrefix(content, []byte("---\n")) {
		return skillHeader{}, "", fmt.Errorf("SKILL.md must start with YAML frontmatter")
	}
	closing := bytes.Index(content[4:], []byte("\n---\n"))
	if closing < 0 {
		return skillHeader{}, "", fmt.Errorf("SKILL.md has no closing frontmatter delimiter")
	}
	closing += 4
	yamlBytes := content[4:closing]
	body := content[closing+5:]
	if len(body) > 0 && body[0] == '\n' {
		body = body[1:]
	}
	header := skillHeader{}
	if err := yaml.Unmarshal(yamlBytes, &header); err != nil {
		return skillHeader{}, "", fmt.Errorf("parse SKILL.md frontmatter: %w", err)
	}
	if header.Name == "" {
		return skillHeader{}, "", fmt.Errorf("SKILL.md frontmatter is missing name")
	}
	return header, string(body), nil
}

func readSupportingFiles(skillDir string) ([]skillFile, error) {
	// Symlinks are rejected so hashing cannot escape the managed Skill directory
	// or vary according to an external target that Registry does not control.
	files := []skillFile{}
	err := filepath.WalkDir(skillDir, func(filePath string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.Type()&os.ModeSymlink != 0 {
			return fmt.Errorf("symlinks are not supported: %s", filePath)
		}
		if entry.IsDir() || filePath == filepath.Join(skillDir, "SKILL.md") {
			return nil
		}
		content, err := os.ReadFile(filePath)
		if err != nil {
			return err
		}
		if !utf8.Valid(content) {
			return fmt.Errorf("local binary file is not supported: %s", filePath)
		}
		info, err := entry.Info()
		if err != nil {
			return err
		}
		isExecutable := info.Mode()&0o111 != 0
		relativePath, err := filepath.Rel(skillDir, filePath)
		if err != nil {
			return err
		}
		text := string(content)
		files = append(files, skillFile{
			RelativePath: filepath.ToSlash(relativePath),
			Content:      &text,
			IsExecutable: isExecutable,
		})
		return nil
	})
	return files, err
}

func validateRelativePath(relativePath string) error {
	// Hash inputs and write targets use canonical POSIX paths. Requiring the input
	// to already be clean prevents traversal and avoids multiple spellings for one
	// logical path from producing ambiguous manifests.
	cleaned := filepath.ToSlash(filepath.Clean(relativePath))
	if relativePath == "" || relativePath == "." || strings.Contains(relativePath, "\\") ||
		strings.HasPrefix(relativePath, "/") || cleaned != relativePath {
		return fmt.Errorf("unsafe relative path %q", relativePath)
	}
	for _, part := range strings.Split(relativePath, "/") {
		if part == ".." {
			return fmt.Errorf("unsafe relative path %q", relativePath)
		}
	}
	return nil
}
