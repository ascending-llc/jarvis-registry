import type { AgentInfo, NodeData } from './types';

const getDefaultStepObjective = (desc: string): string =>
  `Instructions for the Agent to complete the task, e.g.:\n${desc}`;

export const getDefaultNodeData = (type: string, label: string, desc: string): NodeData => {
  const base = { label, description: desc || '' };
  const executionBase = { ...base, stepObjective: getDefaultStepObjective(desc) };
  if (type === 'parallel') {
    const pData = base as import('./types').ParallelNodeData;
    return { ...base, branches: pData.branches || ['Branch A', 'Branch B'] };
  }
  if (type === 'router') {
    const rData = base as import('./types').RouterNodeData;
    return {
      ...base,
      cases: rData.cases || ['critical', 'normal'],
      routeBy: 'session_state.severity',
      defaultCase: 'low-priority',
    };
  }
  if (type === 'loop') return { ...base, maxIterations: 5, exitCondition: 'session_state.done == true' };
  if (type === 'pool') {
    return {
      ...executionBase,
      agents: [
        { id: 'classifier-agent', label: 'Classifier Agent', desc: '', path: 'classifier-agent' },
        { id: 'responder-agent', label: 'Responder Agent', desc: '', path: 'responder-agent' },
      ] satisfies AgentInfo[],
    };
  }
  if (type === 'cond') return { ...base, expression: 'session_state.score > 0.8' };
  if (type === 'agent' || type === 'mcp') return executionBase as NodeData;
  return base as NodeData;
};
