import { useCallback, useEffect, useRef, useState } from 'react'
import { applyEvent, initialPipeline } from '../lib/pipelineReducer'

// Must match the host the page is served from - see the note in App.jsx.
const WS_URL = import.meta.env.VITE_WS_URL || 'ws://localhost:8787/ws/investigate'

function uid() {
  return Math.random().toString(36).slice(2) + Date.now().toString(36)
}

function guardrailReasons(decisions) {
  return decisions
    .filter((d) => !d.allowed)
    .map((d) => d.reason)
    .join(' ')
}

function summarizeDone(event, pipeline) {
  if (!event.allowed) {
    if (event.stage_blocked === 'input') {
      return `Blocked by input guardrails: ${guardrailReasons(pipeline.inputGuardrail.decisions) || 'request denied.'}`
    }
    if (event.stage_blocked === 'rbac') {
      const reason = pipeline.rbac.decision?.reason || 'this role is not authorized for the requested domain(s).'
      return `Blocked by RBAC: ${reason}`
    }
    return 'The investigation could not be completed.'
  }
  const report = event.report || {}
  const factors = report.contributing_factors?.length
    ? `\n\nContributing factors:\n${report.contributing_factors.map((f) => `- ${f}`).join('\n')}`
    : ''
  const confidence = typeof report.confidence === 'number' ? `\n\nConfidence: ${(report.confidence * 100).toFixed(0)}%` : ''
  return `${report.root_cause || 'Investigation complete.'}${factors}${confidence}`
}

// The WebSocket handshake carries the httpOnly session cookie automatically
// (same-site, see app/server.py) - the server rejects it outright (close
// code 4401) if there's no valid session, so `enabled` here just controls
// whether this hook tries to connect at all. Pass false while logged out so
// the app doesn't sit there retrying a connection the server will keep
// refusing, and so a fresh login always starts a fresh socket rather than
// silently reusing one opened under a previous session.
export function useInvestigation(enabled, onUnauthorized) {
  const [connected, setConnected] = useState(false)
  const [messages, setMessages] = useState([])
  const [pipeline, setPipeline] = useState(initialPipeline())
  const [running, setRunning] = useState(false)
  // One entry per question asked this session, each holding its own
  // pipeline snapshot - the Detail Flow page browses this list. pipeline
  // (above) always mirrors the entry currently in flight.
  const [history, setHistory] = useState([])
  const wsRef = useRef(null)
  const pipelineRef = useRef(pipeline)
  pipelineRef.current = pipeline
  const currentIdRef = useRef(null)
  // Always call the latest onUnauthorized without re-running the connect
  // effect every time the caller passes a fresh arrow function.
  const onUnauthorizedRef = useRef(onUnauthorized)
  onUnauthorizedRef.current = onUnauthorized

  useEffect(() => {
    if (!enabled) {
      setConnected(false)
      setMessages([])
      setPipeline(initialPipeline())
      setHistory([])
      setRunning(false)
      currentIdRef.current = null
      return
    }

    let cancelled = false
    let socket

    function connect() {
      if (cancelled) return
      socket = new WebSocket(WS_URL)
      wsRef.current = socket

      socket.onopen = () => setConnected(true)
      socket.onclose = (event) => {
        setConnected(false)
        if (event.code === 4401) {
          onUnauthorizedRef.current?.()
          return
        }
        if (!cancelled) setTimeout(connect, 2000)
      }
      socket.onerror = () => socket.close()
      socket.onmessage = (event) => {
        const data = JSON.parse(event.data)
        handleEvent(data)
      }
    }

    connect()
    return () => {
      cancelled = true
      socket?.close()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled])

  const handleEvent = useCallback((event) => {
    if (event.type === 'error') {
      setMessages((prev) => [...prev, { id: uid(), role: 'system', text: `Error: ${event.message}` }])
      return
    }

    const next = applyEvent(pipelineRef.current, event)
    setPipeline(next)
    const currentId = currentIdRef.current
    if (currentId) {
      setHistory((prev) => prev.map((entry) => (entry.id === currentId ? { ...entry, pipeline: next } : entry)))
    }

    if (event.type === 'done') {
      setRunning(false)
      setMessages((prev) => {
        const list = [...prev]
        const idx = list.map((m) => m.pending).lastIndexOf(true)
        const text = summarizeDone(event, next)
        if (idx !== -1) {
          list[idx] = { ...list[idx], text, pending: false, allowed: event.allowed }
        }
        return list
      })
    }
  }, [])

  const ask = useCallback((question, role) => {
    const socket = wsRef.current
    if (!socket || socket.readyState !== WebSocket.OPEN) return
    const id = uid()
    currentIdRef.current = id
    const fresh = initialPipeline()
    setPipeline(fresh)
    setHistory((prev) => [...prev, { id, question, role, startedAt: new Date(), pipeline: fresh }])
    setRunning(true)
    setMessages((prev) => [
      ...prev,
      { id: uid(), role: 'user', text: question },
      { id: uid(), role: 'assistant', text: '', pending: true },
    ])
    socket.send(JSON.stringify({ question, role }))
  }, [])

  return { connected, messages, pipeline, running, ask, history }
}
