import { useCallback, useEffect, useRef, useState } from 'react';
import SERVICES from '@/services';
import type { PrincipalSearchResult } from '@/services/acl/type';

export interface PrincipalSearchState {
  query: string;
  setQuery: (val: string) => void;
  results: PrincipalSearchResult[];
  showDropdown: boolean;
  setShowDropdown: (val: boolean) => void;
  loading: boolean;
  containerRef: React.RefObject<HTMLDivElement>;
  select: (result: PrincipalSearchResult) => void;
}

interface UsePrincipalSearchOptions {
  isOpen: boolean;
  existingKeys: Set<string>;
  onSelect: (result: PrincipalSearchResult) => void;
}

export const usePrincipalSearch = ({
  isOpen,
  existingKeys,
  onSelect,
}: UsePrincipalSearchOptions): PrincipalSearchState => {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<PrincipalSearchResult[]>([]);
  const [showDropdown, setShowDropdown] = useState(false);
  const [loading, setLoading] = useState(false);

  const containerRef = useRef<HTMLDivElement>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;

  useEffect(() => {
    if (isOpen) {
      setQuery('');
      setResults([]);
    }
  }, [isOpen]);

  useEffect(() => {
    if (timerRef.current) clearTimeout(timerRef.current);
    if (!query.trim()) {
      setResults([]);
      setShowDropdown(false);
      setLoading(false);
      return;
    }

    setLoading(true);
    setShowDropdown(true);

    timerRef.current = setTimeout(async () => {
      try {
        const data = await SERVICES.ACL.searchPrincipals(query, 10);
        setResults(data.filter(r => !existingKeys.has(`${r.principalType}:${r.principalId}`)));
      } catch {
        setResults([]);
      } finally {
        setLoading(false);
      }
    }, 300);

    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [query, existingKeys]);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setShowDropdown(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const select = useCallback((result: PrincipalSearchResult) => {
    onSelectRef.current(result);
    setQuery('');
    setShowDropdown(false);
  }, []);

  return {
    query,
    setQuery,
    results,
    showDropdown,
    setShowDropdown,
    loading,
    containerRef,
    select,
  };
};
