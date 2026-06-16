import { useEffect, useId, useState } from 'react';
import API from '@/services/api';
import Request from '@/services/request';

type EntityType = 'mcp_server' | 'tool' | 'a2a_agent' | 'skill';

const DEFAULT_ENTITY_TYPES: EntityType[] = ['mcp_server', 'tool', 'a2a_agent', 'skill'];
const DEFAULT_ENTITY_TYPES_KEY = DEFAULT_ENTITY_TYPES.join('|');

export interface MatchingToolHit {
  toolName: string;
  description?: string;
  relevanceScore: number;
  matchContext?: string;
}

export interface SemanticServerHit {
  path: string;
  serverId?: string;
  serverName: string;
  description?: string;
  tags: string[];
  numTools: number;
  enabled: boolean;
  relevanceScore: number;
  matchContext?: string;
  matchingTools: MatchingToolHit[];
}

export interface SemanticToolHit {
  serverPath: string;
  serverName: string;
  toolName: string;
  description?: string;
  relevanceScore: number;
  matchContext?: string;
}

export interface SemanticAgentHit {
  path: string;
  agentId?: string;
  agentName: string;
  description?: string;
  tags: string[];
  skills: string[];
  trustLevel?: string;
  visibility?: string;
  enabled?: boolean;
  url?: string;
  agentCard?: Record<string, any>;
  relevanceScore: number;
  matchContext?: string;
}

export interface SemanticSkillHit {
  agentId: string;
  agentPath: string;
  agentName: string;
  skillName: string;
  description?: string;
  relevanceScore: number;
  matchContext?: string;
}

export interface SemanticSearchResponse {
  query: string;
  servers: SemanticServerHit[];
  tools: SemanticToolHit[];
  agents: SemanticAgentHit[];
  skills: SemanticSkillHit[];
  totalServers: number;
  totalTools: number;
  totalAgents: number;
  totalSkills: number;
}

interface UseSemanticSearchOptions {
  enabled?: boolean;
  minLength?: number;
  maxResults?: number;
  entityTypes?: EntityType[];
}

interface UseSemanticSearchReturn {
  results: SemanticSearchResponse | null;
  loading: boolean;
  error: string | null;
}

export const useSemanticSearch = (query: string, options: UseSemanticSearchOptions = {}): UseSemanticSearchReturn => {
  const [results, setResults] = useState<SemanticSearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const instanceId = useId();
  const cancelKey = `semanticSearch_${instanceId}`;

  const enabled = options.enabled ?? true;
  const minLength = options.minLength ?? 1;
  const maxResults = options.maxResults ?? 10;
  const entityTypes = options.entityTypes ?? DEFAULT_ENTITY_TYPES;
  const entityTypesKey = options.entityTypes?.join('|') ?? DEFAULT_ENTITY_TYPES_KEY;

  useEffect(() => {
    const trimmedQuery = query.trim();
    if (!enabled || trimmedQuery.length < minLength) {
      setResults(null);
      setError(null);
      setLoading(false);
      return;
    }

    let cancelled = false;

    const runSearch = async () => {
      setLoading(true);
      setError(null);
      try {
        const responseData = await Request.post(
          API.getSearch,
          {
            query: trimmedQuery,
            entityTypes,
            maxResults,
            includeDisabled: false,
          },
          { cancelTokenKey: cancelKey },
        );

        if (responseData && (responseData as any).Code === -200) {
          return; // Request was cancelled
        }

        if (!cancelled) {
          setResults(responseData as SemanticSearchResponse);
        }
      } catch (err: any) {
        if (cancelled) return;
        const message = err?.detail || err?.message || 'Semantic search failed.';
        setError(message);
        setResults(null);
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    runSearch();

    return () => {
      cancelled = true;
      Request.cancels[cancelKey]?.();
    };
  }, [query, enabled, minLength, maxResults, entityTypesKey, cancelKey]);

  return { results, loading, error };
};
