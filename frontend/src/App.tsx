import { Route, BrowserRouter as Router, Routes } from 'react-router-dom';
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

function App() {
  return (
    <ThemeProvider>
      <AuthProvider>
        <GlobalProvider>
          <ServerProvider>
            <Router basename={getBasePath() || '/'}>
              <Routes>
                <Route path='/login' element={<Login />} />
                <Route
                  path='/oauth-callback'
                  element={
                    <ProtectedRoute>
                      <OAuthCallback />
                    </ProtectedRoute>
                  }
                />
                <Route
                  path='/'
                  element={
                    <ProtectedRoute>
                      <Layout>
                        <Dashboard />
                      </Layout>
                    </ProtectedRoute>
                  }
                />
                <Route
                  path='/server-registry'
                  element={
                    <ProtectedRoute>
                      <Layout>
                        <ServerRegistryOrEdit />
                      </Layout>
                    </ProtectedRoute>
                  }
                />
                <Route
                  path='/server-edit'
                  element={
                    <ProtectedRoute>
                      <Layout>
                        <ServerRegistryOrEdit />
                      </Layout>
                    </ProtectedRoute>
                  }
                />
                <Route
                  path='/agent-registry'
                  element={
                    <ProtectedRoute>
                      <Layout>
                        <AgentRegistryOrEdit />
                      </Layout>
                    </ProtectedRoute>
                  }
                />
                <Route
                  path='/agent-edit'
                  element={
                    <ProtectedRoute>
                      <Layout>
                        <AgentRegistryOrEdit />
                      </Layout>
                    </ProtectedRoute>
                  }
                />
                <Route
                  path='/federation-registry'
                  element={
                    <ProtectedRoute>
                      <Layout>
                        <FederationRegistryOrEdit />
                      </Layout>
                    </ProtectedRoute>
                  }
                />
                <Route
                  path='/federation-edit'
                  element={
                    <ProtectedRoute>
                      <Layout>
                        <FederationRegistryOrEdit />
                      </Layout>
                    </ProtectedRoute>
                  }
                />
                <Route
                  path='/generate-token'
                  element={
                    <ProtectedRoute>
                      <Layout>
                        <TokenGeneration />
                      </Layout>
                    </ProtectedRoute>
                  }
                />
              </Routes>
            </Router>
          </ServerProvider>
        </GlobalProvider>
      </AuthProvider>
    </ThemeProvider>
  );
}

export default App;
