import axios from 'axios';
import type React from 'react';
import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { getBasePath } from '@/config';
import SERVICES from '@/services';
import { ServerConnection } from '@/services/mcp/type';
import type { PermissionType, Server } from '@/services/server/type';

export interface ServerInfo {
  id: string;
  name: string;
  title: string;
  permissions: PermissionType;
  path: string;
  description?: string;
  official?: boolean;
  enabled: boolean;
  tags?: string[];
  lastCheckedTime?: string;
  usersCount?: number;
  rating?: number;
  status?: 'active' | 'inactive' | 'error';
  numTools?: number;
  url?: string;
  numStars?: number;
  isPython?: boolean;
  connectionState: ServerConnection;
  requiresOauth: boolean;
}

interface Agent {
  name: string;
  path: string;
  url?: string;
  description?: string;
  version?: string;
  visibility?: 'public' | 'private' | 'group-restricted';
  trust_level?: 'community' | 'verified' | 'trusted' | 'unverified';
  enabled: boolean;
  tags?: string[];
  last_checked_time?: string;
  usersCount?: number;
  rating?: number;
  status?: 'healthy' | 'healthy-auth-expired' | 'unhealthy' | 'unknown';
}

interface ServerStats {
  total: number;
  enabled: number;
  disabled: number;
  withIssues: number;
}

interface AgentStats {
  total: number;
  enabled: number;
  disabled: number;
  withIssues: number;
}

interface ServerContextType {
  // Server state
  servers: ServerInfo[];
  setServers: React.Dispatch<React.SetStateAction<ServerInfo[]>>;
  stats: ServerStats;
  serverLoading: boolean;
  serverError: string | null;

  // Agent state
  agents: Agent[];
  setAgents: React.Dispatch<React.SetStateAction<Agent[]>>;
  agentStats: AgentStats;
  agentLoading: boolean;
  agentError: string | null;

  // Shared state
  viewMode: 'servers' | 'agents';
  setViewMode: (mode: 'servers' | 'agents') => void;
  activeFilter: string;
  setActiveFilter: (filter: string) => void;

  // Actions
  refreshServerData: (notLoading?: boolean) => Promise<ServerInfo[]>;
  refreshAgentData: (notLoading?: boolean) => Promise<void>;
  handleServerUpdate: (id: string, updates: Partial<ServerInfo>) => void;
  getServerStatusByPolling: (serverId: string, callback?: (state: ServerConnection | undefined) => void) => void;
  cancelPolling: (serverId?: string) => void;
}

const ServerContext = createContext<ServerContextType | undefined>(undefined);

export const useServer = () => {
  const context = useContext(ServerContext);
  if (context === undefined) {
    throw new Error('useServer must be used within an ServerProvider');
  }
  return context;
};

interface ServerProviderProps {
  children: ReactNode;
}

export const ServerProvider: React.FC<ServerProviderProps> = ({ children }) => {
  const [servers, setServers] = useState<ServerInfo[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [viewMode, setViewMode] = useState<'servers' | 'agents'>('servers');
  const [activeFilter, setActiveFilter] = useState<string>('all');
  const [serverLoading, setServerLoading] = useState(true);
  const [agentLoading, setAgentLoading] = useState(true);
  const [serverError, setServerError] = useState<string | null>(null);
  const [agentError, setAgentError] = useState<string | null>(null);
  const timeoutRef = useRef<Record<string, NodeJS.Timeout>>({});

  // Calculate server stats
  const stats = useMemo<ServerStats>(
    () => ({
      total: servers.length,
      enabled: servers.filter(s => s.enabled).length,
      disabled: servers.filter(s => !s.enabled).length,
      withIssues: servers.filter(s => s.status === 'inactive' || s.status === 'error').length,
    }),
    [servers],
  );

  // Calculate agent stats
  const agentStats = useMemo<AgentStats>(
    () => ({
      total: agents.length,
      enabled: agents.filter(a => a.enabled).length,
      disabled: agents.filter(a => !a.enabled).length,
      withIssues: agents.filter(a => a.status === 'unhealthy').length,
    }),
    [agents],
  );

  // Helper function to map backend health status to frontend status
  const mapHealthStatus = (healthStatus: string): 'healthy' | 'unhealthy' | 'unknown' => {
    if (!healthStatus || healthStatus === 'unknown') return 'unknown';
    if (healthStatus === 'healthy') return 'healthy';
    if (healthStatus.includes('unhealthy') || healthStatus.includes('error') || healthStatus.includes('timeout'))
      return 'unhealthy';
    return 'unknown';
  };

  const handleServerUpdate = (id: string, updates: Partial<ServerInfo>) => {
    setServers(prevServers => prevServers.map(server => (server.id === id ? { ...server, ...updates } : server)));
  };

  const constructServerData = (serversList: Server[]): ServerInfo[] => {
    return serversList.map((serverInfo: Server) => {
      return {
        id: serverInfo.id,
        name: serverInfo.serverName || 'Unknown Server',
        title: serverInfo.title,
        permissions: serverInfo.permissions,
        path: serverInfo.path,
        description: serverInfo.description || '',
        official: serverInfo.isOfficial || false,
        enabled: serverInfo.enabled !== undefined ? serverInfo.enabled : false,
        tags: serverInfo.tags || [],
        lastCheckedTime: serverInfo.lastConnected,
        usersCount: 0,
        rating: serverInfo.numStars || 0,
        status: serverInfo.status || 'unknown', // undefined
        numTools: serverInfo.numTools || 0,
        url: serverInfo.url,
        numStars: serverInfo.numStars || 0,
        isPython: serverInfo.isPython || false,
        requiresOauth: serverInfo.requiresOauth,
        connectionState: serverInfo.connectionState,
      };
    });
  };

  const refreshServerData = useCallback(async (notLoading?: boolean): Promise<ServerInfo[]> => {
    try {
      if (!notLoading) setServerLoading(true);
      setServerError(null);
      const result = await SERVICES.SERVER.getServers();
      const transformedServers: ServerInfo[] = constructServerData(result?.servers || []);
      setServers(transformedServers);
      return transformedServers;
    } catch (error: any) {
      setServerError(error?.data?.detail || 'Failed to fetch servers');
      return [];
    } finally {
      setServerLoading(false);
    }
  }, []);

  const refreshAgentData = useCallback(async (notLoading?: boolean) => {
    try {
      if (!notLoading) setAgentLoading(true);
      setAgentError(null);
      const agentsResponse = await axios.get('/api/agents').catch(() => ({ data: { agents: [] } }));
      const agentsData = agentsResponse.data || {};
      const agentsList = agentsData.agents || [];

      const transformedAgents: Agent[] = agentsList.map((agentInfo: any) => ({
        name: agentInfo.display_name || agentInfo.name || 'Unknown Agent',
        path: agentInfo.path,
        url: agentInfo.url,
        description: agentInfo.description || '',
        version: agentInfo.version,
        visibility: agentInfo.visibility || 'private',
        trust_level: agentInfo.trust_level || 'community',
        enabled: agentInfo.is_enabled !== undefined ? agentInfo.is_enabled : false,
        tags: agentInfo.tags || [],
        last_checked_time: agentInfo.last_checked_iso,
        usersCount: 0,
        rating: agentInfo.num_stars || 0,
        status: mapHealthStatus(agentInfo.health_status || 'unknown'),
      }));
      setAgents(transformedAgents);
    } catch (error: any) {
      setAgentError(error?.detail || 'Failed to fetch agents');
    } finally {
      setAgentLoading(false);
    }
  }, []);

  useEffect(() => {
    const isOnLoginPage = typeof window !== 'undefined' && window.location.pathname === `${getBasePath()}/login`;
    if (isOnLoginPage) {
      setServerLoading(false);
      setAgentLoading(false);
      return () => {
        Object.values(timeoutRef.current).forEach(timeout => {
          clearTimeout(timeout);
        });
        timeoutRef.current = {};
      };
    }
    refreshServerData();
    refreshAgentData();
    return () => {
      Object.values(timeoutRef.current).forEach(timeout => {
        clearTimeout(timeout);
      });
      timeoutRef.current = {};
    };
  }, [refreshAgentData, refreshServerData]);

  const getServerStatusById = useCallback(async (serverId: string): Promise<ServerConnection | undefined> => {
    try {
      const result = await SERVICES.MCP.getServerStatusById(serverId);
      handleServerUpdate(serverId, { connectionState: result.connectionState });
      return result.connectionState;
    } catch (error: any) {
      console.log('error', error);
    }
  }, []);

  const getServerStatusByPolling = useCallback(
    async (serverId: string, callback?: (state: ServerConnection | undefined) => void) => {
      // Clear existing timeout for this specific server if it exists
      if (timeoutRef.current[serverId]) {
        clearTimeout(timeoutRef.current[serverId]);
        delete timeoutRef.current[serverId];
      }

      const initialServer: ServerInfo | undefined = servers.find((server: ServerInfo) => server.id === serverId);
      if (!initialServer) return;

      const poll = async () => {
        const currentState = await getServerStatusById(serverId);

        if (currentState === ServerConnection.CONNECTING) {
          timeoutRef.current[serverId] = setTimeout(() => {
            poll();
          }, 5000);
        } else {
          // Stop polling for this server
          if (timeoutRef.current[serverId]) {
            clearTimeout(timeoutRef.current[serverId]);
            delete timeoutRef.current[serverId];

            const result = await SERVICES.SERVER.refreshServerHealth(serverId);
            handleServerUpdate(serverId, {
              lastCheckedTime: result.lastConnected,
              numTools: result.numTools,
              status: result.status || 'unknown',
            });
          }
        }
        callback?.(currentState);
      };

      await poll();
    },
    [refreshServerData, servers],
  );

  const cancelPolling = useCallback((serverId?: string) => {
    if (serverId) {
      if (timeoutRef.current[serverId]) {
        clearTimeout(timeoutRef.current[serverId]);
        delete timeoutRef.current[serverId];
      }
    } else {
      Object.values(timeoutRef.current).forEach(timeout => {
        clearTimeout(timeout);
      });
      timeoutRef.current = {};
    }
  }, []);

  const value: ServerContextType = {
    servers,
    setServers,
    stats,
    serverLoading,
    serverError,

    agents,
    setAgents,
    agentStats,
    agentLoading,
    agentError,

    viewMode,
    setViewMode,
    activeFilter,
    setActiveFilter,

    refreshServerData,
    refreshAgentData,
    handleServerUpdate,
    getServerStatusByPolling,
    cancelPolling,
  };

  return <ServerContext.Provider value={value}>{children}</ServerContext.Provider>;
};
