import React from "react";
import { AlertTriangle, RotateCcw, Home } from "lucide-react";

export class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    console.error("ErrorBoundary caught an error:", error, errorInfo);
    if (this.props.onError) {
      this.props.onError(error, errorInfo);
    }
  }

  handleReset = () => {
    this.setState({ hasError: false, error: null });
    if (this.props.onReset) {
      this.props.onReset();
    }
  };

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) {
        if (typeof this.props.fallback === "function") {
          return this.props.fallback({
            error: this.state.error,
            reset: this.handleReset,
          });
        }
        return this.props.fallback;
      }

      const isCompact = this.props.compact;

      if (isCompact) {
        return (
          <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-red-900" role="alert">
            <div className="flex items-center gap-2 font-medium text-sm text-red-800">
              <AlertTriangle className="h-4 w-4 text-red-600 shrink-0" />
              <span>{this.props.title || "Module Error"}</span>
            </div>
            <p className="mt-1 text-xs text-red-700">
              {this.state.error?.message || "An unexpected error occurred while rendering this module."}
            </p>
            <div className="mt-3 flex gap-2">
              <button
                onClick={this.handleReset}
                className="inline-flex items-center gap-1 rounded bg-red-600 px-2.5 py-1 text-xs font-medium text-white shadow-sm hover:bg-red-700 focus:outline-none"
              >
                <RotateCcw className="h-3 w-3" />
                Retry Module
              </button>
            </div>
          </div>
        );
      }

      return (
        <div className="min-h-[300px] flex items-center justify-center p-6">
          <div className="max-w-md w-full rounded-xl border border-slate-200 bg-white p-6 shadow-sm text-center">
            <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-red-50 text-red-600 mb-4">
              <AlertTriangle className="h-6 w-6" />
            </div>
            <h3 className="text-base font-semibold text-slate-900">
              {this.props.title || "Something went wrong"}
            </h3>
            <p className="mt-2 text-xs text-slate-500 break-words font-mono bg-slate-50 p-2.5 rounded border border-slate-100 text-left">
              {this.state.error?.message || "An unexpected error occurred."}
            </p>
            <div className="mt-5 flex justify-center gap-3">
              <button
                onClick={this.handleReset}
                className="inline-flex items-center gap-1.5 rounded-lg bg-slate-900 px-4 py-2 text-xs font-medium text-white hover:bg-slate-800 transition-colors shadow-sm"
              >
                <RotateCcw className="h-3.5 w-3.5" />
                Try Again
              </button>
              <button
                onClick={() => (window.location.href = "/projects")}
                className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-4 py-2 text-xs font-medium text-slate-700 hover:bg-slate-50 transition-colors"
              >
                <Home className="h-3.5 w-3.5 text-slate-500" />
                Return to Projects
              </button>
            </div>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}

export default ErrorBoundary;
