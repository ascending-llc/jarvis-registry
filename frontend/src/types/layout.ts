export type ResourceStatusFilter = 'all' | 'enabled' | 'disabled';

export type ResourceStats = {
  total: number;
  enabled: number;
  disabled: number;
};

export type LayoutSearchConfig = {
  value: string;
  placeholder: string;
  onChange: (value: string) => void;
  onClear: () => void;
};

export type SkillScanFilter = 'all' | 'review' | 'findings' | 'clean' | 'outdated' | 'unscanned' | 'failed';

export type SkillsNavigationConfig = {
  stats: ResourceStats;
  activeFilter: ResourceStatusFilter;
  onFilterChange: (filter: ResourceStatusFilter) => void;
  showStatusFilters: boolean;
  scanFilters?: {
    active: SkillScanFilter;
    items: { value: SkillScanFilter; label: string; count: number; dotClass: string }[];
    onChange: (filter: SkillScanFilter) => void;
  };
};

export type ModelProviderFilter = 'all' | 'aws_bedrock' | 'azure_openai' | 'openai' | 'anthropic';

export type ModelsNavigationConfig = {
  total: number;
  providerCounts: Record<ModelProviderFilter, number>;
  activeProvider: ModelProviderFilter;
  onProviderChange: (provider: ModelProviderFilter) => void;
  showProviderFilters: boolean;
};
