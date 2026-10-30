export type Account = {
  user_id: string
  display_name: string
  ticket_balance: number
}

export type Box = 1 | 2 | 3
export type RoomState = {
  code: string
  revision: number
  status: 'WAITING' | 'STARTING' | 'PLAYING' | 'FINISHED'
  players: { user_id: string; display_name: string; ready: boolean; connected: boolean }[]
  game: null | {
    participant_ids: string[]
    scores: Record<string, number>
    round_number: number
    current_round: null | {
      number: number
      phase: 'CHOOSING' | 'REVEAL'
      deadline: number
      choices: Record<string, Box>
      my_choice: Box | null
      box_values?: number[]
    }
    winner_ids: string[]
  }
  server_time: number
}

export const apiBase = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000').replace(/\/$/, '')

export class ApiError extends Error {
  constructor(message: string, public status: number) {
    super(message)
  }
}

export async function request<T>(path: string, token: string, method = 'GET', body?: unknown): Promise<T> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), 15_000)
  try {
    const response = await fetch(`${apiBase}${path}`, {
      method,
      headers: { Authorization: `Bearer ${token}`, ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      signal: controller.signal,
    })
    const data = await response.json().catch(() => null)
    if (!response.ok) {
      const detail = typeof data?.detail === 'string' ? data.detail : null
      throw new ApiError(detail || (response.status === 401 ? 'Your demo token is invalid or expired. Select a demo player again.' : `Request failed (${response.status}). Please try again.`), response.status)
    }
    if (data === null) throw new Error('The server returned an unreadable response. Please reconnect.')
    return data as T
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (controller.signal.aborted) throw new Error('The server took too long to respond. Please check your connection.')
    if (error instanceof TypeError) throw new Error('Cannot reach the game server. Check your connection and that both services are running.')
    throw error
  } finally {
    window.clearTimeout(timeout)
  }
}

export function requestAccount(path: string, token: string, method = 'GET') {
  return request<Account>(path, token, method)
}
