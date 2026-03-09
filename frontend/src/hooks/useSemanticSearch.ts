import axios from 'axios';
import { useEffect, useState } from 'react';
import API from '@/services/api';

type EntityType = 'mcpServer' | 'tool' | 'a2aAgent';

const DEFAULT_ENTITY_TYPES: EntityType[] = ['mcpServer', 'tool', 'a2aAgent'];
const DEFAULT_ENTITY_TYPES_KEY = DEFAULT_ENTITY_TYPES.join('|');

export interface MatchingToolHit {
  toolName: string;
  description?: string;
  relevanceScore: number;
  matchContext?: string;
}

export interface SemanticServerHit {
  path: string;
  serverName: string;
  description?: string;
  tags: string[];
  numTools: number;
  isEnabled: boolean;
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
  agentName: string;
  description?: string;
  tags: string[];
  skills: string[];
  trustLevel?: string;
  visibility?: string;
  isEnabled?: boolean;
  url?: string;
  agentCard?: Record<string, any>;
  relevanceScore: number;
  matchContext?: string;
}

export interface SemanticSearchResponse {
  query: string;
  servers: SemanticServerHit[];
  tools: SemanticToolHit[];
  agents: SemanticAgentHit[];
  totalServers: number;
  totalTools: number;
  totalAgents: number;
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
  debouncedQuery: string;
}

export const useSemanticSearch = (query: string, options: UseSemanticSearchOptions = {}): UseSemanticSearchReturn => {
  const [results, setResults] = useState<SemanticSearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [debouncedQuery, setDebouncedQuery] = useState('');

  const enabled = options.enabled ?? true;
  const minLength = options.minLength ?? 2;
  const maxResults = options.maxResults ?? 10;
  const entityTypes = options.entityTypes ?? DEFAULT_ENTITY_TYPES;
  const entityTypesKey = options.entityTypes?.join('|') ?? DEFAULT_ENTITY_TYPES_KEY;

  // Debounce user input to minimize API calls
  useEffect(() => {
    const handle = setTimeout(() => {
      setDebouncedQuery(query.trim());
    }, 350);

    return () => clearTimeout(handle);
  }, [query]);

  useEffect(() => {
    if (!enabled || debouncedQuery.length < minLength) {
      setResults(null);
      setError(null);
      setLoading(false);
      return;
    }

    let cancelled = false;
    const controller = new AbortController();

    const runSearch = async () => {
      setLoading(true);
      setError(null);
      try {
        const response = await axios.post<SemanticSearchResponse>(
          API.getSemanticSearch,
          {
            query: debouncedQuery,
            entityTypes,
            maxResults,
          },
          { signal: controller.signal },
        );
        if (!cancelled) {
          setResults(response.data);
        }
      } catch (err: any) {
        if (axios.isCancel(err) || cancelled) return;
        const message = err.response?.data?.detail || err.message || 'Semantic search failed.';
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
      controller.abort();
    };
  }, [debouncedQuery, enabled, minLength, maxResults, entityTypesKey]);

  return { results, loading, error, debouncedQuery };
};
