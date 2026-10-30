import { useEffect, useRef, useState } from 'react'
import { io } from 'socket.io-client'
import { ApiError, apiBase, request, requestAccount, type Account, type Box, type RoomState } from './api'

export const users = [
  { id: 'user-1', name: 'Alice', token: 'demo-alice', color: 'peach' },
  { id: 'user-2', name: 'Bob', token: 'demo-bob', color: 'blue' },
  { id: 'user-3', name: 'Charlie', token: 'demo-charlie', color: 'yellow' },
  { id: 'user-4', name: 'Dana', token: 'demo-dana', color: 'purple' },
]
const tokenKey = 'lucky-rooms-demo-token'
const roomKey = 'lucky-rooms-room'
type Busy = null | 'purchase' | 'create' | 'join' | 'ready' | 'start' | 'choice'
type Connection = 'connecting' | 'connected' | 'reconnecting' | 'offline'

function storedSession() {
  try {
    const token = sessionStorage.getItem(tokenKey)
    const selectedToken = users.some(user => user.token === token) ? token! : users[0].token
    const saved = JSON.parse(sessionStorage.getItem(roomKey) || 'null')
    return { token: selectedToken, code: saved?.token === selectedToken && /^[A-Z0-9]{6}$/.test(saved.code) ? saved.code as string : null }
  } catch {
    return { token: users[0].token, code: null }
  }
}

function saveSession(token: string, code: string | null) {
  try {
    sessionStorage.setItem(tokenKey, token)
    if (code) sessionStorage.setItem(roomKey, JSON.stringify({ token, code }))
    else sessionStorage.removeItem(roomKey)
  } catch { /* Private browsing may disable storage; the current game still works. */ }
}

function errorText(error: unknown) {
  return error instanceof Error ? error.message : 'Something went wrong. Please try again.'
}

export function useLuckyRooms() {
  const [initial] = useState(storedSession)
  const [token, setToken] = useState(initial.token)
  const [roomCode, setRoomCode] = useState<string | null>(initial.code)
  const [room, setRoom] = useState<RoomState | null>(null)
  const [account, setAccount] = useState<Account | null>(null)
  const [accountLoading, setAccountLoading] = useState(true)
  const [balanceUncertain, setBalanceUncertain] = useState(false)
  const [busy, setBusy] = useState<Busy>(null)
  const [pendingBox, setPendingBox] = useState<Box | null>(null)
  const [connection, setConnection] = useState<Connection>('connecting')
  const [connectionError, setConnectionError] = useState('')
  const [restoring, setRestoring] = useState(Boolean(initial.code))
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [joinCode, setJoinCode] = useState('')
  const [secondsLeft, setSecondsLeft] = useState(0)
  const selected = users.find(user => user.token === token)!

  // Identity and room epochs invalidate every outstanding REST callback immediately.
  const identity = useRef(0)
  const roomEpoch = useRef(0)
  const accountSequence = useRef(0)
  const roomRef = useRef<RoomState | null>(null)
  const codeRef = useRef(initial.code)
  const mutation = useRef<symbol | null>(null)
  const roomRequest = useRef<{ lock: symbol; settled: (snapshot: RoomState) => boolean } | null>(null)
  const resync = useRef<() => void>(() => {})
  const disconnect = useRef<() => void>(() => {})
  const serverClock = useRef<{ time: number; received: number } | null>(null)

  async function loadAccount(currentToken = token, currentIdentity = identity.current, quiet = false) {
    const sequence = ++accountSequence.current
    setAccountLoading(true)
    if (!quiet) { setError(''); setMessage('') }
    try {
      const data = await requestAccount('/me', currentToken)
      if (identity.current === currentIdentity && sequence === accountSequence.current) {
        setAccount(data)
        setBalanceUncertain(false)
      }
    } catch (failure) {
      if (identity.current === currentIdentity && sequence === accountSequence.current) {
        setBalanceUncertain(true)
        setError(errorText(failure))
      }
    } finally {
      if (identity.current === currentIdentity && sequence === accountSequence.current) setAccountLoading(false)
    }
  }

  useEffect(() => {
    const current = ++identity.current
    saveSession(token, codeRef.current)
    void loadAccount(token, current)
    return () => { identity.current++ }
    // This effect owns the identity lifetime; requests read the captured token.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token])

  function clearRoom(explanation = '') {
    roomEpoch.current++
    codeRef.current = null
    roomRef.current = null
    serverClock.current = null
    disconnect.current()
    setRoomCode(null)
    setRoom(null)
    setRestoring(false)
    setConnectionError('')
    setPendingBox(null)
    if (roomRequest.current?.lock === mutation.current) {
      mutation.current = null
      setBusy(null)
    }
    roomRequest.current = null
    setError('')
    setMessage(explanation)
    saveSession(token, null)
  }

  function accept(snapshot: RoomState, sentAt?: number) {
    if (snapshot.code !== codeRef.current || snapshot.revision < (roomRef.current?.revision ?? -1)) return
    const received = performance.now()
    const clock = serverClock.current
    // GET round trips estimate transport delay. Socket timestamps are a lower bound
    // on server-now; they never move an already calibrated clock backwards.
    const estimated = snapshot.server_time * 1000 + (sentAt === undefined ? 0 : (received - sentAt) / 2)
    if (!clock || estimated > clock.time + received - clock.received) {
      serverClock.current = { time: estimated, received }
    }
    roomRef.current = snapshot
    setRoom(snapshot)
    const pending = roomRequest.current
    if (pending?.settled(snapshot)) {
      // Socket state can settle a room action before its REST reply. Never carry
      // a pending action into a later round or let its reply overwrite a new one.
      roomRequest.current = null
      if (mutation.current === pending.lock) {
        mutation.current = null
        setBusy(null)
        setPendingBox(null)
      }
    }
    setRestoring(false)
    setConnectionError('')
  }

  useEffect(() => {
    if (!roomCode) return
    const currentIdentity = identity.current
    const currentRoom = roomEpoch.current
    let active = true
    let syncing = false
    let everConnected = false
    let watchSequence = 0
    const current = () => active && identity.current === currentIdentity && roomEpoch.current === currentRoom
    const socket = io(apiBase, {
      autoConnect: false,
      forceNew: true,
      auth: { access_token: token },
      reconnectionDelay: 800,
      reconnectionDelayMax: 4000,
      timeout: 10_000,
    })
    setConnection('connecting')
    setConnectionError('')

    function membershipLost(reason: string) {
      if (!current()) return
      setJoinCode(roomCode!)
      clearRoom(reason)
    }

    async function sync() {
      if (!current() || syncing) return
      syncing = true
      const sent = performance.now()
      try {
        const snapshot = await request<RoomState>(`/rooms/${roomCode}`, token)
        if (current()) accept(snapshot, sent)
      } catch (failure) {
        if (!current()) return
        if (failure instanceof ApiError && (failure.status === 403 || failure.status === 404)) {
          membershipLost(failure.status === 404
            ? `Room ${roomCode} is no longer available. Create a new room or join another.`
            : `You are no longer in room ${roomCode}. Waiting-room players are removed after disconnecting; only Ready players enter a game. You can try joining again.`)
        } else {
          setConnectionError(errorText(failure))
          setRestoring(false)
        }
      } finally { syncing = false }
    }

    function watch() {
      const sequence = ++watchSequence
      const socketId = socket.id
      socket.timeout(8000).emit('watch_room', { code: roomCode }, (timeout: Error | null, ack?: { ok: boolean; error?: string }) => {
        if (!current() || sequence !== watchSequence || socket.id !== socketId || !socket.connected) return
        if (timeout) {
          setConnection('offline')
          setConnectionError('Room synchronization timed out. Reconnect to get the latest state.')
        } else if (!ack?.ok) {
          if (ack?.error === 'Room membership required' || ack?.error === 'Room not found') {
            membershipLost(`You are no longer in room ${roomCode}. ${ack.error}. Rejoin using the code, or create a new room.`)
          } else {
            setConnection('offline')
            setConnectionError(ack?.error || 'Could not watch this room. Please reconnect.')
          }
        } else {
          setConnection('connected')
        }
      })
    }

    socket.on('connect', () => {
      if (!current()) return
      everConnected = true
      watch()
      void sync()
    })
    socket.on('room_state', (snapshot: RoomState) => {
      if (current()) accept(snapshot)
    })
    socket.on('room_removed', (payload: { code: string; reason: string }) => {
      if (current() && payload.code === roomCode) membershipLost('The game started without you because you were not Ready. Join or create another room to play.')
    })
    socket.on('disconnect', () => {
      if (!current()) return
      watchSequence++
      setConnection('reconnecting')
      setConnectionError('Connection lost. Reconnecting… The server keeps the game moving.')
    })
    socket.on('connect_error', () => {
      if (!current()) return
      watchSequence++
      setConnection(everConnected ? 'reconnecting' : 'offline')
      setConnectionError('Cannot connect to live updates. Check your connection, demo token, and that both services are running.')
      setRestoring(false)
    })
    const reconnect = () => {
      if (!current()) return
      setConnectionError('')
      setConnection('connecting')
      if (socket.connected) watch()
      else socket.connect()
      void sync()
    }
    const onVisible = () => { if (document.visibilityState === 'visible') reconnect() }
    resync.current = reconnect
    disconnect.current = () => { active = false; socket.removeAllListeners(); socket.disconnect() }
    window.addEventListener('online', reconnect)
    document.addEventListener('visibilitychange', onVisible)
    void sync()
    socket.connect()
    return () => {
      active = false
      window.removeEventListener('online', reconnect)
      document.removeEventListener('visibilitychange', onVisible)
      socket.removeAllListeners()
      socket.disconnect()
      resync.current = () => {}
      disconnect.current = () => {}
    }
    // A socket belongs to exactly one identity/room. Replacing either creates a new socket.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, roomCode])

  const round = room?.game?.current_round
  useEffect(() => {
    if (room?.status !== 'PLAYING' || round?.phase !== 'CHOOSING') { setSecondsLeft(0); return }
    function updateClock() {
      const clock = serverClock.current
      const serverNow = clock ? clock.time + performance.now() - clock.received : Date.now()
      setSecondsLeft(Math.max(0, Math.min(10, Math.ceil((round!.deadline * 1000 - serverNow) / 1000))))
    }
    updateClock()
    const interval = window.setInterval(updateClock, 100)
    return () => window.clearInterval(interval)
  }, [room?.status, round?.number, round?.phase, round?.deadline])

  useEffect(() => {
    if (room?.status === 'PLAYING' || room?.status === 'FINISHED') void loadAccount(token, identity.current, true)
    // Every participant refreshes their balance after ticket consumption.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [room?.status])

  async function mutate(kind: Exclude<Busy, null>, action: (current: () => boolean) => Promise<void>) {
    if (mutation.current) return
    const lock = Symbol(kind)
    mutation.current = lock
    const currentIdentity = identity.current
    const currentRoom = roomEpoch.current
    const current = () => identity.current === currentIdentity
      && (kind === 'purchase' || roomEpoch.current === currentRoom)
      && mutation.current === lock
    setBusy(kind)
    setError('')
    setMessage('')
    try { await action(current) }
    catch (failure) { if (current()) setError(errorText(failure)) }
    finally {
      if (mutation.current === lock) {
        mutation.current = null
        if (roomRequest.current?.lock === lock) roomRequest.current = null
        setBusy(null)
        setPendingBox(null)
      }
    }
  }

  function enterRoom(snapshot: RoomState) {
    roomEpoch.current++
    disconnect.current()
    codeRef.current = snapshot.code
    roomRef.current = null
    serverClock.current = null
    setRoomCode(snapshot.code)
    setConnection('connecting')
    saveSession(token, snapshot.code)
    accept(snapshot)
  }

  function selectUser(nextToken: string) {
    if (mutation.current || nextToken === token || !users.some(user => user.token === nextToken)) return
    identity.current++
    accountSequence.current++
    clearRoom()
    setAccount(null)
    setAccountLoading(true)
    setBalanceUncertain(false)
    setJoinCode('')
    setToken(nextToken)
    saveSession(nextToken, null)
  }

  function purchase() {
    if (accountLoading || balanceUncertain || !account) return
    return mutate('purchase', async current => {
      const sequence = ++accountSequence.current
      try {
        const data = await requestAccount('/tickets/purchase', token, 'POST')
        if (current()) {
          if (sequence === accountSequence.current) setAccount(data)
          else void loadAccount(token, identity.current, true)
          setMessage(previous => previous || 'One demo ticket added. Your next adventure is ready.')
        }
      } catch (failure) {
        if (current()) {
          setBalanceUncertain(true)
          setError(`${errorText(failure)} Could not confirm the purchase. Refresh your balance before buying again.`)
        }
      }
    })
  }

  function createRoom() {
    return mutate('create', async current => {
      const snapshot = await request<RoomState>('/rooms', token, 'POST')
      if (current()) enterRoom(snapshot)
    })
  }

  function joinRoom() {
    const code = joinCode.trim().toUpperCase()
    if (!/^[A-Z0-9]{6}$/.test(code)) { setError('Enter a six-character room code using letters and numbers.'); return }
    return mutate('join', async current => {
      const snapshot = await request<RoomState>(`/rooms/${code}/join`, token, 'POST')
      if (current()) enterRoom(snapshot)
    })
  }

  function roomMutation(kind: 'ready' | 'start' | 'choice', body?: { ready?: boolean; round?: number; box?: Box }) {
    const code = codeRef.current
    if (!code || connection !== 'connected') return
    return mutate(kind, async current => {
      roomRequest.current = {
        lock: mutation.current!,
        settled: snapshot => {
          if (kind === 'start') return snapshot.status === 'PLAYING' || snapshot.status === 'FINISHED'
          if (kind === 'ready') return snapshot.status !== 'WAITING' || snapshot.players.find(player => player.user_id === selected.id)?.ready === body?.ready
          const round = snapshot.game?.current_round
          return snapshot.status !== 'PLAYING' || round?.number !== body?.round || round?.phase !== 'CHOOSING' || round.my_choice !== null
        },
      }
      try {
        const snapshot = await request<RoomState>(`/rooms/${code}/${kind}`, token, kind === 'ready' ? 'PATCH' : 'POST', body)
        if (current()) accept(snapshot)
      } catch (failure) {
        // A lost response can hide an accepted choice or a completed ticket debit.
        // Resynchronize once; never retry a mutation automatically.
        if (current()) resync.current()
        throw failure
      }
    })
  }

  function toggleReady() {
    const player = roomRef.current?.players.find(player => player.user_id === selected.id)
    if (player && roomRef.current?.status === 'WAITING') return roomMutation('ready', { ready: !player.ready })
  }

  function startGame() {
    const snapshot = roomRef.current
    if (!snapshot || !['WAITING', 'STARTING'].includes(snapshot.status)) return
    if (!snapshot.players.find(player => player.user_id === selected.id)?.ready || snapshot.players.filter(player => player.ready).length < 2) return
    return roomMutation('start')
  }

  function choose(box: Box) {
    const snapshot = roomRef.current
    const current = snapshot?.game?.current_round
    if (mutation.current || connection !== 'connected' || snapshot?.status !== 'PLAYING' || current?.phase !== 'CHOOSING' || current.my_choice !== null || secondsLeft <= 0) return
    setPendingBox(box)
    return roomMutation('choice', { round: current.number, box })
  }

  function goHome() {
    if (mutation.current) return
    clearRoom()
    void loadAccount(token, identity.current, true)
  }

  return {
    selected, account: account?.user_id === selected.id ? account : null,
    accountLoading, balanceUncertain, busy, room, roomCode, connection, connectionError,
    error, message, joinCode, setJoinCode, restoring, secondsLeft, pendingBox,
    selectUser, refreshAccount: () => loadAccount(), purchase, createRoom, joinRoom,
    toggleReady, startGame, choose, goHome, reconnectRoom: () => resync.current(),
  }
}
