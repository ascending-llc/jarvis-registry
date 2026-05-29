import { createBrowserRouter, RouterProvider } from 'react-router-dom';
import Layout from './components/Layout';
import ProtectedRoute from './components/ProtectedRoute';
import { getBasePath } from './config';
import { AuthProvider } from './contexts/AuthContext';
import { GlobalProvider } from './contexts/GlobalContext';
import { ServerProvider } from './contexts/ServerContext';
import { ThemeProvider } from './contexts/ThemeContext';
import AgentRegistryOrEdit from './pages/AgentRegistryOrEdit';
import Dashboard from './pages/Dashboard';
import FederationRegistryOrEdit from './pages/FederationRegistryOrEdit';
import Login from './pages/Login';
import OAuthCallback from './pages/OAuthCallback';
import ServerRegistryOrEdit from './pages/ServerRegistryOrEdit';
import TokenGeneration from './pages/TokenGeneration';
import WorkflowRegistryOrEdit from './pages/WorkflowRegistryOrEdit';

const router = createBrowserRouter(
  [
    { path: '/login', element: <Login /> },
    {
      path: '/oauth-callback',
      element: (
        <ProtectedRoute>
          <OAuthCallback />
        </ProtectedRoute>
      ),
    },
    {
      path: '/',
      element: (
        <ProtectedRoute>
          <Layout>
            <Dashboard />
          </Layout>
        </ProtectedRoute>
      ),
    },
    {
      path: '/server-registry',
      element: (
        <ProtectedRoute>
          <Layout>
            <ServerRegistryOrEdit />
          </Layout>
        </ProtectedRoute>
      ),
    },
    {
      path: '/server-edit',
      element: (
        <ProtectedRoute>
          <Layout>
            <ServerRegistryOrEdit />
          </Layout>
        </ProtectedRoute>
      ),
    },
    {
      path: '/agent-registry',
      element: (
        <ProtectedRoute>
          <Layout>
            <AgentRegistryOrEdit />
          </Layout>
        </ProtectedRoute>
      ),
    },
    {
      path: '/agent-edit',
      element: (
        <ProtectedRoute>
          <Layout>
            <AgentRegistryOrEdit />
          </Layout>
        </ProtectedRoute>
      ),
    },
    {
      path: '/federation-registry',
      element: (
        <ProtectedRoute>
          <Layout>
            <FederationRegistryOrEdit />
          </Layout>
        </ProtectedRoute>
      ),
    },
    {
      path: '/federation-edit',
      element: (
        <ProtectedRoute>
          <Layout>
            <FederationRegistryOrEdit />
          </Layout>
        </ProtectedRoute>
      ),
    },
    {
      path: '/workflow-registry',
      element: (
        <ProtectedRoute>
          <Layout>
            <WorkflowRegistryOrEdit />
          </Layout>
        </ProtectedRoute>
      ),
    },
    {
      path: '/workflow-edit',
      element: (
        <ProtectedRoute>
          <Layout>
            <WorkflowRegistryOrEdit />
          </Layout>
        </ProtectedRoute>
      ),
    },
    {
      path: '/generate-token',
      element: (
        <ProtectedRoute>
          <Layout>
            <TokenGeneration />
          </Layout>
        </ProtectedRoute>
      ),
    },
  ],
  { basename: getBasePath() || '/' },
);

function App() {
  return (
    <ThemeProvider>
      <AuthProvider>
        <GlobalProvider>
          <ServerProvider>
            <RouterProvider router={router} />
          </ServerProvider>
        </GlobalProvider>
      </AuthProvider>
    </ThemeProvider>
  );
}

export default App;
