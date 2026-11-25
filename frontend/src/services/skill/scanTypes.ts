export type SkillScanStatus = 'not_scanned' | 'pending' | 'scanning' | 'completed' | 'failed';
export type SkillScanResultStatus = 'succeeded' | 'partial' | 'failed' | 'skipped';
export type SkillScanSeverity = 'critical' | 'high' | 'medium' | 'low' | 'info';
export type SkillScanSeverityCounts = Partial<Record<SkillScanSeverity, number>>;
export type SkillScanReason =
  | 'llm_unavailable'
  | 'llm_failed'
  | 'llm_skipped_size_limit'
  | 'analysis_incomplete'
  | 'chat_supporting_files_unsupported'
  | 'tool_error'
  | 'timeout'
  | 'parse_error';
export type SkillScanCategory =
  | 'prompt_injection'
  | 'data_exfiltration'
  | 'command_execution'
  | 'credential_access'
  | 'obfuscation'
  | 'supply_chain'
  | 'excessive_permissions'
  | 'harmful_content'
  | 'social_engineering'
  | 'other';

export type SkillScanFindingResponse = {
  tool: string;
  ruleId: string | null;
  severity: SkillScanSeverity;
  category: SkillScanCategory;
  toolCategory: string | null;
  title: string;
  message: string;
  filePath: string | null;
  startLine: number | null;
  endLine: number | null;
  snippet: string | null;
  remediation: string | null;
  confidence: number | null;
};

export type SkillScanToolResultResponse = {
  tool: string;
  toolVersion: string;
  status: SkillScanResultStatus;
  statusReason: SkillScanReason | null;
  outdatedTool: boolean;
  findings: SkillScanFindingResponse[];
  findingsTruncated: boolean;
  llmProviderType: 'aws_bedrock' | 'azure_openai' | null;
  llmModelId: string | null;
  inputTokens: number | null;
  outputTokens: number | null;
  durationMs: number | null;
  errorMessage: string | null;
  toolMetadata: Record<string, unknown>;
  scannedAt: string;
};

export type SkillScanJobResponse = {
  status: 'pending' | 'scanning' | 'done' | 'failed';
  rerequested: boolean;
  triggerType: 'auto' | 'manual';
  requestedAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  attemptCount: number;
  errorCode: string | null;
};

export type SkillScanSummaryResponse = {
  contentDigest: string;
  skillVersion: number;
  status: SkillScanResultStatus;
  maxSeverity: SkillScanSeverity | null;
  severityCounts: SkillScanSeverityCounts;
  scannedAt: string;
};

export type SkillScanResponse = {
  skillId: string;
  skillVersion: number;
  scanStatus: SkillScanStatus;
  stale: boolean;
  outdatedTool: boolean;
  job: SkillScanJobResponse | null;
  summary: SkillScanSummaryResponse | null;
  results: SkillScanToolResultResponse[];
};

export type SkillScanBriefResponse = {
  scanStatus: SkillScanStatus;
  status: SkillScanResultStatus | null;
  maxSeverity: SkillScanSeverity | null;
  severityCounts: SkillScanSeverityCounts;
  stale: boolean;
  outdatedTool: boolean;
  scannedAt: string | null;
};

export type SkillScanRequestResult = { httpStatus: 200 | 202; scan: SkillScanResponse };
