import type React from 'react';
import { createContext, type ReactNode, useContext, useEffect, useState } from 'react';

import { APP_ROUTES, getBrowserPath, isLoginBrowserPath } from '@/routes';
import SERVICES from '@/services';

interface User {
  username: string;
  userId?: string;
  email?: string;
  scopes?: string[];
  groups?: string[];
  authMethod?: string;
  provider?: string;
  canModifyServers?: boolean;
  isAdmin?: boolean;
}

interface AuthContextType {
  user: User | null;
  logout: () => Promise<void>;
  loading: boolean;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};

interface AuthProviderProps {
  children: ReactNode;
}

export const AuthProvider: React.FC<AuthProviderProps> = ({ children }) => {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const isOnLoginPage = typeof window !== 'undefined' && isLoginBrowserPath(window.location.pathname);
    if (isOnLoginPage) {
      setUser(null);
      setLoading(false);
      return;
    }
    checkAuth();
  }, []);

  const checkAuth = async () => {
    try {
      const userData = await SERVICES.AUTH.getAuthMe();
      setUser({
        username: userData.username,
        userId: userData.userId,
        email: userData.email,
        scopes: userData.scopes || [],
        groups: userData.groups || [],
        authMethod: userData.authMethod || 'basic',
        provider: userData.provider,
        canModifyServers: userData.canModifyServers || false,
        isAdmin: userData.isAdmin || false,
      });
    } catch (_error) {
      // User not authenticated
      setUser(null);
    } finally {
      setLoading(false);
    }
  };

  const logout = async () => {
    try {
      await SERVICES.AUTH.logout();
    } catch (_error) {
      // Ignore errors during logout
    } finally {
      const loginPath = getBrowserPath(APP_ROUTES.login);
      try {
        window.location.replace(loginPath);
      } catch (_error) {
        window.location.assign(loginPath);
      }
    }
  };

  const value = {
    user,
    logout,
    loading,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};
