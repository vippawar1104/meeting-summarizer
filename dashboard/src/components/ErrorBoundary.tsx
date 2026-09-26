import { Component, type ReactNode } from "react";

interface State {
  failed: boolean;
}

/** A rendering bug must never leave a blank page: show a way out instead. */
export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  componentDidCatch(error: Error): void {
    console.error("dashboard crashed", error);
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div className="center" role="alert">
        <h1>Something went wrong</h1>
        <p>The dashboard hit an unexpected problem. Reloading usually fixes it.</p>
        <button className="btn primary" onClick={() => window.location.reload()}>
          Reload
        </button>
      </div>
    );
  }
}
