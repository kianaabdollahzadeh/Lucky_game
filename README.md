# Lucky Rooms - Phase 4

Two FastAPI services and a playable React/Vite/TypeScript multiplayer game in
the existing BoxSelectGame repository. Choose a demo player, buy mock tickets,
create or join a private room, and play five timed treasure-chest rounds with
friends. The fantasy arcade interface includes a lobby, live scores, chest
reveals, and a final winner screen. REST handles mutations; authenticated
Socket.IO snapshots synchronize the players. Payments are simulated.

## Run locally on Windows

Use Python 3.12+ and Node.js 22.12+ in VS Code PowerShell terminals. From the
repository root, install once:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
cd frontend
npm.cmd ci
cd ..
```

Copy the example only if you do not already have a `.env`. Both Python apps read
the root `.env`; existing process environment variables take precedence. Vite
also reads this file, but exposes only `VITE_*` variables to the browser. The
shared `SERVICE_SECRET` is development-only and must match between the services.
Never prefix it with `VITE_`. Restart the relevant process after changing config.

### One-command local or LAN demo

After the one-time installation above, start all three services in one terminal.
No virtual-environment activation is needed.

Local (open **http://localhost:5173**):

```powershell
python dev.py
```

LAN (automatically selects an active physical Wi-Fi/Ethernet IPv4 address):

```powershell
python dev.py --lan
```

Manual IP override (replace the example with this computer's LAN IPv4 address):

```powershell
python dev.py --lan --ip 192.168.1.100
```

Everyone on the LAN should open the **printed URL**, not `localhost`. Detection
prefers the physical interface with the lowest default-route/interface metric;
if equally suitable addresses remain, it lists them and asks for `--ip`. It does
not need internet access. Automatic detection supports Windows; other systems
can use the manual override. An absolute path to `dev.py` also works from another
directory.

The launcher checks dependencies and ports, waits for all services to respond,
and stops its service processes on **Ctrl+C** (or a service failure). It uses the
repository's `.venv` and does not install anything. Configuration overrides apply
only to child processes: `.env` and the shared service secret are preserved.
Restart after Python code/configuration changes; the Python services run without
auto-reload. Vite retains its normal frontend hot updates.

LAN mode exposes the frontend on TCP **5173** and backend on TCP **8000**, with
CORS restricted to the printed frontend origin. Mock Mother stays on loopback
**127.0.0.1:8001**. If Windows Firewall blocks connections, allow Python/Node or
these two LAN ports on your **trusted private network**. Guest Wi-Fi client
isolation can prevent devices reaching each other. This is a LAN demo, not an
internet deployment; the launcher does not change firewall or network settings.

### Manual startup fallback

Run each command in a **separate terminal**, starting from the repository root:

```powershell
# Terminal 1: Mock Mother Server
.\.venv\Scripts\python.exe -m uvicorn mock_mother.main:app --host 127.0.0.1 --port 8001
```

```powershell
# Terminal 2: Game Backend
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

```powershell
# Terminal 3: React frontend
cd frontend
npm.cmd run dev
```

Open **http://localhost:5173**. Choose users independently in separate tabs;
the selected demo token is kept in `sessionStorage`. Each user's balance is
shared across tabs. `npm.cmd` and the explicit virtual-environment Python path
work without changing PowerShell execution policy or activating scripts.

## Play in the browser

1. Open two or more tabs at **http://localhost:5173**. Choose a different demo
   player in each tab and buy a ticket for each player.
2. Create a room in one tab, copy its six-character code, and join from the others.
3. Toggle **I'm ready** for the players who will participate. Any Ready player
   can select **Start game** once at least two players are Ready.
4. Pick one of the three chests within each ten-second window. The first accepted
   choice locks in; the server reveals the values and updates scores. A missed
   choice earns zero. Five rounds run automatically, with two-second reveals.
5. View the final scores and any tied winners, then return Home to create or join
   a new room. A successful start consumes one ticket per Ready participant and
   returns non-Ready players Home.

The selected identity and active room code are stored together in `sessionStorage`
for this tab. Refreshing during a game restores the authorized room and current
scores. A player whose last watching connection closes in the waiting lobby may
be removed by the server; rejoin using the room code. Returning Home closes this
tab's room connection. The server keeps an active game running.

If ticket purchase cannot be confirmed, use **Refresh balance** before purchasing
again. A confirmed start failure restores the lobby. An uncertain ticket debit
can leave the room starting; **Retry start** safely reuses the backend operation.
Connection failures show a reconnect/resync action. The timer estimates server
time from snapshots and REST round trips; reaching zero only changes its display,
and the client waits for server results. No room polling is used.

Default configuration is in [.env.example](.env.example). CORS allows
`http://localhost:5173` exactly; update `FRONTEND_ORIGIN` if using a different
frontend origin. Vite uses a strict port so it does not silently move to 5174.

## Demo identities

| User ID | Display name | Access token |
| --- | --- | --- |
| user-1 | Alice | demo-alice |
| user-2 | Bob | demo-bob |
| user-3 | Charlie | demo-charlie |
| user-4 | Dana | demo-dana |

All balances start at zero. Tokens are public test fixtures, not real credentials.

## API

Interactive API docs: Game Backend at http://localhost:8000/docs and Mock Mother
Server at http://localhost:8001/docs.

| Service | Endpoint | Input / authorization |
| --- | --- | --- |
| Backend | `GET /me` | `Authorization: Bearer <demo-token>` |
| Backend | `POST /tickets/purchase` | Same Bearer header, no body; account comes from validated token |
| Backend | `POST /rooms` | Bearer header; creates a room and adds the caller (201) |
| Backend | `POST /rooms/{code}/join` | Bearer header; joins a waiting room, safely repeatable |
| Backend | `GET /rooms/{code}` | Bearer header; room members only |
| Backend | `PATCH /rooms/{code}/ready` | Bearer header and JSON `{ "ready": true }` or `{ "ready": false }` |
| Backend | `POST /rooms/{code}/start` | Bearer header; any Ready member may start |
| Backend | `POST /rooms/{code}/choice` | Bearer header and JSON `{ "round": 1, "box": 2 }` |
| Mother | `POST /auth/validate` | JSON `{ "access_token": "demo-alice" }` |
| Mother | `POST /tickets/purchase` | JSON `{ "user_id": "user-1" }` and `X-Service-Secret` header |
| Mother | `POST /tickets/consume` | JSON below and `X-Service-Secret` header |

Authentication and purchase return `user_id`, `display_name`, and `ticket_balance`.
Purchase adds exactly one ticket. On Start, the backend calls the internal Mother
consumption endpoint with the frozen list of Ready participants:

```json
{
  "participant_user_ids": ["user-1", "user-2"],
  "idempotency_key": "demo-start-001"
}
```

Consumption returns the key and a `ticket_balances` map. It checks all participants
and debits one ticket each under one lock. Missing users, duplicate IDs, invalid
requests, or insufficient funds leave every balance unchanged. A successful key
replays its original result without another debit; participant order is ignored.
Reusing that key for different participants returns 409. Failed attempts do not
reserve keys, so they can be retried after purchasing tickets.

The backend returns 401 for invalid/missing tokens, 502 for failed/malformed
upstream responses, 503 for network errors, and 504 for timeouts. It does not
automatically retry purchases. If a purchase response is lost, refresh the
balance before making another purchase.

## Rooms and gameplay

Rooms use unique six-character codes (letters and digits, case-insensitive on
lookup), hold up to four players, and begin in `WAITING`. Creators and new members
start non-Ready. Any Ready member can start once at least two players are Ready;
there is no Host. A user can belong to multiple rooms, with tickets checked
atomically for each game start.

Start moves the room to `STARTING` and freezes membership and readiness while
Mother consumes one ticket per Ready player. Only one request per room is in
flight. Confirmed insufficient tickets return 409 and restore `WAITING`, keeping
all members and readiness. If the request times out or its outcome is uncertain,
the room stays `STARTING`: a Ready participant can repeat Start with no body.
The backend reuses the same participants and idempotency key, preventing another
deduction. Non-Ready members are removed only after successful consumption and
can no longer read the room.

`PLAYING` runs five rounds automatically, including when no client is polling.
Every round has a 10-second `CHOOSING` window followed by a two-second `REVEAL`.
Boxes are numbered **1, 2, 3** and contain a fresh server shuffle of `[10, 0, -5]`.
Players may pick the same box. The first valid choice is final; identical retries
are harmless during that round's open window. A missed choice scores zero. The
server enforces monotonic deadlines and never reveals early, even if everyone
has chosen. Highest total score wins, including ties. `FINISHED` rooms cannot
restart; create a new room to play again.

All successful room endpoints return a snapshot with `code`, `revision`, `status`,
`players`, `game`, and `server_time`. `game` is null before play; during play it contains
`participant_ids`, `scores`, `round_number`, `current_round`, and `winner_ids`.
The current round contains `number`, `phase`, `choices` (user ID to box number),
`my_choice` (the authenticated viewer's box number, or null), and `deadline` (Unix
seconds). During `CHOOSING`, `choices` contains only the viewer's own selection;
during `REVEAL`, it contains everyone's selections. Use `server_time` and `deadline` for countdown
display; the deadline always marks the end of choosing. `box_values` appears
only in `REVEAL`, in box-number order. Final scores and all winner IDs remain
available after finishing. A player's `connected` flag means at least one of
their authenticated sockets is watching this room.

Room errors use FastAPI's `detail` response: 401 for missing/invalid authentication,
403 for membership or readiness authorization, 404 for an unknown room, 409 for
capacity, state, timing, or choice conflicts, and 422 for invalid request bodies.
All six room endpoints require authentication. Readiness requires an explicit
JSON boolean; choice round and box IDs require integers.

## Real-time room observation

Socket.IO uses the default `/socket.io/` path on the Game Backend (port 8000),
with HTTP polling and WebSocket support. It shares `FRONTEND_ORIGIN` with REST.
The existing Uvicorn command is unchanged; use one worker. Integration uses
python-socketio's [ASGI support](https://python-socketio.readthedocs.io/en/stable/server.html#running-as-an-asgi-application).

Connect with a Socket.IO 4.x client and `auth: { access_token: "demo-alice" }`.
The backend validates the token with Mother; missing or invalid tokens reject
the connection. Any client-supplied user ID is ignored. Authentication is checked
again on reconnect. Create or join the gameplay room through REST first.

| Direction | Event | Payload / result |
| --- | --- | --- |
| Client to server | `watch_room` | Room-code string, or `{ "code": "ABC123" }`; acknowledgement `{ "ok": true, "code": "ABC123" }` or `{ "ok": false, "error": "..." }` |
| Server to client | `room_state` | The same personalized snapshot returned by REST |
| Server to client | `room_removed` | `{ "code": "ABC123", "reason": "not_ready" }` after successful Start excludes a non-Ready watcher |

Only current members can watch. Watching sends the current snapshot immediately
and then updates for joins, readiness, Start success/failure, choices, every
round and reveal, scores, game completion, and connection changes. Watch retries
are safe and never create players. Each socket watches one room; disconnect and
reconnect to switch rooms. Multiple sockets for one user are supported.

`room_state` is sent separately to each watching socket so private choices stay
private. Notifications are captured in mutation order and delivered outside room
locks; socket I/O does not control the game clock. `revision` increases on room
changes. When combining REST replies and socket events, keep the highest revision
for that room and discard older snapshots. On reconnect, call `watch_room` again
for the latest state; authenticated `GET /rooms/{code}` also recovers state.

When the last watching socket for a user disconnects in `WAITING`, that member
is removed and remaining watchers receive the updated room. REST-only members
are never removed merely for not opening a socket. In `STARTING`, disconnects
only mark connectivity false: the frozen ticket operation and participants stay
intact, even if a confirmed ticket failure later returns the room to `WAITING`.
In `PLAYING` and `FINISHED`, participants, choices and scores are retained.
Rounds continue while everyone is disconnected; reconnecting never pauses or
restarts the game. A player removed from `WAITING` must join again through REST.

There are no socket gameplay mutation events. Continue to use REST for Ready,
Start and choices. Non-Ready watchers receive `room_removed` only on successful
Start and are unsubscribed from subsequent game updates.

## Verify

From the repository root:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_phase1.py tests/test_phase2.py tests/test_phase3.py -q
.\.venv\Scripts\python.exe -m compileall -q backend mock_mother tests
.\.venv\Scripts\python.exe -c "import backend.main, mock_mother.main"
cd frontend
npm.cmd run build
```

Tests use fresh in-memory Mother applications and HTTPX transports, with no
running services needed. They cover auth, balance changes, service secrets,
atomicity, idempotency, concurrent consumption, validation, CORS, and upstream
errors. Phase 2 adds room capacity and membership, concurrent starts, safe retries
after uncertain consumption, hidden results, choices, timed rounds, scoring, and
winners. Phase 3 covers socket authentication, member-only observation, private
snapshots, ordered state updates, removal, disconnect/reconnect and multiple
connections. Tests inject shorter round durations through `RoomManager`; normal
application startup always uses 10-second choosing and two-second reveal windows.

The frontend build includes strict TypeScript checking. For an interaction review,
follow the browser steps above with two distinct users; leave a third user not
Ready to check removal. Try starting without tickets, refresh during play, and
disconnect/reconnect a waiting player. Choose the same chest with both players
through all five rounds to exercise the shared-winner screen. Check Home, lobby,
game, and final screens at 320, 375, 390, and 430 px widths, including keyboard
focus and reduced-motion settings. The UI uses native CSS and inline SVG, with
`socket.io-client` as its only additional runtime dependency.

For a quick HTTP check with both services running:

```powershell
$demoHeaders = @{ Authorization = 'Bearer demo-alice' }
Invoke-RestMethod http://localhost:8000/me -Headers $demoHeaders
Invoke-RestMethod http://localhost:8000/tickets/purchase -Method Post -Headers $demoHeaders
Invoke-RestMethod http://localhost:8000/me -Headers $demoHeaders
```

## Limits

Run **both services with one worker each**. Rooms, game tasks, balances, and
idempotency records exist only in memory and reset when their service restarts.
Auto-reloading also resets state. Active game and notification tasks are cancelled
on backend shutdown. Successful keys and rooms (including empty and finished
rooms) are retained for their process's lifetime. Delivery is best effort; there
is no event replay history, and token revocation is not polled during an existing
connection. This is a local mock foundation; persistent storage, production
authentication, payment handling, restart recovery, and scale testing are outside
this demo. A Home action leaves the current view rather than cancelling an active
game. There is no rematch; create or join another room. Session recovery depends
on browser session storage and the current backend process still holding the room.
