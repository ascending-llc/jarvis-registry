package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"

	"gopkg.in/yaml.v3"
)

type skillMetadata struct {
	ID          string  `json:"id"`
	Path        string  `json:"path"`
	Name        string  `json:"name"`
	ContentHash string  `json:"contentHash"`
	UpdatedAt   string  `json:"updatedAt"`
	DeletedAt   *string `json:"deletedAt"`
}

type skillListResponse struct {
	Skills []skillMetadata `json:"skills"`
}

type skillFile struct {
	RelativePath string  `json:"relativePath"`
	Content      *string `json:"content"`
	IsBinary     *bool   `json:"isBinary"`
	IsExecutable bool    `json:"isExecutable"`
}

type skillContent struct {
	ID                     string         `json:"id"`
	Name                   string         `json:"name"`
	Description            string         `json:"description"`
	AlwaysApply            bool           `json:"alwaysApply"`
	DisableModelInvocation bool           `json:"disableModelInvocation"`
	UserInvocable          bool           `json:"userInvocable"`
	AllowedTools           []string       `json:"allowedTools"`
	Category               string         `json:"category"`
	Frontmatter            map[string]any `json:"frontmatter"`
	Body                   string         `json:"body"`
	ContentHash            string         `json:"contentHash"`
	Files                  []skillFile    `json:"files"`
}

type manifestEntry struct {
	Path        string `json:"path"`
	Name        string `json:"name"`
	ContentHash string `json:"contentHash"`
	UpdatedAt   string `json:"updatedAt"`
}

type localManifest struct {
	Skills map[string]manifestEntry `json:"skills"`
}

type syncResult struct {
	added     int
	updated   int
	deleted   int
	unchanged int
}

type apiClient struct {
	baseURL string
	token   string
	http    *http.Client
	logger  *syncLogger
}

type skillSyncer struct {
	api          *apiClient
	skillsRoot   string
	manifestPath string
	logger       *syncLogger
}

func (syncer *skillSyncer) sync(ctx context.Context, full bool) (syncResult, error) {
	manifest, err := loadManifest(syncer.manifestPath)
	if err != nil {
		return syncResult{}, err
	}
	debugf(
		syncer.logger,
		"manifest loaded path=%q managed_skills=%d",
		syncer.manifestPath,
		len(manifest.Skills),
	)
	remote, err := syncer.api.listSkills(ctx)
	if err != nil {
		return syncResult{}, err
	}
	infof(syncer.logger, "metadata received skills=%d", len(remote.Skills))
	if err := os.MkdirAll(syncer.skillsRoot, 0o755); err != nil {
		return syncResult{}, err
	}

	result := syncResult{}
	for _, metadata := range remote.Skills {
		if metadata.DeletedAt != nil {
			debugf(syncer.logger, "tombstone received id=%s path=%q deleted_at=%q", metadata.ID, metadata.Path, *metadata.DeletedAt)
			deleted, err := syncer.deleteSkill(manifest, metadata.ID)
			if err != nil {
				return syncResult{}, err
			}
			if deleted {
				result.deleted++
				infof(syncer.logger, "managed skill deleted id=%s", metadata.ID)
			} else {
				result.unchanged++
				warnf(syncer.logger, "unknown tombstone ignored id=%s", metadata.ID)
			}
			continue
		}
		changed, added, err := syncer.syncSkill(ctx, manifest, metadata)
		if err != nil {
			return syncResult{}, fmt.Errorf("sync skill %s: %w", metadata.ID, err)
		}
		if added {
			result.added++
		} else if changed {
			result.updated++
		} else {
			result.unchanged++
		}
	}
	// Prune managed skills that no longer exist on the server (physical deletes).
	// The list endpoint always returns all skills, so orphans are detected directly.
	if len(manifest.Skills) > 0 {
		remoteIDs := make(map[string]struct{}, len(remote.Skills))
		for _, metadata := range remote.Skills {
			if metadata.DeletedAt == nil {
				remoteIDs[metadata.ID] = struct{}{}
			}
		}
		for id := range manifest.Skills {
			if _, exists := remoteIDs[id]; !exists {
				deleted, err := syncer.deleteSkill(manifest, id)
				if err != nil {
					return syncResult{}, err
				}
				if deleted {
					result.deleted++
					infof(syncer.logger, "orphaned skill pruned id=%s", id)
				}
			}
		}
	}

	if err := saveManifest(syncer.manifestPath, manifest); err != nil {
		return syncResult{}, err
	}
	debugf(
		syncer.logger,
		"manifest saved path=%q managed_skills=%d",
		syncer.manifestPath,
		len(manifest.Skills),
	)
	return result, nil
}

func (syncer *skillSyncer) syncSkill(
	ctx context.Context,
	manifest *localManifest,
	metadata skillMetadata,
) (changed bool, added bool, err error) {
	// A remote ID may only manage the directory recorded for it in manifest.json.
	// Refusing unmanaged collisions keeps Registry sync from overwriting user data.
	if err := validateRelativePath(metadata.Path); err != nil || strings.Contains(metadata.Path, "/") {
		return false, false, fmt.Errorf("unsafe skill directory %q", metadata.Path)
	}
	entry, managed := manifest.Skills[metadata.ID]
	destination := filepath.Join(syncer.skillsRoot, metadata.Path)
	debugf(
		syncer.logger,
		"skill evaluating id=%s path=%q managed=%t remote_hash=%s",
		metadata.ID,
		metadata.Path,
		managed,
		metadata.ContentHash,
	)
	if !managed {
		if _, statErr := os.Stat(destination); statErr == nil {
			return false, false, fmt.Errorf("refusing to overwrite unmanaged directory %s", destination)
		} else if !errors.Is(statErr, os.ErrNotExist) {
			return false, false, statErr
		}
	}

	localHash := ""
	if managed {
		localPath := filepath.Join(syncer.skillsRoot, entry.Path)
		localHashValue, hashErr := hashLocalSkill(localPath)
		if hashErr != nil {
			// A missing or malformed managed directory is treated as local drift and repaired from Registry.
			debugf(syncer.logger, "local hash unavailable id=%s path=%q error=%q", metadata.ID, localPath, hashErr)
		} else {
			localHash = localHashValue
			debugf(syncer.logger, "local hash computed id=%s hash=%s", metadata.ID, localHash)
		}
	}
	if managed && localHash == metadata.ContentHash {
		// Equality against the hash reconstructed from disk is sufficient to skip
		// the content endpoint; a path-only rename can be handled independently.
		if entry.Path != metadata.Path {
			if err := syncer.moveSkill(entry.Path, metadata.Path); err != nil {
				return false, false, err
			}
			infof(syncer.logger, "managed skill moved id=%s old_path=%q new_path=%q", metadata.ID, entry.Path, metadata.Path)
		}
		manifest.Skills[metadata.ID] = manifestEntry{
			Path: metadata.Path, Name: metadata.Name, ContentHash: metadata.ContentHash, UpdatedAt: metadata.UpdatedAt,
		}
		debugf(syncer.logger, "skill unchanged id=%s path=%q", metadata.ID, metadata.Path)
		return entry.Path != metadata.Path, false, nil
	}

	debugf(syncer.logger, "skill content required id=%s local_hash=%q remote_hash=%s", metadata.ID, localHash, metadata.ContentHash)
	content, err := syncer.api.getSkillContent(ctx, metadata.ID)
	if err != nil {
		return false, false, err
	}
	definition := definitionFromContent(content)
	downloadHash, err := computeSkillHash(definition)
	if err != nil {
		return false, false, err
	}
	if downloadHash != metadata.ContentHash || downloadHash != content.ContentHash {
		// Require list metadata, detail metadata, and downloaded bytes to describe
		// the same logical Skill before any local file is replaced.
		return false, false, fmt.Errorf(
			"download hash mismatch: list=%s content=%s computed=%s",
			metadata.ContentHash,
			content.ContentHash,
			downloadHash,
		)
	}
	if err := syncer.replaceSkill(destination, definition); err != nil {
		return false, false, err
	}
	action := "added"
	if managed {
		action = "updated"
	}
	infof(syncer.logger, "skill %s id=%s path=%q hash=%s", action, metadata.ID, metadata.Path, metadata.ContentHash)
	if managed && entry.Path != metadata.Path {
		if err := syncer.removeManagedPath(entry.Path); err != nil {
			return false, false, err
		}
	}
	manifest.Skills[metadata.ID] = manifestEntry{
		Path: metadata.Path, Name: metadata.Name, ContentHash: metadata.ContentHash, UpdatedAt: metadata.UpdatedAt,
	}
	return managed, !managed, nil
}

func definitionFromContent(content skillContent) skillDefinition {
	return skillDefinition{
		Name:                   content.Name,
		Description:            content.Description,
		AlwaysApply:            content.AlwaysApply,
		DisableModelInvocation: content.DisableModelInvocation,
		UserInvocable:          content.UserInvocable,
		AllowedTools:           content.AllowedTools,
		Category:               content.Category,
		Frontmatter:            content.Frontmatter,
		Body:                   content.Body,
		Files:                  content.Files,
	}
}

func (syncer *skillSyncer) replaceSkill(destination string, skill skillDefinition) error {
	// Build and verify the complete skill beside its destination before swapping directories.
	temporary, err := os.MkdirTemp(syncer.skillsRoot, ".skill-sync-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(temporary)
	if err := writeSkill(temporary, skill); err != nil {
		return err
	}
	expectedHash, err := computeSkillHash(skill)
	if err != nil {
		return err
	}
	writtenHash, err := hashLocalSkill(temporary)
	if err != nil {
		return err
	}
	if writtenHash != expectedHash {
		// JSON -> YAML -> filesystem conversion can change types or content. The
		// round-trip check catches that before the verified directory is installed.
		return fmt.Errorf("written skill hash mismatch: expected=%s actual=%s", expectedHash, writtenHash)
	}
	backupRoot, err := os.MkdirTemp(syncer.skillsRoot, ".skill-backup-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(backupRoot)
	backup := filepath.Join(backupRoot, "skill")
	// Keep the previous directory recoverable until the verified replacement rename succeeds.
	if _, err := os.Stat(destination); err == nil {
		if err := os.Rename(destination, backup); err != nil {
			return err
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	if err := os.Rename(temporary, destination); err != nil {
		_ = os.Rename(backup, destination)
		return err
	}
	return os.RemoveAll(backup)
}

func writeSkill(directory string, skill skillDefinition) error {
	// Promoted Skill fields are serialized explicitly; the remaining frontmatter
	// stays inline so readLocalSkill can reconstruct the original hash manifest.
	frontmatter, err := normalizeYAMLFrontmatter(skill.Frontmatter)
	if err != nil {
		return err
	}
	header := skillHeader{
		Name:        skill.Name,
		Description: skill.Description,
		AlwaysApply: skill.AlwaysApply,
		Category:    skill.Category,
		Frontmatter: frontmatter,
	}
	if skill.DisableModelInvocation {
		header.DisableModelInvocation = true
	}
	if !skill.UserInvocable {
		userInvocable := false
		header.UserInvocable = &userInvocable
	}
	if len(skill.AllowedTools) > 0 {
		header.AllowedTools = skill.AllowedTools
	}
	yamlBytes, err := yaml.Marshal(header)
	if err != nil {
		return err
	}
	skillMD := append([]byte("---\n"), yamlBytes...)
	skillMD = append(skillMD, []byte("---\n\n"+skill.Body)...)
	if err := os.WriteFile(filepath.Join(directory, "SKILL.md"), skillMD, 0o644); err != nil {
		return err
	}
	for _, file := range skill.Files {
		if err := validateRelativePath(file.RelativePath); err != nil {
			return err
		}
		if file.Content == nil {
			return fmt.Errorf("file %q has no content", file.RelativePath)
		}
		filePath := filepath.Join(directory, filepath.FromSlash(file.RelativePath))
		if err := os.MkdirAll(filepath.Dir(filePath), 0o755); err != nil {
			return err
		}
		perm := os.FileMode(0o644)
		if file.IsExecutable {
			perm = 0o755
		}
		if err := os.WriteFile(filePath, []byte(*file.Content), perm); err != nil {
			return err
		}
	}
	return nil
}

func normalizeYAMLFrontmatter(frontmatter map[string]any) (map[string]any, error) {
	// encoding/json preserves numbers as json.Number for hash fidelity, while yaml.v3 would quote that type as text.
	normalized, err := normalizeYAMLValue(frontmatter)
	if err != nil {
		return nil, err
	}
	return normalized.(map[string]any), nil
}

func normalizeYAMLValue(value any) (any, error) {
	// Decode responses with json.Number for hash fidelity, then recursively turn
	// numbers into YAML-native values so yaml.v3 does not quote them as strings.
	switch typed := value.(type) {
	case json.Number:
		if integer, err := typed.Int64(); err == nil {
			return integer, nil
		}
		floatingPoint, err := typed.Float64()
		if err != nil {
			return nil, fmt.Errorf("invalid JSON number %q in frontmatter: %w", typed, err)
		}
		return floatingPoint, nil
	case map[string]any:
		normalized := make(map[string]any, len(typed))
		for key, item := range typed {
			normalizedItem, err := normalizeYAMLValue(item)
			if err != nil {
				return nil, err
			}
			normalized[key] = normalizedItem
		}
		return normalized, nil
	case []any:
		normalized := make([]any, len(typed))
		for index, item := range typed {
			normalizedItem, err := normalizeYAMLValue(item)
			if err != nil {
				return nil, err
			}
			normalized[index] = normalizedItem
		}
		return normalized, nil
	default:
		return value, nil
	}
}

func (syncer *skillSyncer) deleteSkill(manifest *localManifest, skillID string) (bool, error) {
	entry, managed := manifest.Skills[skillID]
	if !managed {
		return false, nil
	}
	// Resolve deletion from the trusted manifest entry, never from the untrusted tombstone path.
	if err := syncer.removeManagedPath(entry.Path); err != nil {
		return false, err
	}
	delete(manifest.Skills, skillID)
	return true, nil
}

func (syncer *skillSyncer) removeManagedPath(relativePath string) error {
	// Managed Skill directories must be direct children of skillsRoot. This keeps
	// recursive deletion bounded even if manifest.json has been corrupted.
	if err := validateRelativePath(relativePath); err != nil || strings.Contains(relativePath, "/") {
		return fmt.Errorf("unsafe managed skill path %q", relativePath)
	}
	return os.RemoveAll(filepath.Join(syncer.skillsRoot, relativePath))
}

func (syncer *skillSyncer) moveSkill(oldPath string, newPath string) error {
	if err := validateRelativePath(oldPath); err != nil || strings.Contains(oldPath, "/") {
		return fmt.Errorf("unsafe managed skill path %q", oldPath)
	}
	oldDestination := filepath.Join(syncer.skillsRoot, oldPath)
	newDestination := filepath.Join(syncer.skillsRoot, newPath)
	if _, err := os.Stat(newDestination); err == nil {
		return fmt.Errorf("refusing to overwrite directory %s", newDestination)
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	return os.Rename(oldDestination, newDestination)
}

func loadManifest(manifestPath string) (*localManifest, error) {
	content, err := os.ReadFile(manifestPath)
	if errors.Is(err, os.ErrNotExist) {
		return &localManifest{Skills: map[string]manifestEntry{}}, nil
	}
	if err != nil {
		return nil, err
	}
	manifest := &localManifest{}
	if err := json.Unmarshal(content, manifest); err != nil {
		return nil, fmt.Errorf("parse manifest: %w", err)
	}
	if manifest.Skills == nil {
		manifest.Skills = map[string]manifestEntry{}
	}
	return manifest, nil
}

func saveManifest(manifestPath string, manifest *localManifest) error {
	// Write beside the destination and rename only after the complete JSON document
	// is flushed and closed, preventing readers from observing a partial manifest.
	content, err := json.MarshalIndent(manifest, "", "  ")
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(manifestPath), 0o755); err != nil {
		return err
	}
	temporary, err := os.CreateTemp(filepath.Dir(manifestPath), ".skills-manifest-")
	if err != nil {
		return err
	}
	temporaryPath := temporary.Name()
	defer os.Remove(temporaryPath)
	if _, err := temporary.Write(append(content, '\n')); err != nil {
		temporary.Close()
		return err
	}
	if err := temporary.Chmod(0o600); err != nil {
		temporary.Close()
		return err
	}
	if err := temporary.Close(); err != nil {
		return err
	}
	return os.Rename(temporaryPath, manifestPath)
}

func (client *apiClient) listSkills(ctx context.Context) (skillListResponse, error) {
	endpoint := client.baseURL + "/proxy/skills"
	response := skillListResponse{}
	err := client.getJSON(ctx, endpoint, &response)
	return response, err
}

func (client *apiClient) getSkillContent(ctx context.Context, skillID string) (skillContent, error) {
	response := skillContent{}
	endpoint := client.baseURL + "/proxy/skills/" + url.PathEscape(skillID) + "/content"
	err := client.getJSON(ctx, endpoint, &response)
	return response, err
}

func (client *apiClient) getJSON(ctx context.Context, endpoint string, target any) error {
	// Bound response memory for this smoke client and retain JSON number spelling;
	// both are important before the payload enters hash and YAML conversion paths.
	startedAt := time.Now()
	debugf(client.logger, "http request method=GET url=%q", endpoint)
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return err
	}
	request.Header.Set("Authorization", "Bearer "+client.token)
	request.Header.Set("Accept", "application/json")
	response, err := client.http.Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	body, err := io.ReadAll(io.LimitReader(response.Body, 16<<20))
	if err != nil {
		return err
	}
	debugf(
		client.logger,
		"http response method=GET url=%q status=%d bytes=%d elapsed=%s",
		endpoint,
		response.StatusCode,
		len(body),
		time.Since(startedAt).Round(time.Millisecond),
	)
	if response.StatusCode != http.StatusOK {
		return fmt.Errorf("GET %s returned %s: %s", endpoint, response.Status, strings.TrimSpace(string(body)))
	}
	decoder := json.NewDecoder(bytes.NewReader(body))
	// Preserve JSON number spelling until YAML serialization so cross-language hash validation remains exact.
	decoder.UseNumber()
	if err := decoder.Decode(target); err != nil {
		return fmt.Errorf("decode %s: %w", endpoint, err)
	}
	return nil
}
