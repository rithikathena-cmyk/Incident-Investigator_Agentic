import { useEffect, useState } from 'react'
import AuditLogPage from './components/AuditLogPage'
import ChatPanel from './components/ChatPanel'
import DetailFlowPage from './components/DetailFlowPage'
import DetailLog from './components/DetailLog'
import LoginPage from './components/LoginPage'
import SummaryPage from './components/SummaryPage'
import TracePipeline from './components/TracePipeline'
import { useInvestigation } from './hooks/useInvestigation'
import './App.css'

function hashToView(hash) {
  if (hash === '#/detail') return 'detail'
  if (hash === '#/audit') return 'audit'
  return 'chat'
}

function useView() {
  const [view, setView] = useState(() => hashToView(window.location.hash))

  useEffect(() => {
    function onHashChange() {
      setView(hashToView(window.location.hash))
    }
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  function go(next) {
    window.location.hash = next === 'chat' ? '#/' : `#/${next}`
  }

  return [view, go]
}

// Deliberately 'localhost', not '127.0.0.1': the login session cookie is
// SameSite=Lax (app/server.py), and browsers treat "localhost" and
// "127.0.0.1" as different sites even though both resolve to loopback - a
// mismatch here means the cookie silently never reaches the API/WS, which
// looks like every authenticated request being rejected for no visible
// reason. Keep this the same host the page itself is served from.
const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8787'

const AGENT_TITLES = {
  production: 'Production',
  maintenance: 'Maintenance',
  quality: 'Quality',
  knowledge: 'Knowledge',
}

function workingLabel(pipeline, running) {
  const activeAgents = pipeline.agentOrder.filter((k) => pipeline.agents[k]?.status === 'active')
  if (activeAgents.length > 0) {
    return { text: `Working: ${activeAgents.map((k) => AGENT_TITLES[k] || k).join(', ')}`, live: true }
  }
  if (pipeline.supervisor.status === 'active') {
    return { text: 'Supervisor planning…', live: true }
  }
  if (pipeline.done) {
    return { text: pipeline.done.allowed ? 'Investigation complete' : 'Investigation blocked', live: false }
  }
  if (running) return { text: 'Starting…', live: true }
  return { text: 'Idle', live: false }
}

function App() {
  // undefined = still checking for an existing session; null = logged out;
  // {username, role} = logged in. Checked once on mount via a cookie-backed
  // session (see app/server.py's /api/auth/me) so a page refresh doesn't
  // force a fresh login as long as the session is still valid.
  const [user, setUser] = useState(undefined)
  const [view, setView] = useView()

  useEffect(() => {
    fetch(`${API_URL}/api/auth/me`, { credentials: 'include' })
      .then((r) => (r.ok ? r.json() : null))
      .then(setUser)
      .catch(() => setUser(null))
  }, [])

  // A session can expire (or be revoked) while the app is open - the socket
  // reports that via a 4401 close, handled here directly rather than via an
  // extra state+effect roundtrip, dropping back to the login screen instead
  // of sitting in a silent reconnect-fail loop.
  const { connected, messages, pipeline, running, ask, history } = useInvestigation(Boolean(user), () => setUser(null))
  const [roles, setRoles] = useState([])
  const [role, setRole] = useState('plant_engineer')
  const [lastQuestion, setLastQuestion] = useState('')
  const [sidebarOpen, setSidebarOpen] = useState(true)
  // Shown once per login, before the chat view - see components/SummaryPage.jsx.
  // Reset on logout (below) so the next sign-in (same tab, no full reload)
  // lands on it again too, rather than skipping straight to chat.
  const [started, setStarted] = useState(false)
  const [pendingQuestion, setPendingQuestion] = useState('')

  useEffect(() => {
    if (!user) return
    fetch(`${API_URL}/api/roles`, { credentials: 'include' })
      .then((r) => r.json())
      .then((data) => {
        setRoles(data.roles)
        setRole(data.default)
      })
      .catch(() => {})
  }, [user])

  async function handleLogout() {
    try {
      await fetch(`${API_URL}/api/auth/logout`, { method: 'POST', credentials: 'include' })
    } catch {
      // Best-effort - clear local state regardless of network failure.
    }
    setUser(null)
    setView('chat')
    setStarted(false)
    setPendingQuestion('')
  }

  function handleAsk(question, selectedRole) {
    setLastQuestion(question)
    ask(question, selectedRole)
  }

  function handleStart(question) {
    setPendingQuestion(question)
    setStarted(true)
  }

  if (user === undefined) {
    return (
      <div className="app-loading">
        <div className="app-loading-mark">IN</div>
      </div>
    )
  }

  if (user === null) {
    return <LoginPage onLogin={setUser} />
  }

  if (view === 'detail') {
    return <DetailFlowPage history={history} running={running} onBack={() => setView('chat')} />
  }

  if (view === 'audit') {
    return <AuditLogPage user={user} onBack={() => setView('chat')} />
  }

  if (!started) {
    return <SummaryPage user={user} onStart={handleStart} onLogout={handleLogout} />
  }

  const status = workingLabel(pipeline, running)

  return (
    <div className="app-shell">
      <ChatPanel
        messages={messages}
        running={running}
        initialInput={pendingQuestion}
        roles={roles}
        role={role}
        onRoleChange={setRole}
        onAsk={handleAsk}
        connected={connected}
        user={user}
        onLogout={handleLogout}
        onOpenAudit={() => setView('audit')}
      />

      <aside className={`workflow-sidebar ${sidebarOpen ? '' : 'is-collapsed'}`}>
        <button
          type="button"
          className="sidebar-handle"
          onClick={() => setSidebarOpen((v) => !v)}
          aria-expanded={sidebarOpen}
          aria-label={sidebarOpen ? 'Collapse workflow panel' : 'Expand workflow panel'}
        >
          <span className="sidebar-handle-icon">{sidebarOpen ? '›' : '‹'}</span>
          <span className="sidebar-handle-label">Workflow</span>
        </button>

        <div className="sidebar-content">
          <div className="sidebar-head">
            <div className="sidebar-head-row">
              <h2>Workflow</h2>
              <button type="button" className="sidebar-detail-link" onClick={() => setView('detail')}>
                Detail trace ↗
              </button>
            </div>
            <p className={`sidebar-status ${status.live ? 'is-live' : ''}`}>{status.text}</p>
          </div>
          <div className="sidebar-pipeline-scroll">
            <TracePipeline pipeline={pipeline} question={lastQuestion} running={running} compact />
          </div>
          <DetailLog log={pipeline.log} />
        </div>
      </aside>
    </div>
  )
}

export default App
