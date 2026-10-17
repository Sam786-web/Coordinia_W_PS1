import { useCallback, useEffect, useRef, useState } from 'react'
import { api, wsUrl } from '../lib/api'

/**
 * Live backend state: one REST snapshot + a WebSocket that pushes STATE_UPDATED events.
 * Falls back to polling while the socket is down.
 */
export function useLiveState(projectId = null) {
  const [state, setState] = useState(null)
  const [error, setError] = useState(null)
  const [autopilot, setAutopilot] = useState(null)
  const [ws, setWs] = useState({ status: 'connecting', rtt: null, lastEventAt: null })
  const debounce = useRef(null)
  const pingSentAt = useRef(0)

  // Leads follow the active project; members stay on the project they joined
  const refresh = useCallback(async () => {
    try {
      const s = await api.get(projectId ? `/api/state?project_id=${projectId}` : '/api/state')
      setState(s)
      setError(null)
    } catch (e) {
      setError(e.message || 'Backend unreachable')
    }
  }, [projectId])

  const refreshAutopilot = useCallback(async () => {
    try {
      setAutopilot(await api.get('/api/autopilot/status'))
    } catch {
      /* non-critical */
    }
  }, [])

  const scheduleRefresh = useCallback(() => {
    clearTimeout(debounce.current)
    debounce.current = setTimeout(() => {
      refresh()
      refreshAutopilot()
    }, 40)
  }, [refresh, refreshAutopilot])

  useEffect(() => {
    refresh()
    refreshAutopilot()
  }, [refresh, refreshAutopilot])

  // WebSocket with exponential-backoff reconnect + RTT heartbeat
  useEffect(() => {
    let socket
    let retry = 0
    let closed = false
    let reconnectTimer
    let heartbeat

    const connect = () => {
      setWs((w) => ({ ...w, status: retry ? 'reconnecting' : 'connecting' }))
      socket = new WebSocket(wsUrl())
      socket.onopen = () => {
        retry = 0
        setWs((w) => ({ ...w, status: 'live' }))
        scheduleRefresh()
        heartbeat = setInterval(() => {
          if (socket.readyState === WebSocket.OPEN) {
            pingSentAt.current = performance.now()
            socket.send('ping')
          }
        }, 15000)
      }
      socket.onmessage = (e) => {
        let msg
        try {
          msg = JSON.parse(e.data)
        } catch {
          return
        }
        if (msg.event === 'STATE_UPDATED') {
          setWs((w) => ({ ...w, lastEventAt: Date.now() }))
          scheduleRefresh()
        } else if (msg.event === 'PONG' && pingSentAt.current) {
          setWs((w) => ({ ...w, rtt: Math.round(performance.now() - pingSentAt.current) }))
        } else if (msg.event === 'CONNECTED') {
          pingSentAt.current = performance.now()
          socket.send('ping')
        }
      }
      socket.onclose = () => {
        clearInterval(heartbeat)
        if (closed) return
        setWs((w) => ({ ...w, status: 'reconnecting' }))
        retry += 1
        reconnectTimer = setTimeout(connect, Math.min(8000, 500 * 2 ** retry))
      }
      socket.onerror = () => socket.close()
    }
    connect()
    return () => {
      closed = true
      clearTimeout(reconnectTimer)
      clearInterval(heartbeat)
      socket?.close()
    }
  }, [scheduleRefresh])

  // Polling fallback while the socket is down
  useEffect(() => {
    if (ws.status === 'live') return
    const id = setInterval(() => {
      refresh()
      refreshAutopilot()
    }, 8000)
    return () => clearInterval(id)
  }, [ws.status, refresh, refreshAutopilot])

  return { state, error, refresh, autopilot, refreshAutopilot, ws }
}
