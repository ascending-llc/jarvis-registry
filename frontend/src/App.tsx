import { createBrowserRouter, Navigate, RouterProvider, useLocation, useParams } from 'react-router-dom';
import Layout from './components/Layout';
import ProtectedRoute from './components/ProtectedRoute';
import { getBasePath } from './config';
import { AuthProvider } from './contexts/AuthContext';
import { GlobalProvider } from './contexts/GlobalContext';
import { ServerProvider } from './contexts/ServerContext';
import { ThemeProvider } from './contexts/ThemeContext';
import AgentRegistryOrEdit from './pages/AgentRegistryOrEdit';
import ConsentAgent from './pages/ConsentAgent';
import ConsentDownstream from './pages/ConsentDownstream';
import ConsentDownstreamError from './pages/ConsentDownstreamError';
import ConsentServer from './pages/ConsentServer';
import Dashboard from './pages/Dashboard';
import DeviceVerify from './pages/DeviceVerify';
import FederationRegistryOrEdit from './pages/FederationRegistryOrEdit';
import Login from './pages/Login';
import NotFound from './pages/NotFound';
import OAuthCallback from './pages/OAuthCallback';
import ServerRegistryOrEdit from './pages/ServerRegistryOrEdit';
import SkillsPage from './pages/Skills';
import TokenGeneration from './pages/TokenGeneration';
import WorkflowRegistryOrEdit from './pages/WorkflowRegistryOrEdit';
import { APP_ROUTES } from './routes';

const SkillSyncSourceRouteBridge = ({ list = false }: { list?: boolean }) => {
  const { id } = useParams();
  const location = useLocation();
  const params = new URLSearchParams(location.search);

  if (list) {
    params.set('tab', 'external');
    return <Navigate replace to={`${APP_ROUTES.root}?${params.toString()}`} />;
  }

  if (!id) return <Navigate replace to={`${APP_ROUTES.root}?tab=external`} />;
  params.set('id', id);
  params.set('provider', 'github');
  return <Navigate replace to={`${APP_ROUTES.federationEdit}?${params.toString()}`} />;
};

const router = createBrowserRouter(
  [
    { path: APP_ROUTES.login, element: <Login /> },
    {
      path: APP_ROUTES.oauthCallback,
      element: (
        <ProtectedRoute>
          <OAuthCallback />
        </ProtectedRoute>
      ),
    },
    {
      path: APP_ROUTES.device,
      element: (
        <ProtectedRoute>
          <DeviceVerify />
        </ProtectedRoute>
      ),
    },
    {
      path: APP_ROUTES.consentDownstream,
      element: (
        <ProtectedRoute>
          <ConsentDownstream />
        </ProtectedRoute>
      ),
    },
    {
      path: APP_ROUTES.consentDownstreamError,
      element: (
        <ProtectedRoute>
          <ConsentDownstreamError />
        </ProtectedRoute>
      ),
    },
    {
      path: APP_ROUTES.consentServer,
      element: (
        <ProtectedRoute>
          <ConsentServer />
        </ProtectedRoute>
      ),
    },
    {
      path: APP_ROUTES.consentAgent,
      element: (
        <ProtectedRoute>
          <ConsentAgent />
        </ProtectedRoute>
      ),
    },
    {
      path: APP_ROUTES.root,
      element: (
        <ProtectedRoute>
          <Layout>
            <Dashboard />
          </Layout>
        </ProtectedRoute>
      ),
    },
    {
      path: APP_ROUTES.serverRegistry,
      element: (
        <ProtectedRoute>
          <Layout>
            <ServerRegistryOrEdit />
          </Layout>
        </ProtectedRoute>
      ),
    },
    {
      path: APP_ROUTES.serverEdit,
      element: (
        <ProtectedRoute>
          <Layout>
            <ServerRegistryOrEdit />
          </Layout>
        </ProtectedRoute>
      ),
    },
    {
      path: APP_ROUTES.agentRegistry,
      element: (
        <ProtectedRoute>
          <Layout>
            <AgentRegistryOrEdit />
          </Layout>
        </ProtectedRoute>
      ),
    },
    {
      path: APP_ROUTES.agentEdit,
      element: (
        <ProtectedRoute>
          <Layout>
            <AgentRegistryOrEdit />
          </Layout>
        </ProtectedRoute>
      ),
    },
    {
      path: APP_ROUTES.federationRegistry,
      element: (
        <ProtectedRoute>
          <Layout>
            <FederationRegistryOrEdit />
          </Layout>
        </ProtectedRoute>
      ),
    },
    {
      path: APP_ROUTES.federationEdit,
      element: (
        <ProtectedRoute>
          <Layout>
            <FederationRegistryOrEdit />
          </Layout>
        </ProtectedRoute>
      ),
    },
    {
      path: APP_ROUTES.skillSyncSources,
      element: (
        <ProtectedRoute>
          <SkillSyncSourceRouteBridge list />
        </ProtectedRoute>
      ),
    },
    {
      path: APP_ROUTES.skillSyncSourceDetail,
      element: (
        <ProtectedRoute>
          <SkillSyncSourceRouteBridge />
        </ProtectedRoute>
      ),
    },
    {
      path: APP_ROUTES.workflowRegistry,
      element: (
        <ProtectedRoute>
          <Layout>
            <WorkflowRegistryOrEdit />
          </Layout>
        </ProtectedRoute>
      ),
    },
    {
      path: APP_ROUTES.workflowEdit,
      element: (
        <ProtectedRoute>
          <Layout>
            <WorkflowRegistryOrEdit />
          </Layout>
        </ProtectedRoute>
      ),
    },
    {
      path: APP_ROUTES.skills,
      element: (
        <ProtectedRoute>
          <SkillsPage />
        </ProtectedRoute>
      ),
    },
    {
      path: APP_ROUTES.generateToken,
      element: (
        <ProtectedRoute>
          <Layout>
            <TokenGeneration />
          </Layout>
        </ProtectedRoute>
      ),
    },
    { path: '*', element: <NotFound /> },
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
