import React, { Suspense, lazy } from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { AuthProvider, useAuth } from './contexts/AuthContext.jsx';
import { ProjectProvider } from './contexts/ProjectContext.jsx';
import { ChatProvider } from './contexts/ChatContext.jsx';
import { ProjectRunProvider } from './contexts/ProjectRunContext.jsx';
import { ThemeProvider } from './contexts/ThemeContext.jsx';
import { ToastProvider } from './components/common/Toast';
import ErrorBoundary from './components/common/ErrorBoundary';
import { FullPageSpinner } from './components/common/Spinner';
import Layout from './components/layout/Layout.jsx';

const Intro = lazy(() => import('./pages/Intro.jsx'));
const LoginForm = lazy(() => import('./components/auth/LoginForm.jsx'));
const RegisterForm = lazy(() => import('./components/auth/RegisterForm.jsx'));
const ForgotPasswordForm = lazy(() => import('./components/auth/ForgotPasswordForm.jsx'));
const Dashboard = lazy(() => import('./pages/Dashboard.jsx'));
const ProjectDetail = lazy(() => import('./pages/projects/ProjectDetail.jsx'));
const Settings = lazy(() => import('./pages/Settings.jsx'));
const Profile = lazy(() => import('./pages/Profile.jsx'));
const SystemDocs = lazy(() => import('./pages/SystemDocs.jsx'));

const ProtectedRoute = ({ children }) => {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) return <FullPageSpinner label="Checking your session…" />;
  return user ? children : <Navigate to="/login" state={{ from: location.pathname }} replace />;
};

const PublicOnlyRoute = ({ children }) => {
  const { user, loading } = useAuth();
  if (loading) return <FullPageSpinner label="Loading…" />;
  return user ? <Navigate to="/dashboard" replace /> : children;
};

const App = () => (
  <ErrorBoundary>
    <ThemeProvider>
      <Router>
        <ToastProvider>
          <AuthProvider>
            <ProjectProvider>
              <ProjectRunProvider>
                <ChatProvider>
                <Suspense fallback={<FullPageSpinner label="Loading…" />}>
                  <Routes>
                    <Route path="/" element={<Intro />} />
                    <Route path="/intro" element={<Intro />} />

                    <Route
                      path="/login"
                      element={
                        <PublicOnlyRoute>
                          <LoginForm />
                        </PublicOnlyRoute>
                      }
                    />
                    <Route
                      path="/register"
                      element={
                        <PublicOnlyRoute>
                          <RegisterForm />
                        </PublicOnlyRoute>
                      }
                    />
                    <Route
                      path="/forgot-password"
                      element={
                        <PublicOnlyRoute>
                          <ForgotPasswordForm />
                        </PublicOnlyRoute>
                      }
                    />

                    <Route
                      path="/dashboard"
                      element={
                        <ProtectedRoute>
                          <Layout>
                            <Dashboard />
                          </Layout>
                        </ProtectedRoute>
                      }
                    />
                    <Route
                      path="/projects/:projectId"
                      element={
                        <ProtectedRoute>
                          <Layout>
                            <ProjectDetail />
                          </Layout>
                        </ProtectedRoute>
                      }
                    />
                    <Route
                      path="/settings"
                      element={
                        <ProtectedRoute>
                          <Layout>
                            <Settings />
                          </Layout>
                        </ProtectedRoute>
                      }
                    />
                    <Route
                      path="/profile"
                      element={
                        <ProtectedRoute>
                          <Layout>
                            <Profile />
                          </Layout>
                        </ProtectedRoute>
                      }
                    />
                    <Route
                      path="/docs"
                      element={
                        <ProtectedRoute>
                          <Layout>
                            <SystemDocs />
                          </Layout>
                        </ProtectedRoute>
                      }
                    />

                    <Route path="*" element={<Navigate to="/" replace />} />
                  </Routes>
                </Suspense>
                </ChatProvider>
              </ProjectRunProvider>
            </ProjectProvider>
          </AuthProvider>
        </ToastProvider>
      </Router>
    </ThemeProvider>
  </ErrorBoundary>
);

export default App;
