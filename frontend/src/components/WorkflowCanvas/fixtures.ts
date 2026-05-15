import type { Edge } from '@xyflow/react';
import { getLayoutedElements } from './layout';
import type { SchemaField, WorkflowNode } from './types';

/** Mock backend /agents/{id}/schema response. */
export const AGENT_SCHEMAS: Record<string, { output: SchemaField[] }> = {
  CloudWatch: {
    output: [
      {
        name: 'message.severity',
        type: 'string',
        desc: 'Alert severity level',
        enum: ['critical', 'high', 'medium', 'low'],
      },
      { name: 'message.score', type: 'number', desc: 'Anomaly confidence score (0-1)' },
      { name: 'message.service', type: 'string', desc: 'AWS service identifier' },
      { name: 'message.tags', type: 'list(string)', desc: 'Resource tags' },
      { name: 'message.region', type: 'string', desc: 'AWS region' },
      { name: 'message.timestamp', type: 'timestamp', desc: 'Event time (RFC 3339)' },
    ],
  },
  Slack: {
    output: [
      { name: 'message.channel', type: 'string', desc: 'Slack channel name' },
      { name: 'message.user', type: 'string', desc: 'Sender user ID' },
      { name: 'message.text', type: 'string', desc: 'Message body' },
      { name: 'message.ts', type: 'string', desc: 'Message timestamp' },
    ],
  },
  PagerDuty: {
    output: [
      { name: 'message.incident_key', type: 'string', desc: 'Unique incident key' },
      { name: 'message.severity', type: 'string', desc: 'Incident severity' },
      { name: 'message.service', type: 'string', desc: 'Affected service name' },
      { name: 'message.status', type: 'string', desc: 'Incident status' },
    ],
  },
  'Diagnosis Agent': {
    output: [
      { name: 'session_state.findings', type: 'list(string)', desc: 'Identified root causes' },
      { name: 'session_state.confidence', type: 'number', desc: 'Diagnosis confidence score (0-1)' },
      { name: 'session_state.severity', type: 'string', desc: 'Assessed severity level' },
      { name: 'session_state.recommended', type: 'string', desc: 'Recommended next action' },
      { name: 'session_state.retry', type: 'bool', desc: 'Should retry diagnosis' },
    ],
  },
  'Remediation Agent': {
    output: [
      { name: 'session_state.status', type: 'string', desc: 'Remediation status' },
      { name: 'session_state.actions_taken', type: 'list(string)', desc: 'Steps executed' },
      { name: 'session_state.done', type: 'bool', desc: 'Remediation complete flag' },
      { name: 'session_state.retry', type: 'bool', desc: 'Should retry remediation' },
    ],
  },
  'Classifier Agent': {
    output: [
      { name: 'session_state.category', type: 'string', desc: 'Classified category' },
      { name: 'session_state.score', type: 'number', desc: 'Classification confidence' },
      { name: 'session_state.labels', type: 'list(string)', desc: 'All predicted labels' },
    ],
  },
  'Scorer Agent': {
    output: [
      { name: 'session_state.score', type: 'number', desc: 'Final numeric score (0-1)' },
      { name: 'session_state.tier', type: 'string', desc: 'Score tier: hot|warm|cold' },
      { name: 'session_state.approved', type: 'bool', desc: 'Passed score threshold' },
    ],
  },
};

/** Empty canvas: a single "Add next node" placeholder. */
export const getInitialElements = (): { nodes: WorkflowNode[]; edges: Edge[] } => {
  const nodes: WorkflowNode[] = [
    { id: 'add0', type: 'add', position: { x: 0, y: 0 }, data: { label: '' } },
  ];
  return getLayoutedElements(nodes, []);
};
