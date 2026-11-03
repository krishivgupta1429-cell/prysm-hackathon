import { useState, useEffect, useRef, useCallback } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { apiUrl } from '@/lib/api'
import { getToken, fetchUser } from '@/lib/auth'
import ReactMarkdown from 'react-markdown'

interface Message {
  role: 'user' | 'assistant'
  content: string
}

interface AnalysisData {
  overallScore: number
  scoreLabel: string
  summary: string
  topPriorities: string[]
  parameters: Array<{
    name: string
    score: number
    weight: number
    whatIsHappening: string
    whyItMatters: string
    whatToChange: string
    howToDoIt: string
    whatToExpect: string
  }>
  niche: string
  platform: string
  analysis_id?: string
}

interface ChatContext {
  parameterName: string
  parameterScore: number
  whatIsHappening: string
  whatToChange: string
  howToDoIt: string
  autoQuestion: string
}

interface SessionSummary {
  session_id: string
  title: string
  session_type: 'analysis' | 'general'
  analysis_id: string | null
  updated_at: string
}

function formatDate(iso: string) {
  const d = new Date(iso)
  const now = new Date()
  const diffMs = now.getTime() - d.getTime()
  const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24))
  if (diffDays === 0) return 'Today'
  if (diffDays === 1) return 'Yesterday'
  if (diffDays < 7) return `${diffDays}d ago`
  return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' })
}

export default function Chat() {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [thinking, setThinking] = useState(false)
  const [typing, setTyping] = useState(false)
  const [suggestions, setSuggestions] = useState<string[]>([])
  const [analysis, setAnalysis] = useState<AnalysisData | null>(null)
  const [chatContext, setChatContext] = useState<ChatContext | null>(null)
  const [autoSent, setAutoSent] = useState(false)
  const [visibleContent, setVisibleContent] = useState('')
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [menuOpenId, setMenuOpenId] = useState<string | null>(null)
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [renameValue, setRenameValue] = useState('')
  const [searchParams] = useSearchParams()
  const sessionType = searchParams.get('type') === 'general' ? 'general' : 'analysis'
  const [userCredits, setUserCredits] = useState<number>(999)

  const messagesEndRef = useRef<HTMLDivElement>(null)
  const messagesScrollRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const fullContentRef = useRef('')
  const typingIntervalRef = useRef<number>(0)
  const visibleIndexRef = useRef(0)
  const streamDoneRef = useRef(false)
  const navigate = useNavigate()

  useEffect(() => {
    fetchUser().then(u => { if (u) setUserCredits(u.credits) })
  }, [])

  useEffect(() => {
    if (!menuOpenId) return
    const close = () => setMenuOpenId(null)
    document.addEventListener('click', close)
    return () => document.removeEventListener('click', close)
  }, [menuOpenId])

  // Lock body scroll when sidebar is open on mobile
  useEffect(() => {
    if (sidebarOpen) {
      document.body.style.overflow = 'hidden'
    } else {
      document.body.style.overflow = ''
    }
    return () => { document.body.style.overflow = '' }
  }, [sidebarOpen])

  // Ensure mouse wheel scrolls the messages container directly
  useEffect(() => {
    const el = messagesScrollRef.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      e.stopPropagation()
      el.scrollTop += e.deltaY
    }
    el.addEventListener('wheel', onWheel, { passive: true })
    return () => el.removeEventListener('wheel', onWheel)
  }, [])

  useEffect(() => {
    const stored = localStorage.getItem('prysm_result')
    if (stored) {
      try { setAnalysis(JSON.parse(stored)) } catch {}
    }
    const ctx = localStorage.getItem('prysm_chat_context')
    if (ctx) {
      try {
        setChatContext(JSON.parse(ctx))
        localStorage.removeItem('prysm_chat_context')
      } catch {}
    }
    fetchSessions()
  }, [])

  async function deleteSession(sid: string) {
    try {
      const token = getToken()
      await fetch(apiUrl(`/prysm/chat-sessions/${sid}`), {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      })
      setSessions(prev => prev.filter(s => s.session_id !== sid))
      if (sessionId === sid) {
        setSessionId(null)
        setMessages([])
      }
    } catch {}
    setMenuOpenId(null)
  }

  async function renameSession(sid: string, title: string) {
    try {
      const token = getToken()
      await fetch(apiUrl(`/prysm/chat-sessions/${sid}`), {
        method: 'PATCH',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ title }),
      })
      setSessions(prev => prev.map(s => s.session_id === sid ? { ...s, title } : s))
    } catch {}
    setRenamingId(null)
    setMenuOpenId(null)
  }

  async function fetchSessions() {
    try {
      const token = getToken()
      if (!token) return
      const res = await fetch(apiUrl('/prysm/chat-sessions'), {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (res.ok) setSessions(await res.json())
    } catch {}
  }

  // Load existing analysis session from DB on mount
  useEffect(() => {
    if (sessionType === 'general') return
    if (!analysis?.analysis_id) return
    loadExistingSession(analysis.analysis_id)
  }, [analysis?.analysis_id, sessionType])

  async function loadExistingSession(analysisId: string) {
    try {
      const token = getToken()
      if (!token) return
      const res = await fetch(apiUrl('/prysm/chat-sessions'), {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) return
      const all: SessionSummary[] = await res.json()
      const existing = all.find(s => s.session_type === 'analysis' && s.analysis_id === analysisId)
      if (existing) {
        setSessionId(existing.session_id)
        await loadSessionMessages(existing.session_id)
      }
    } catch {}
  }

  async function loadSessionMessages(sid: string) {
    try {
      const token = getToken()
      if (!token) return
      const res = await fetch(apiUrl(`/prysm/chat-sessions/${sid}`), {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (res.ok) {
        const data = await res.json()
        if (data.messages?.length > 0) setMessages(data.messages)
      }
    } catch {}
  }

  async function openSession(s: SessionSummary) {
    // If it's an analysis session, we need to load analysis data first
    if (s.session_type === 'analysis' && s.analysis_id) {
      try {
        const token = getToken()
        const res = await fetch(apiUrl(`/prysm/dashboard/${s.analysis_id}`), {
          headers: { Authorization: `Bearer ${token}` },
        })
        if (res.ok) {
          const result = await res.json()
          localStorage.setItem('prysm_result', JSON.stringify(result))
          setAnalysis(result)
        }
      } catch {}
    } else {
      // General session — clear analysis context
      localStorage.removeItem('prysm_result')
      localStorage.removeItem('prysm_chat_context')
      setAnalysis(null)
      setChatContext(null)
    }
    stopTypewriter()
    setSessionId(s.session_id)
    setMessages([])
    setSuggestions([])
    await loadSessionMessages(s.session_id)
  }

  function startNewChat() {
    stopTypewriter()
    setMessages([])
    setSessionId(null)
    setSuggestions([])
    setInput('')
    setChatContext(null)
    // Keep analysis context if we're in analysis mode
    if (sessionType !== 'analysis') {
      setAnalysis(null)
      localStorage.removeItem('prysm_result')
    }
    if (analysis) fetchSuggestions(analysis)
  }

  useEffect(() => {
    if (chatContext && !autoSent && analysis) {
      setAutoSent(true)
      sendMessage(chatContext.autoQuestion)
    }
  }, [chatContext, analysis, autoSent])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, visibleContent])

  useEffect(() => {
    if (!chatContext && analysis && messages.length === 0) {
      fetchSuggestions(analysis)
    }
  }, [analysis, chatContext, messages.length])

  function startTypewriter() {
    if (typingIntervalRef.current) clearInterval(typingIntervalRef.current)
    visibleIndexRef.current = 0
    setVisibleContent('')
    setTyping(true)

    typingIntervalRef.current = window.setInterval(() => {
      const full = fullContentRef.current
      const currentIndex = visibleIndexRef.current
      if (currentIndex < full.length) {
        const charsToAdd = full[currentIndex] === '\n' ? 1 : Math.min(3, full.length - currentIndex)
        visibleIndexRef.current += charsToAdd
        setVisibleContent(full.slice(0, visibleIndexRef.current))
      } else if (streamDoneRef.current) {
        clearInterval(typingIntervalRef.current)
        typingIntervalRef.current = 0
        setVisibleContent(full)
        setTyping(false)
        setMessages(prev => {
          const updated = [...prev]
          if (updated.length && updated[updated.length - 1].role === 'assistant') {
            updated[updated.length - 1] = { role: 'assistant', content: full }
          }
          return updated
        })
        // Refresh sessions list to update title/timestamp
        fetchSessions()
      }
    }, 18)
  }

  function stopTypewriter() {
    if (typingIntervalRef.current) {
      clearInterval(typingIntervalRef.current)
      typingIntervalRef.current = 0
    }
    setTyping(false)
  }

  async function fetchSuggestions(analysisData: any) {
    try {
      const headers: Record<string, string> = { 'Content-Type': 'application/json' }
      const tk = getToken()
      if (tk) headers['Authorization'] = `Bearer ${tk}`
      const res = await fetch(apiUrl('/prysm/chat'), {
        method: 'POST',
        headers,
        body: JSON.stringify({ messages: [], analysisData }),
      })
      const data = await res.json()
      if (data.suggestions) setSuggestions(data.suggestions)
    } catch {
      setSuggestions([
        'How do I create a viral hook?',
        'What makes a Reel go viral?',
        'How should I structure my content?',
      ])
    }
  }

  const sendMessage = useCallback(async (content: string) => {
    if (!content.trim() || loading) return

    const userMessage: Message = { role: 'user', content: content.trim() }
    const newMessages = [...messages, userMessage]
    setMessages(newMessages)
    setInput('')
    setLoading(true)
    setThinking(true)
    setSuggestions([])
    fullContentRef.current = ''
    streamDoneRef.current = false
    visibleIndexRef.current = 0
    setVisibleContent('')
    stopTypewriter()

    try {
      const chatHeaders: Record<string, string> = { 'Content-Type': 'application/json' }
      const chatToken = getToken()
      if (chatToken) chatHeaders['Authorization'] = `Bearer ${chatToken}`

      const res = await fetch(apiUrl('/prysm/chat'), {
        method: 'POST',
        headers: chatHeaders,
        body: JSON.stringify({
          messages: newMessages.map(m => ({ role: m.role, content: m.content })),
          analysisData: analysis,
          sessionId: sessionId,
          sessionType,
          analysisId: analysis?.analysis_id ?? null,
        }),
      })

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}))
        if (res.status === 402 || errData.detail === 'out_of_credits') {
          throw new Error('__out_of_credits__')
        }
        throw new Error(errData.detail || `Server error: ${res.status}`)
      }

      const reader = res.body?.getReader()
      const decoder = new TextDecoder()
      let firstChunk = true

      setMessages([...newMessages, { role: 'assistant', content: '' }])

      if (reader) {
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          const chunk = decoder.decode(value, { stream: true })
          const lines = chunk.split('\n')
          for (const line of lines) {
            if (line.startsWith('data: ')) {
              const data = line.slice(6)
              if (data === '[DONE]') continue
              try {
                const parsed = JSON.parse(data)
                if (parsed.error) throw new Error(parsed.error)
                if (parsed.session_id && !sessionId) setSessionId(parsed.session_id)
                if (parsed.delta) {
                  if (firstChunk) {
                    firstChunk = false
                    setThinking(false)
                    startTypewriter()
                  }
                  fullContentRef.current += parsed.delta
                }
              } catch (parseErr: any) {
                if (parseErr?.message && !parseErr.message.startsWith('JSON')) throw parseErr
              }
            }
          }
        }
      }

      streamDoneRef.current = true
      // Refresh credit balance after each message
      fetchUser().then(u => { if (u) setUserCredits(u.credits) })

      // If stream completed but no content was received, show error
      if (!fullContentRef.current) {
        stopTypewriter()
        setThinking(false)
        setMessages(prev => {
          const updated = [...prev]
          if (updated.length > 0 && updated[updated.length - 1].role === 'assistant') {
            updated[updated.length - 1] = { role: 'assistant', content: 'No response received. Please try again.' }
          }
          return updated
        })
      }

    } catch (err: any) {
      stopTypewriter()
      setThinking(false)
      const isOutOfCredits = err?.message === '__out_of_credits__'
      const errMsg = isOutOfCredits
        ? "You've used up your credits — I can't respond until you top up.\n\n[Upgrade your plan →](https://theprysm.io/pricing)"
        : err?.message || 'Sorry, something went wrong. Please try again.'
      setMessages(prev => {
        const updated = [...prev]
        if (updated.length > 0 && updated[updated.length - 1].role === 'assistant') {
          updated[updated.length - 1] = { role: 'assistant', content: errMsg }
        } else {
          updated.push({ role: 'assistant', content: errMsg })
        }
        return updated
      })
    } finally {
      setLoading(false)
    }
  }, [messages, loading, analysis, sessionId])

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage(input)
    }
  }

  const hasAnalysis = !!analysis?.parameters
  const weakest = hasAnalysis
    ? [...analysis!.parameters].sort((a, b) => a.score - b.score)[0]
    : null

  function renderMarkdown(content: string) {
    return (
      <ReactMarkdown
        components={{
          h2: ({ children }) => <h2 className="font-display font-bold text-foreground/70 text-base mt-4 mb-2">{children}</h2>,
          h3: ({ children }) => <h3 className="font-display font-semibold text-foreground/65 text-sm mt-3 mb-1.5">{children}</h3>,
          strong: ({ children }) => <strong className="font-semibold text-foreground/70">{children}</strong>,
          p: ({ children }) => <p className="mb-2.5 leading-relaxed">{children}</p>,
          ul: ({ children }) => <ul className="mb-3 ml-1 flex flex-col gap-1.5">{children}</ul>,
          ol: ({ children }) => <ol className="mb-3 ml-1 flex flex-col gap-1.5 list-decimal list-inside">{children}</ol>,
          li: ({ children }) => <li className="text-foreground/55 leading-relaxed flex gap-2"><span className="text-foreground/25 mt-0.5 shrink-0">•</span><span>{children}</span></li>,
          hr: () => <hr className="border-border my-3" />,
          code: ({ children }) => <code className="bg-secondary/60 px-1.5 py-0.5 rounded text-xs font-mono text-foreground/60">{children}</code>,
        }}
      >
        {content}
      </ReactMarkdown>
    )
  }

  return (
    <div className="h-screen bg-background text-foreground flex flex-col overflow-hidden">
      {/* Nav */}
      <nav className="border-b border-border px-4 sm:px-6 h-14 flex items-center justify-between shrink-0 z-20">
        <div className="flex items-center gap-3">
          <button
            onClick={() => setSidebarOpen(v => !v)}
            className="w-8 h-8 flex items-center justify-center rounded-lg text-foreground/30 hover:text-foreground/60 hover:bg-foreground/5 transition-all cursor-pointer"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 6.75h16.5M3.75 12h16.5M3.75 17.25h16.5" />
            </svg>
          </button>
          <a href="/" className="font-logo text-[18px] text-foreground">Prysm</a>
        </div>
        <div className="flex gap-3 items-center">
          {hasAnalysis && (
            <a href="/results" className="text-sm text-foreground/30 hover:text-foreground/50 transition-colors">← Results</a>
          )}
          <a href="/analyse" className="text-sm text-foreground/40 hover:text-foreground/60 transition-colors hidden sm:block">New Analysis →</a>
        </div>
      </nav>

      <div className="flex flex-1 overflow-hidden relative">
        {/* ── Mobile overlay backdrop — full screen, blocks scroll ─────────── */}
        {sidebarOpen && (
          <div
            className="fixed inset-0 z-30 sm:hidden"
            style={{ background: 'rgba(0,0,0,0.45)' }}
            onClick={() => setSidebarOpen(false)}
          />
        )}

        {/* ── Sidebar ─────────────────────────────────────────────────────── */}
        {sidebarOpen && (
          <aside className="
            fixed inset-y-0 left-0 z-40 flex flex-col
            sm:relative sm:inset-auto sm:w-[240px] sm:z-auto
            border-r border-border bg-background overflow-hidden
          " style={{ width: 'min(85vw, 320px)' }}>
            {/* New Chat button */}
            <div className="p-3 border-b border-border">
              <button
                onClick={() => { startNewChat(); setSidebarOpen(false) }}
                className="w-full flex items-center gap-2 px-3 py-2.5 rounded-xl border border-border text-foreground/50 text-[13px] font-body hover:text-foreground/70 hover:bg-foreground/[0.03] transition-all cursor-pointer"
              >
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
                </svg>
                New chat
              </button>
            </div>

            {/* Session list */}
            <div className="flex-1 overflow-y-auto py-2">
              {sessions.length === 0 ? (
                <p className="text-[11px] text-foreground/20 font-body text-center mt-6 px-4">No chats yet</p>
              ) : (
                <div className="flex flex-col gap-0.5 px-2">
                  {sessions.map(s => (
                    <div
                      key={s.session_id}
                      className={`relative rounded-xl transition-all group ${
                        sessionId === s.session_id
                          ? 'bg-foreground/[0.06]'
                          : 'hover:bg-foreground/[0.03]'
                      }`}
                    >
                      {/* Rename input */}
                      {renamingId === s.session_id ? (
                        <div className="px-3 py-2">
                          <input
                            autoFocus
                            value={renameValue}
                            onChange={e => setRenameValue(e.target.value)}
                            onKeyDown={e => {
                              if (e.key === 'Enter') renameSession(s.session_id, renameValue)
                              if (e.key === 'Escape') { setRenamingId(null); setMenuOpenId(null) }
                            }}
                            onBlur={() => { if (renameValue.trim()) renameSession(s.session_id, renameValue); else setRenamingId(null) }}
                            className="w-full bg-background border border-foreground/20 rounded-lg px-2 py-1 text-[12px] text-foreground outline-none font-body"
                          />
                        </div>
                      ) : (
                        <button
                          onClick={() => { openSession(s); setMenuOpenId(null); setSidebarOpen(false) }}
                          className={`w-full text-left px-3 py-2.5 cursor-pointer ${
                            sessionId === s.session_id ? 'text-foreground/70' : 'text-foreground/40 group-hover:text-foreground/60'
                          }`}
                        >
                          <div className="flex items-start gap-2 pr-5">
                            <div className="shrink-0 mt-0.5">
                              {s.session_type === 'analysis' ? (
                                <svg className="w-3 h-3 text-foreground/25" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                                  <path strokeLinecap="round" strokeLinejoin="round" d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 013 19.875v-6.75zm9.75-5.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v12.375c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V7.5z" />
                                </svg>
                              ) : (
                                <svg className="w-3 h-3 text-foreground/25" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                                  <path strokeLinecap="round" strokeLinejoin="round" d="M8.625 12a.375.375 0 11-.75 0 .375.375 0 01.75 0zm0 0H8.25m4.125 0a.375.375 0 11-.75 0 .375.375 0 01.75 0zm0 0H12m4.125 0a.375.375 0 11-.75 0 .375.375 0 01.75 0zm0 0h-.375M21 12c0 4.556-4.03 8.25-9 8.25a9.764 9.764 0 01-2.555-.337A5.972 5.972 0 015.41 20.97a5.969 5.969 0 01-.474-.065 4.48 4.48 0 00.978-2.025c.09-.457-.133-.901-.467-1.226C3.93 16.178 3 14.189 3 12c0-4.556 4.03-8.25 9-8.25s9 3.694 9 8.25z" />
                                </svg>
                              )}
                            </div>
                            <div className="flex-1 min-w-0">
                              <p className="text-[12px] font-body leading-snug line-clamp-2 break-words">{s.title}</p>
                              <p className="text-[10px] text-foreground/20 font-body mt-0.5">{formatDate(s.updated_at)}</p>
                            </div>
                          </div>
                        </button>
                      )}

                      {/* Three-dot menu button */}
                      {renamingId !== s.session_id && (
                        <button
                          onClick={e => { e.stopPropagation(); setMenuOpenId(menuOpenId === s.session_id ? null : s.session_id) }}
                          className="absolute right-2 top-1/2 -translate-y-1/2 w-5 h-5 flex items-center justify-center rounded-md text-foreground/20 hover:text-foreground/50 hover:bg-foreground/[0.06] transition-all opacity-0 group-hover:opacity-100 cursor-pointer"
                        >
                          <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 16 16">
                            <circle cx="8" cy="3" r="1.2" /><circle cx="8" cy="8" r="1.2" /><circle cx="8" cy="13" r="1.2" />
                          </svg>
                        </button>
                      )}

                      {/* Dropdown menu */}
                      {menuOpenId === s.session_id && (
                        <div
                          className="absolute right-2 top-8 z-50 bg-background border border-border rounded-xl shadow-lg overflow-hidden min-w-[140px]"
                          onClick={e => e.stopPropagation()}
                        >
                          <button
                            onClick={() => { setRenamingId(s.session_id); setRenameValue(s.title); setMenuOpenId(null) }}
                            className="flex items-center gap-2.5 w-full px-3 py-2.5 text-[12px] text-foreground/60 hover:bg-foreground/[0.04] transition-colors cursor-pointer font-body"
                          >
                            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                              <path strokeLinecap="round" strokeLinejoin="round" d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L10.582 16.07a4.5 4.5 0 01-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 011.13-1.897l8.932-8.931zm0 0L19.5 7.125" />
                            </svg>
                            Rename
                          </button>
                          <div className="border-t border-border/50" />
                          <button
                            onClick={() => deleteSession(s.session_id)}
                            className="flex items-center gap-2.5 w-full px-3 py-2.5 text-[12px] text-red-500/70 hover:bg-red-50/30 transition-colors cursor-pointer font-body"
                          >
                            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                              <path strokeLinecap="round" strokeLinejoin="round" d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 01-2.244 2.077H8.084a2.25 2.25 0 01-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 00-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 013.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 00-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 00-7.5 0" />
                            </svg>
                            Delete
                          </button>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Back to dashboard */}
            <div className="p-3 border-t border-border">
              <a
                href="/dashboard"
                className="flex items-center gap-2 px-3 py-2 rounded-lg text-foreground/30 hover:text-foreground/50 text-[12px] font-body transition-colors"
              >
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 12l8.954-8.955c.44-.439 1.152-.439 1.591 0L21.75 12M4.5 9.75v10.125c0 .621.504 1.125 1.125 1.125H9.75v-4.875c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125V21h4.125c.621 0 1.125-.504 1.125-1.125V9.75M8.25 21h8.25" />
                </svg>
                Dashboard
              </a>
            </div>
          </aside>
        )}

        {/* ── Main chat area ───────────────────────────────────────────────── */}
        <div className="flex-1 flex flex-col overflow-hidden">
          {/* Parameter Context Banner */}
          {chatContext && (
            <div className="border-b border-border/50 px-6 py-3 bg-secondary/20 shrink-0">
              <div className="max-w-[680px] mx-auto flex items-center gap-3">
                <span className="px-2.5 py-1 rounded-md bg-secondary border border-border text-[12px] font-semibold text-foreground/50 whitespace-nowrap">
                  {chatContext.parameterName} — {chatContext.parameterScore}/100
                </span>
                <p className="text-xs text-foreground/30 truncate">{chatContext.whatToChange}</p>
              </div>
            </div>
          )}

          {/* Messages */}
          <div ref={messagesScrollRef} className="flex-1 overflow-y-auto px-4 sm:px-6" style={{ overscrollBehavior: 'contain', WebkitOverflowScrolling: 'touch' } as React.CSSProperties}>
            <div className="max-w-[680px] mx-auto py-6 sm:py-8">
              {messages.length === 0 && !chatContext && (
                <div className="pt-10 pb-8">
                  <div className="w-12 h-12 rounded-xl bg-foreground/5 border border-border flex items-center justify-center mb-5 text-xl text-foreground/30">
                    ◆
                  </div>
                  <h1 className="font-display text-xl sm:text-2xl font-bold tracking-tight mb-2">
                    {hasAnalysis ? `Let's improve your ${analysis!.niche || ''} Reel` : 'Ask Prysm AI anything'}
                  </h1>
                  <p className="text-foreground/40 text-sm leading-relaxed mb-3 max-w-md font-body">
                    {hasAnalysis
                      ? `Your video scored ${analysis!.overallScore}/100. I've studied every frame and I'm ready to help you fix what's holding it back.`
                      : `I'm your AI content strategist for Instagram Reels. Ask me about hooks, pacing, trends, or anything about growing on Instagram.`}
                  </p>

                  {hasAnalysis && weakest && (
                    <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg bg-secondary/50 border border-border text-xs text-foreground/40 mb-8">
                      <span className="text-foreground/25">Weakest area:</span>
                      <span className="text-foreground/50 font-medium">{weakest.name} — {weakest.score}/100</span>
                    </div>
                  )}

                  {suggestions.length > 0 && (
                    <div className="flex flex-col gap-2 mt-2">
                      {suggestions.map((s, i) => (
                        <button key={i} onClick={() => sendMessage(s)}
                          className="text-left px-4 py-3.5 rounded-xl bg-secondary/30 border border-border text-foreground/60 text-sm leading-relaxed cursor-pointer hover:bg-secondary/60 hover:border-foreground/15 transition-all font-body">
                          {s}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {messages.map((msg, i) => {
                const isLastAssistant = msg.role === 'assistant' && i === messages.length - 1
                const contentToRender = (isLastAssistant && typing) ? visibleContent : msg.content

                return (
                  <div key={i} className={`mb-6 flex flex-col ${msg.role === 'user' ? 'items-end' : 'items-start'}`}>
                    <div className="text-[11px] font-semibold tracking-widest uppercase text-foreground/25 mb-1.5 px-0.5">
                      {msg.role === 'user' ? 'You' : 'Prysm AI'}
                    </div>
                    {msg.role === 'user' ? (
                      <div className="max-w-[85%] px-4 py-3 rounded-2xl rounded-br-sm bg-foreground/8 text-foreground/70 text-sm leading-relaxed break-words font-body">
                        {msg.content}
                      </div>
                    ) : (
                      <div className="w-full text-foreground/55 text-sm leading-relaxed font-body">
                        {isLastAssistant && thinking && (
                          <div className="flex items-center gap-3 py-2">
                            <div className="flex gap-1">
                              {[0, 1, 2].map(j => (
                                <div key={j} className="w-2 h-2 rounded-full bg-foreground/25"
                                  style={{ animation: `typing-dot 1.4s ease-in-out ${j * 0.2}s infinite` }} />
                              ))}
                            </div>
                            <span className="text-xs text-foreground/20 font-body">Prysm is thinking...</span>
                          </div>
                        )}
                        {contentToRender && !thinking && (
                          <>
                            {renderMarkdown(contentToRender)}
                            {typing && (
                              <span className="inline-block w-[2px] h-[18px] bg-foreground/50 ml-[2px] align-text-bottom"
                                style={{ animation: 'cursor-blink 0.8s step-end infinite' }} />
                            )}
                          </>
                        )}
                      </div>
                    )}
                  </div>
                )
              })}

              <div ref={messagesEndRef} />
            </div>
          </div>

          {/* Input Area — hidden on mobile when sidebar is open */}
          <div className={`border-t border-border px-3 sm:px-6 py-2 sm:py-4 shrink-0 bg-background ${sidebarOpen ? 'hidden sm:block' : ''}`} style={{ paddingBottom: 'max(8px, env(safe-area-inset-bottom))' }}>
            <div className="max-w-[680px] mx-auto">
              {userCredits <= 0 && (
                <div className="mb-3 flex items-center justify-between gap-3 px-4 py-2.5 rounded-xl bg-foreground/[0.04] border border-foreground/10">
                  <p className="text-xs text-foreground/50 font-body">You're out of credits — top up to keep chatting with Prysm AI.</p>
                  <a href="/pricing" className="text-xs font-body font-semibold text-foreground/80 hover:text-foreground whitespace-nowrap transition-colors">Upgrade →</a>
                </div>
              )}
              <div className="flex items-end gap-3 bg-secondary/30 border border-border rounded-2xl pl-4 pr-1 py-1 focus-within:border-foreground/20 transition-colors">
                <textarea
                  ref={inputRef}
                  value={input}
                  onChange={e => {
                    setInput(e.target.value)
                    e.target.style.height = 'auto'
                    e.target.style.height = Math.min(e.target.scrollHeight, 120) + 'px'
                  }}
                  onKeyDown={handleKeyDown}
                  placeholder={userCredits <= 0 ? 'Upgrade to continue chatting...' : chatContext ? `Ask more about ${chatContext.parameterName.toLowerCase()}...` : hasAnalysis ? 'Ask about your video analysis...' : 'Ask me anything about your content...'}
                  rows={1}
                  disabled={userCredits <= 0}
                  className="flex-1 bg-transparent border-none outline-none text-foreground text-sm leading-relaxed resize-none py-2.5 placeholder:text-foreground/25 font-body max-h-[120px] disabled:cursor-not-allowed"
                />
                <button
                  onClick={() => sendMessage(input)}
                  disabled={!input.trim() || loading || userCredits <= 0}
                  className={`w-9 h-9 rounded-xl flex items-center justify-center shrink-0 transition-all mb-0.5 ${
                    input.trim() && !loading && userCredits > 0
                      ? 'bg-foreground cursor-pointer hover:opacity-90'
                      : 'bg-foreground/8 cursor-not-allowed'
                  }`}
                >
                  <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                    <path d="M3 13L13 8L3 3V7L9 8L3 9V13Z"
                      fill={input.trim() && !loading ? 'hsl(var(--background))' : 'currentColor'}
                      className={input.trim() && !loading ? '' : 'text-foreground/25'}
                    />
                  </svg>
                </button>
              </div>
              <p className="text-center text-[11px] text-foreground/20 mt-2 font-body hidden sm:block">
                Prysm AI uses your analysis data to give personalised advice
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
