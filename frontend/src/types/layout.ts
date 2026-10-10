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

export type SkillsNavigationConfig = {
  stats: ResourceStats;
  activeFilter: ResourceStatusFilter;
  onFilterChange: (filter: ResourceStatusFilter) => void;
  showStatusFilters: boolean;
};

export type ModelProviderFilter = 'all' | 'aws_bedrock' | 'azure_openai';

export type ModelsNavigationConfig = {
  total: number;
  providerCounts: Record<ModelProviderFilter, number>;
  activeProvider: ModelProviderFilter;
  onProviderChange: (provider: ModelProviderFilter) => void;
  showProviderFilters: boolean;
};
