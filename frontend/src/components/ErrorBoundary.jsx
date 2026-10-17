import { Component } from 'react'
import { AlertCircle, RotateCcw } from 'lucide-react'
import { ICON } from '../lib/utils'

export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    console.error('UI error:', error, info?.componentStack)
  }

  render() {
    if (!this.state.error) return this.props.children
    const box = (
      <div className="card p-5">
        <div className="flex items-start gap-3">
          <AlertCircle {...ICON} className="mt-0.5 shrink-0 text-rose-500" />
          <div className="min-w-0">
            <p className="text-sm font-medium text-fg">{this.props.title || 'This section failed to load'}</p>
            <p className="mt-1 break-words font-mono text-xs text-fg-3">{String(this.state.error?.message || this.state.error)}</p>
            <button className="btn mt-3" onClick={() => this.setState({ error: null })}>
              <RotateCcw {...ICON} /> Try again
            </button>
          </div>
        </div>
      </div>
    )
    return this.props.fullscreen ? <div className="flex min-h-screen items-center justify-center bg-bg p-6"><div className="w-full max-w-md">{box}</div></div> : box
  }
}
