// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, test } from 'vitest';

import type { SkillSyncJob } from '@/services/skillSyncSource/type';

import GithubLastSyncDetails from './GithubLastSyncDetails';

// Mirrors a real partial_success job from the Registry API (GET /skill-sync-sources/{id}/jobs/{jobId}).
const makeJob = (overrides: Partial<SkillSyncJob> = {}): SkillSyncJob => ({
  id: 'job-1',
  sourceId: 'source-1',
  jobType: 'full_sync',
  triggerType: 'manual',
  status: 'partial_success',
  phase: 'completed',
  requestSnapshot: { owner: 'anthropics', repo: 'skills', ref: 'main', paths: ['skills'], configRevision: 2 },
  discoverySummary: { discoveredSkillCount: 18, discoveredFileCount: 343, skippedPaths: [] },
  applySummary: {
    skillsCreated: 0,
    skillsUpdated: 18,
    skillsDeleted: 0,
    skillsFailed: 0,
    filesCreated: 0,
    filesUpdated: 325,
    filesDeleted: 0,
  },
  skillErrors: [
    {
      skillPath: 'skills/claude-api',
      upstreamId: 'skills/claude-api',
      errorCode: 'skill_parse_failed',
      errorMessage: "SKILL.md frontmatter validation failed: [{'type': 'string_too_long', 'loc': ('description',)}]",
      phase: 'discovery',
    },
  ],
  errorCode: null,
  error: null,
  startedAt: '2026-09-24T13:58:33.652Z',
  finishedAt: '2026-09-24T13:58:35.429Z',
  createdAt: '2026-09-24T13:58:33.083Z',
  updatedAt: '2026-09-24T13:58:35.429Z',
  ...overrides,
});

afterEach(() => {
  cleanup();
});

describe('GithubLastSyncDetails', () => {
  test('names each failed skill by its path and shows its error code and message', () => {
    render(<GithubLastSyncDetails job={makeJob()} />);

    expect(screen.getByText('Failed skills (1)')).toBeTruthy();
    expect(screen.getByText('skills/claude-api')).toBeTruthy();
    expect(screen.getByText('skill_parse_failed')).toBeTruthy();
    expect(screen.getByText(/SKILL\.md frontmatter validation failed/)).toBeTruthy();
    expect(screen.queryByText('Unknown skill')).toBeNull();
  });

  test('lists every failed skill', () => {
    const job = makeJob({
      skillErrors: [
        {
          skillPath: 'skills/a',
          upstreamId: 'skills/a',
          errorCode: 'skill_name_mismatch',
          errorMessage: "Skill name 'b' does not match its folder name 'a'",
          phase: 'discovery',
        },
        {
          skillPath: 'skills/c',
          upstreamId: 'skills/c',
          errorCode: 'write_failed',
          errorMessage: 'write failed',
          phase: 'apply',
        },
      ],
    });

    render(<GithubLastSyncDetails job={job} />);

    expect(screen.getByText('Failed skills (2)')).toBeTruthy();
    expect(screen.getByText('skills/a')).toBeTruthy();
    expect(screen.getByText("Skill name 'b' does not match its folder name 'a'")).toBeTruthy();
    expect(screen.getByText('skills/c')).toBeTruthy();
    expect(screen.getByText('write failed')).toBeTruthy();
  });

  test('omits the failed skills section when no skill errored', () => {
    render(<GithubLastSyncDetails job={makeJob({ status: 'success', skillErrors: [] })} />);

    expect(screen.queryByText(/Failed skills/)).toBeNull();
  });
});
