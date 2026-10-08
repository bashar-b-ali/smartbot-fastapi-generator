import React from 'react';
import { AlertOctagon, RotateCw } from 'lucide-react';
import Button from './Button';

class ErrorBoundary extends React.Component {
  state = { error: null };

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error('Unhandled UI error:', error, info?.componentStack);
  }

  handleReset = () => {
    this.setState({ error: null });
    if (typeof window !== 'undefined') window.location.assign('/');
  };

  render() {
    if (!this.state.error) return this.props.children;

    return (
      <div className="min-h-screen flex items-center justify-center p-6 bg-surface-muted">
        <div className="max-w-md w-full text-center bg-surface-raised border border-line rounded-2xl shadow-soft p-8">
          <div className="mx-auto h-14 w-14 rounded-2xl bg-red-100 dark:bg-red-500/10 text-red-600 dark:text-red-300 flex items-center justify-center mb-5">
            <AlertOctagon className="h-7 w-7" />
          </div>
          <h1 className="text-xl font-semibold text-ink">Something went wrong</h1>
          <p className="mt-2 text-sm text-ink-muted">
            An unexpected error occurred. Please try again — if the problem persists, refresh the page.
          </p>
          <pre className="mt-5 text-left text-xs text-ink-subtle bg-surface-muted rounded-lg p-3 overflow-auto max-h-40">
            {String(this.state.error?.message || this.state.error)}
          </pre>
          <Button
            variant="primary"
            size="lg"
            className="mt-6"
            leftIcon={<RotateCw className="h-4 w-4" />}
            onClick={this.handleReset}
          >
            Reload app
          </Button>
        </div>
      </div>
    );
  }
}

export default ErrorBoundary;
