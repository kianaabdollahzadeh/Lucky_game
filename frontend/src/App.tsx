import { useState } from "react";
import { useLuckyRooms, users } from "./useLuckyRooms";
import { ClubMark, MysteryBox, PlayerToken } from "./GameArt";

type Game = ReturnType<typeof useLuckyRooms>;
const boxes = [1, 2, 3] as const;
const signed = (value: number) => `${value > 0 ? "+" : ""}${value}`;
const playerIndex = (id: string) =>
  Math.max(
    0,
    users.findIndex((user) => user.id === id),
  );

function TicketBalance({ game }: { game: Game }) {
  return (
    <div className="ticket-balance">
      <div className="ticket-count">
        <span className="eyebrow">{game.selected.name}’s tickets</span>
        <strong>
          {game.accountLoading || game.balanceUncertain
            ? "—"
            : (game.account?.ticket_balance ?? "—")}
          <small> available</small>
        </strong>
      </div>
      <button
        className="ticket-buy"
        onClick={() => void game.purchase()}
        disabled={
          !!game.busy ||
          game.accountLoading ||
          game.balanceUncertain ||
          !game.account
        }
      >
        {game.busy === "purchase" ? "Adding…" : "+ Buy ticket"}
      </button>
    </div>
  );
}

function Scoreboard({ game, final = false }: { game: Game; final?: boolean }) {
  const { room, selected } = game;
  if (!room) return null;
  const round = room.game?.current_round;
  const players = final
    ? [...room.players].sort(
        (a, b) =>
          (room.game?.scores[b.user_id] ?? 0) -
          (room.game?.scores[a.user_id] ?? 0),
      )
    : room.players;
  return (
    <section
      className={`scoreboard ${final ? "final-scores" : ""}`}
      aria-label={final ? "Final standings" : "Scoreboard"}
    >
      <div className="section-line">
        <h2>{final ? "The final tally" : "Around the table"}</h2>
        <span className="eyebrow">
          {final ? "5 rounds played" : "Scoreboard"}
        </span>
      </div>
      <div className="score-list">
        {players.map((player) => {
          const score = room.game?.scores[player.user_id] ?? 0;
          const winner =
            final && room.game?.winner_ids.includes(player.user_id);
          const choice =
            player.user_id === selected.id
              ? round?.my_choice
              : round?.choices[player.user_id];
          const delta =
            round?.phase === "REVEAL" && round.box_values
              ? choice
                ? round.box_values[choice - 1]
                : 0
              : null;
          const rank =
            1 +
            players.filter(
              (other) => (room.game?.scores[other.user_id] ?? 0) > score,
            ).length;
          return (
            <div
              className={`score-player ${winner ? "is-winner" : ""}`}
              key={player.user_id}
            >
              {final && (
                <span className="rank">{String(rank).padStart(2, "0")}</span>
              )}
              <PlayerToken index={playerIndex(player.user_id)} />
              <span className="score-name">
                <strong>{player.display_name}</strong>
                <small>
                  {winner
                    ? room.game!.winner_ids.length > 1
                      ? "Tied winner"
                      : "Winner"
                    : player.user_id === selected.id
                      ? !final && !player.connected
                        ? "You · Offline"
                        : "You"
                      : final
                        ? "Good game"
                        : !player.connected
                          ? "Offline"
                          : "In the game"}
                </small>
              </span>
              {!final && delta !== null && (
                <span
                  key={`${round?.number}-${player.user_id}`}
                  className={`score-delta ${delta > 0 ? "positive" : delta < 0 ? "negative" : ""}`}
                >
                  {signed(delta)}
                </span>
              )}
              <span className="score-total">
                <strong key={score}>{score}</strong>
                <small>pts</small>
              </span>
            </div>
          );
        })}
      </div>
    </section>
  );
}

export default function App() {
  const game = useLuckyRooms();
  const [copied, setCopied] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);
  const { room, selected, busy } = game;
  const me = room?.players.find((player) => player.user_id === selected.id);
  const round = room?.game?.current_round;
  const connected = game.connection === "connected";
  const readyCount = room?.players.filter((player) => player.ready).length ?? 0;
  const canAct = !busy && connected;
  const reveal = round?.phase === "REVEAL";
  const winners =
    room?.players.filter((player) =>
      room.game?.winner_ids.includes(player.user_id),
    ) ?? [];
  const myDelta =
    reveal && round.my_choice && round.box_values
      ? round.box_values[round.my_choice - 1]
      : 0;
  async function copyCode() {
    if (!room) return;
    try {
      await navigator.clipboard.writeText(room.code);
      setCopied(true);
      setCopyFailed(false);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
      setCopyFailed(true);
    }
  }
  return (
    <div className={`app-shell ${room ? "in-room" : "at-home"}`}>
      <header className="topbar">
        <button
          className="brand"
          onClick={game.goHome}
          disabled={!!busy}
          aria-label="Lucky Rooms home"
        >
          <ClubMark />
          <span>
            LUCKY
            <br />
            ROOMS<span className="brand-period">.</span>
          </span>
        </button>
        <span className="topbar-caption">A little luck. A good crowd.</span>
        {room ? (
          <button
            className="text-button"
            onClick={game.goHome}
            disabled={!!busy}
          >
            ← Home
          </button>
        ) : (
          <span className="demo-label">
            <i /> The demo edition
          </span>
        )}
      </header>
      <main className="main-wrap">
        {(game.error || game.message) && (
          <div
            className={`notice ${game.error ? "notice-error" : ""}`}
            role={game.error ? "alert" : "status"}
          >
            {game.error || game.message}
            {(game.balanceUncertain ||
              (!game.account && !game.accountLoading)) && (
              <button
                onClick={() => void game.refreshAccount()}
                disabled={game.accountLoading}
              >
                Refresh balance
              </button>
            )}
          </div>
        )}
        {game.restoring ? (
          <section className="loading-state">
            <ClubMark />
            <span className="eyebrow">Pulling up a chair</span>
            <h1>
              Finding
              <br />
              your room.
            </h1>
            <p>Getting your game and your place at the table.</p>
            <span className="spinner" />
            <button
              className="button button-outline"
              onClick={game.goHome}
              disabled={!!busy}
            >
              Back to home
            </button>
          </section>
        ) : !room && game.roomCode ? (
          <section className="loading-state">
            <ClubMark />
            <h1>
              Lost the
              <br />
              room?
            </h1>
            <p>
              We couldn’t restore room {game.roomCode}. Reconnect to try again,
              or return home.
            </p>
            {game.connectionError && <p role="alert">{game.connectionError}</p>}
            <button
              className="button button-primary"
              onClick={game.reconnectRoom}
            >
              Reconnect / resync
            </button>
            <button
              className="button button-outline"
              onClick={game.goHome}
              disabled={!!busy}
            >
              Back to home
            </button>
          </section>
        ) : !room ? (
          <>
            <div className="home-layout">
              <section className="game-sleeve" aria-labelledby="home-title">
                <div className="sleeve-top">
                  <span className="eyebrow">The good-guessing game</span>
                  <span className="edition">No. 001</span>
                </div>
                <h1 id="home-title">
                  LUCKY
                  <br />
                  <span>ROOMS</span>
                </h1>
                <div className="sleeve-caption">
                  <p>
                    Good friends.
                    <br />
                    Questionable instincts.
                  </p>
                  <span className="sleeve-arrow" aria-hidden="true">
                    ↘
                  </span>
                </div>
                <div className="sleeve-art" aria-hidden="true">
                  <div className="sleeve-box sleeve-box-one">
                    <MysteryBox number={1} />
                  </div>
                  <div className="sleeve-box sleeve-box-two">
                    <MysteryBox number={2} />
                  </div>
                  <div className="sleeve-box sleeve-box-three">
                    <MysteryBox number={3} />
                  </div>
                </div>
                <div className="game-specs">
                  <span>
                    <b>2–4</b> players
                  </span>
                  <span>
                    <b>5</b> rounds
                  </span>
                  <span>
                    <b>10</b> sec / pick
                  </span>
                </div>
              </section>
              <div className="entry-desk">
                <section className="player-panel">
                  <div className="section-label">
                    <span>01</span>
                    <span>Pick your personality</span>
                  </div>
                  <h2>Who’s at the table?</h2>
                  <p className="subcopy">
                    Choose a demo player. Bring your best guess.
                  </p>
                  <div
                    className="player-grid"
                    role="group"
                    aria-label="Choose your player"
                  >
                    {users.map((user, index) => (
                      <button
                        key={user.id}
                        className={`player-choice ${selected.token === user.token ? "active" : ""}`}
                        aria-pressed={selected.token === user.token}
                        disabled={!!busy}
                        onClick={() => game.selectUser(user.token)}
                      >
                        <PlayerToken index={index} />
                        <span>{user.name}</span>
                        <span className="choice-indicator" aria-hidden="true">
                          {selected.token === user.token ? "✓" : ""}
                        </span>
                      </button>
                    ))}
                  </div>
                  <TicketBalance game={game} />
                  {game.accountLoading && (
                    <p className="small-status" role="status">
                      Loading your account…
                    </p>
                  )}
                </section>
                <section className="room-panel">
                  <div className="section-label">
                    <span>02</span>
                    <span>Make room for a little luck</span>
                  </div>
                  <button
                    className="button button-primary create-button"
                    onClick={() => void game.createRoom()}
                    disabled={!!busy || game.accountLoading || !game.account}
                  >
                    {busy === "create" ? "Creating room…" : "Create a room"}
                    <span aria-hidden="true">↗</span>
                  </button>
                  <form
                    className="join-form"
                    onSubmit={(event) => {
                      event.preventDefault();
                      void game.joinRoom();
                    }}
                  >
                    <label htmlFor="room-code">Have a room code?</label>
                    <div className="join-controls">
                      <input
                        id="room-code"
                        value={game.joinCode}
                        onChange={(event) =>
                          game.setJoinCode(event.target.value.toUpperCase())
                        }
                        placeholder="ROOM CODE"
                        maxLength={6}
                        autoComplete="off"
                        autoCapitalize="characters"
                        spellCheck={false}
                      />
                      <button
                        className="button button-ink"
                        disabled={
                          !!busy ||
                          game.accountLoading ||
                          !game.account ||
                          game.joinCode.trim().length !== 6
                        }
                      >
                        {busy === "join" ? "Joining…" : "Join"}
                        <span aria-hidden="true"> →</span>
                      </button>
                    </div>
                  </form>
                </section>
              </div>
            </div>
            <section className="rules" aria-label="How to play">
              <span className="rules-heading">
                Small boxes.
                <br />
                Big possibilities.
              </span>
              <p>
                Pick one box each round. What’s inside is a surprise. The most
                points after five rounds wins.
              </p>
              <div className="value-key">
                <span>
                  <b>+10</b> Nice pick.
                </span>
                <span>
                  <b>0</b> No harm.
                </span>
                <span>
                  <b>−5</b> Oh well.
                </span>
              </div>
            </section>
          </>
        ) : (
          <>
            <div className="room-meta">
              <span className="eyebrow">
                Room <b>{room.code}</b>
              </span>
              <div
                className={`connection ${connected && !game.connectionError ? "connection-good" : ""}`}
                role="status"
              >
                <span className="connection-dot" />
                {connected
                  ? "Connected"
                  : game.connection === "connecting"
                    ? "Connecting…"
                    : game.connection === "reconnecting"
                      ? "Reconnecting…"
                      : "You’re offline."}
                {game.connectionError && (
                  <span className="connection-error">
                    {game.connectionError}
                  </span>
                )}
                {(!connected || game.connectionError) && (
                  <button onClick={game.reconnectRoom}>
                    Reconnect / resync
                  </button>
                )}
              </div>
            </div>
            {room.status === "WAITING" || room.status === "STARTING" ? (
              <section className="lobby-layout">
                <div className="lobby-heading">
                  <div>
                    <span className="eyebrow">Good company starts here</span>
                    <h1>
                      Take a seat<span className="accent">.</span>
                    </h1>
                    <p>Send the code. Get your people around the table.</p>
                  </div>
                  <div className="invite-ticket">
                    <span className="eyebrow">Your room code</span>
                    <strong className="room-code">{room.code}</strong>
                    <button
                      className="text-button"
                      onClick={() => void copyCode()}
                    >
                      {copied ? "Copied! ✓" : "Copy code ↗"}
                    </button>
                    {copyFailed && (
                      <p className="small-status" role="status">
                        Could not copy; select the code manually.
                      </p>
                    )}
                  </div>
                </div>
                <div className="lobby-content">
                  <section
                    className="table-board"
                    aria-label="Players in the room"
                  >
                    {Array.from({ length: 4 }, (_, index) => {
                      const player = room.players[index];
                      return (
                        <div
                          className={`table-seat seat-${index} ${player?.ready ? "seat-ready" : ""}`}
                          key={player?.user_id ?? `empty-${index}`}
                        >
                          <span className="seat-number">0{index + 1}</span>
                          {player ? (
                            <>
                              <PlayerToken
                                index={playerIndex(player.user_id)}
                              />
                              <strong>
                                {player.display_name}
                                {player.user_id === selected.id && (
                                  <small> YOU</small>
                                )}
                              </strong>
                              <span
                                className={`ready-tag ${player.ready ? "is-ready" : ""}`}
                              >
                                {player.ready ? "✓ Ready" : "Not ready"}
                              </span>
                              <span className="seat-connection">
                                {player.connected
                                  ? "Connected"
                                  : "Disconnected"}
                              </span>
                            </>
                          ) : (
                            <>
                              <div className="empty-token">+</div>
                              <strong>Open seat</strong>
                              <span className="seat-connection">
                                Waiting for a friend
                              </span>
                            </>
                          )}
                        </div>
                      );
                    })}
                    <div className="table-center">
                      <ClubMark />
                      <span>ANYONE’S GAME.</span>
                      <small>
                        {room.players.length} / 4 seats · {readyCount} ready
                      </small>
                    </div>
                  </section>
                  <aside className="lobby-actions">
                    <div className="section-label">
                      <span>→</span>
                      <span>The next move</span>
                    </div>
                    <h2>
                      A ticket to
                      <br />a good time.
                    </h2>
                    <p>
                      One ticket each. At least two ready players. Then it’s
                      anyone’s game.
                    </p>
                    <TicketBalance game={game} />
                    {!game.accountLoading &&
                      !game.balanceUncertain &&
                      game.account?.ticket_balance === 0 && (
                        <p className="ticket-hint">
                          You’ll need a ticket before the game can start.
                        </p>
                      )}
                    {room.status === "STARTING" ? (
                      <>
                        <div className="starting" role="status">
                          <span className="spinner" /> Starting your game…
                        </div>
                        <p className="small-status">
                          Taking a while? Retrying is safe while the room is
                          starting.
                        </p>
                        {me?.ready && (
                          <button
                            className="button button-outline wide"
                            disabled={!!busy || !connected}
                            onClick={() => void game.startGame()}
                          >
                            Retry start
                          </button>
                        )}
                      </>
                    ) : (
                      <>
                        <button
                          className={`button wide ${me?.ready ? "button-ready" : "button-primary"}`}
                          disabled={!canAct || !me}
                          onClick={() => void game.toggleReady()}
                        >
                          {busy === "ready"
                            ? "Updating…"
                            : me?.ready
                              ? "✓ You’re ready"
                              : "I’m ready"}
                          <span aria-hidden="true">
                            {me?.ready ? "↶" : "→"}
                          </span>
                        </button>
                        <button
                          className="button button-ink wide"
                          disabled={!canAct || !me?.ready || readyCount < 2}
                          onClick={() => void game.startGame()}
                        >
                          {busy === "start" ? "Starting…" : "Start game"}
                          <span aria-hidden="true">↗</span>
                        </button>
                      </>
                    )}
                    <p className="lobby-footnote">
                      {readyCount < 2
                        ? `${readyCount} of 2 required players ready.`
                        : "The table is ready. Any ready player can start."}{" "}
                      Players who aren’t ready sit this one out.
                    </p>
                  </aside>
                </div>
              </section>
            ) : room.status === "PLAYING" ? (
              <section
                className="game-layout"
                data-phase={round?.phase}
                data-round={round?.number}
              >
                <div className="game-top">
                  <div className="game-title">
                    <div className="round-track">
                      <span className="eyebrow">
                        Round {room.game?.round_number ?? "—"} / 5
                      </span>
                      <span className="round-marks" aria-hidden="true">
                        {[1, 2, 3, 4, 5].map((number) => (
                          <i
                            key={number}
                            className={
                              number < (round?.number ?? 1)
                                ? "done"
                                : number === round?.number
                                  ? "current"
                                  : ""
                            }
                          />
                        ))}
                      </span>
                    </div>
                    <h1>
                      {reveal ? (
                        <>
                          The inside
                          <br className="mobile-break" /> story
                          <span className="accent">.</span>
                        </>
                      ) : (
                        <>
                          Trust your
                          <br className="mobile-break" /> gut
                          <span className="accent">.</span>
                        </>
                      )}
                    </h1>
                  </div>
                  <div
                    className={`timer ${game.secondsLeft <= 3 && !reveal ? "urgent" : ""}`}
                    aria-label={
                      reveal
                        ? "Boxes revealed"
                        : `${game.secondsLeft} seconds left`
                    }
                  >
                    <svg viewBox="0 0 100 100" aria-hidden="true">
                      <circle className="timer-track" cx="50" cy="50" r="45" />
                      <circle
                        className="timer-progress"
                        cx="50"
                        cy="50"
                        r="45"
                        strokeDasharray="282.75"
                        strokeDashoffset={
                          reveal ? 0 : 282.75 * (1 - game.secondsLeft / 10)
                        }
                      />
                    </svg>
                    <strong>
                      {reveal ? "!" : String(game.secondsLeft).padStart(2, "0")}
                    </strong>
                    <small>{reveal ? "REVEAL" : "SECONDS"}</small>
                  </div>
                </div>
                <p className="game-instruction">
                  {reveal
                    ? "A little good luck. A little plot twist."
                    : game.pendingBox !== null
                      ? "Sending your choice to the table…"
                      : round?.my_choice
                        ? "Your pick is locked. The suspense is part of it."
                        : game.secondsLeft <= 0
                          ? "Time’s up. Here comes the reveal."
                          : "Three mystery boxes. One first instinct. Make your pick."}
                </p>
                <div className={`box-stage ${reveal ? "stage-reveal" : ""}`}>
                  <div className="stage-caption" aria-hidden="true">
                    <span>
                      {reveal ? "THE SECRETS ARE OUT" : "WHAT’S IN THE BOX?"}
                    </span>
                    <span>+10 / 0 / −5</span>
                  </div>
                  <div className="boxes">
                    {boxes.map((box) => {
                      const chosen =
                        round?.my_choice === box || game.pendingBox === box;
                      const value = reveal
                        ? round?.box_values?.[box - 1]
                        : undefined;
                      return (
                        <button
                          key={box}
                          className={`box-button ${chosen ? "chosen" : ""} ${reveal ? "revealing" : ""}`}
                          disabled={
                            !canAct ||
                            round?.phase !== "CHOOSING" ||
                            !!round.my_choice ||
                            game.pendingBox !== null ||
                            game.secondsLeft <= 0
                          }
                          onClick={() => void game.choose(box)}
                          aria-label={
                            value !== undefined
                              ? `Box ${box}: ${signed(value)} points${round?.my_choice === box ? ", your pick" : ""}`
                              : `Choose box ${box}${round?.my_choice === box ? ", choice locked in" : game.pendingBox === box ? ", submitting choice" : ""}`
                          }
                          aria-pressed={round?.my_choice === box}
                          aria-busy={game.pendingBox === box}
                        >
                          <span className="box-art-wrap">
                            <MysteryBox number={box} open={reveal} />
                            {value !== undefined && (
                              <span
                                key={`${round?.number}-${box}`}
                                className={`box-result ${value < 0 ? "loss" : value === 0 ? "zero" : ""}`}
                              >
                                <strong>{signed(value)}</strong>
                                <small>POINTS</small>
                              </span>
                            )}
                          </span>
                          <span className="box-label">
                            <b>0{box}</b>
                            <span>
                              {chosen
                                ? game.pendingBox === box
                                  ? "Sending…"
                                  : "Your pick ✓"
                                : reveal
                                  ? "Revealed"
                                  : "Pick me"}
                            </span>
                          </span>
                        </button>
                      );
                    })}
                  </div>
                </div>
                <div
                  className={`round-status ${reveal ? (myDelta > 0 ? "status-gain" : myDelta < 0 ? "status-loss" : "status-neutral") : ""}`}
                  role="status"
                >
                  <span className="status-symbol" aria-hidden="true">
                    {reveal ? signed(myDelta) : round?.my_choice ? "✓" : "→"}
                  </span>
                  <div>
                    <strong>
                      {reveal
                        ? round.my_choice
                          ? myDelta > 0
                            ? "That’s a good guess."
                            : myDelta < 0
                              ? "Luck has a sense of humor."
                              : "A clean slate."
                          : "Sat this one out."
                        : game.pendingBox !== null
                          ? "Making it official…"
                          : round?.my_choice
                            ? `Box 0${round.my_choice}. No second guessing.`
                            : game.secondsLeft <= 0
                              ? "Pencils down."
                              : "Go on. Pick a box."}
                    </strong>
                    <span>
                      {reveal
                        ? round.number === 5
                          ? "The final tally is coming up."
                          : "Next round, fresh possibilities."
                        : game.pendingBox !== null
                          ? "Waiting for confirmation."
                          : round?.my_choice
                            ? "Everyone’s picks stay secret until the reveal."
                            : game.secondsLeft <= 0
                              ? "Waiting for the server’s reveal."
                              : "Your first choice is your final choice."}
                    </span>
                  </div>
                </div>
                <Scoreboard game={game} />
              </section>
            ) : (
              <section className="final-layout">
                <div className="winner-poster">
                  <span className="eyebrow">
                    That’s a wrap · Room {room.code}
                  </span>
                  <h1>
                    Well
                    <br />
                    played<span>.</span>
                  </h1>
                  <div className="winner-stamp" aria-hidden="true">
                    <span>{winners.length > 1 ? "SHARED" : "TOP"}</span>
                    <strong>
                      {winners.length
                        ? (room.game?.scores[winners[0].user_id] ?? 0)
                        : "—"}
                    </strong>
                    <span>POINTS</span>
                  </div>
                  <div className="winner-caption">
                    <span className="eyebrow">
                      {winners.length > 1
                        ? "A tie worth celebrating"
                        : "The luckiest at the table"}
                    </span>
                    <p className="winner-name">
                      {winners.length
                        ? winners
                            .map((player) => player.display_name)
                            .join(" & ")
                        : "Results are in"}
                    </p>
                    <p>
                      {winners.some((player) => player.user_id === selected.id)
                        ? "Looks like your instincts were onto something."
                        : "Good company. Good game. There’s always next time."}
                    </p>
                  </div>
                </div>
                <div className="results-desk">
                  <Scoreboard game={game} final />
                  <p className="results-note">
                    Five rounds. Three boxes.
                    <br />A few very good guesses.
                  </p>
                  <button
                    className="button button-primary wide"
                    onClick={game.goHome}
                    disabled={!!busy}
                  >
                    Back to home<span aria-hidden="true">↗</span>
                  </button>
                </div>
              </section>
            )}
          </>
        )}
      </main>
      <footer>
        <span>
          LUCKY ROOMS<span className="accent">.</span>
        </span>
        <span>Good luck is better together.</span>
        <span className="footer-edition">THE DEMO EDITION / 001</span>
      </footer>
    </div>
  );
}
