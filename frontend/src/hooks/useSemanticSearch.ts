import axios from 'axios';
import { useEffect, useState } from 'react';
import API from '@/services/api';

type EntityType = 'mcp_server' | 'tool' | 'a2a_agent';

const DEFAULT_ENTITY_TYPES: EntityType[] = ['mcp_server', 'tool', 'a2a_agent'];
const DEFAULT_ENTITY_TYPES_KEY = DEFAULT_ENTITY_TYPES.join('|');

export interface MatchingToolHit {
  tool_name: string;
  description?: string;
  relevance_score: number;
  match_context?: string;
}

export interface SemanticServerHit {
  path: string;
  server_name: string;
  description?: string;
  tags: string[];
  num_tools: number;
  is_enabled: boolean;
  relevance_score: number;
  match_context?: string;
  matching_tools: MatchingToolHit[];
}

export interface SemanticToolHit {
  server_path: string;
  server_name: string;
  tool_name: string;
  description?: string;
  relevance_score: number;
  match_context?: string;
}

export interface SemanticAgentHit {
  path: string;
  agent_name: string;
  description?: string;
  tags: string[];
  skills: string[];
  trust_level?: string;
  visibility?: string;
  is_enabled?: boolean;
  url?: string;
  agent_card?: Record<string, any>;
  relevance_score: number;
  match_context?: string;
}

export interface SemanticSearchResponse {
  query: string;
  servers: SemanticServerHit[];
  tools: SemanticToolHit[];
  agents: SemanticAgentHit[];
  total_servers: number;
  total_tools: number;
  total_agents: number;
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
            entity_types: entityTypes,
            max_results: maxResults,
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
