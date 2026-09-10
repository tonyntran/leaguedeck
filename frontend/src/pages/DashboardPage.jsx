import { useEffect, useState } from 'react'
import { getLeagues, getNflScores, getSyncStatus, getWaiverWire, triggerSync } from '../api/client'

const STALE_THRESHOLD_MINUTES = 60

// GET /leagues and /sync-status are both local DB reads (no external
// platform calls), so polling them this often is cheap year-round. What
// actually makes this refresh meaningful during games is the backend's own
// live-window sync cadence (see sync.py) -- this timer doesn't need to know
// whether a game is live, since re-fetching unchanged data off-hours costs
// nothing.
const LIVE_REFRESH_INTERVAL_MS = 60000
// A single dropped request (a blip, a slow response) shouldn't flash a
// warning -- only a sustained run of failures means the auto-refresh has
// actually stalled.
const REFRESH_FAILURE_THRESHOLD = 3

function isDegraded(status) {
  if (status.last_success === false) return true
  if (!status.last_success_at) return true
  const ageMinutes = (Date.now() - new Date(status.last_success_at).getTime()) / 60000
  return ageMinutes > STALE_THRESHOLD_MINUTES
}

// Standard fantasy roster order. We only know each player's real-world
// position (not which lineup slot — e.g. FLEX — they fill), so this can't be
// perfectly slot-accurate, but it keeps the conventional QB-first/K-last
// reading order instead of whatever order Sleeper's API happens to return.
const POSITION_ORDER = ['QB', 'RB', 'WR', 'TE', 'DEF', 'K']

function byPositionOrder(players) {
  return [...players].sort((a, b) => {
    const aIndex = POSITION_ORDER.indexOf(a.position)
    const bIndex = POSITION_ORDER.indexOf(b.position)
    const aRank = aIndex === -1 ? POSITION_ORDER.length : aIndex
    const bRank = bIndex === -1 ? POSITION_ORDER.length : bIndex
    return aRank - bRank
  })
}

// Neither platform reports a per-player score until that player's game has
// started, so "actual" and "projected" are each independently optional --
// pre-kickoff shows the projection alone, in-progress/final shows the
// actual (with the projection alongside, for comparison).
function formatPoints(actualPoints, projectedPoints) {
  const actual = actualPoints != null ? actualPoints.toFixed(1) : null
  const projected = projectedPoints != null ? projectedPoints.toFixed(1) : null
  if (actual != null && projected != null) return `${actual} (proj ${projected})`
  if (actual != null) return actual
  if (projected != null) return `${projected} proj`
  return null
}

function RosterList({ players }) {
  return (
    <div>
      {players.map((player) => {
        const pts = formatPoints(player.actual_points, player.projected_points)
        const isLive = player.actual_points != null
        return (
          <div className={`ld-roster-row${isLive ? ' ld-live' : ''}`} key={player.player_id}>
            <span className="ld-dot" />
            <span className="ld-pos">{player.position}</span>
            <span className="ld-nm">{player.name}</span>
            <span className="ld-tm">{player.team}</span>
            {/* Rendered even when empty (a non-breaking space, not "") so this
                column always reserves its width -- otherwise the team
                abbreviation before it would shift left/right depending on
                whether this row has points yet. */}
            <span className="ld-pts">{pts || ' '}</span>
          </div>
        )
      })}
    </div>
  )
}

// Starting lineup is always open; Bench and Waiver wire default collapsed
// so a card opens on what actually matters on gameday. Each section keeps
// its own open/closed state, independent of every other section and card.
function LineupSection({ title, defaultOpen, collapsible, children }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="ld-lineup-section">
      <h3
        className={`ld-lineup-label${collapsible ? ` ld-collapsible${open ? '' : ' ld-closed'}` : ''}`}
        onClick={collapsible ? () => setOpen((o) => !o) : undefined}
      >
        {collapsible && <span className="ld-chevron">▾</span>}
        {title}
      </h3>
      {open && children}
    </div>
  )
}

// A quick score check across every league without scrolling through full
// cards below -- jumps to the matching card by id.
function ScoreStrip({ leagues }) {
  const rows = leagues
    .map((league) => ({ league, myTeam: league.teams.find((t) => t.is_mine) }))
    .filter(({ myTeam }) => myTeam)
  if (rows.length === 0) return null
  return (
    <div className="ld-scorestrip">
      {rows.map(({ league, myTeam }) => (
        <a className="ld-score-chip" key={league.id} href={`#league-card-${league.id}`}>
          <span className="ld-chip-name">
            {myTeam.name}
            <span className="ld-chip-league">
              {league.name}
              {myTeam.week != null ? ` · week ${myTeam.week}` : ''}
            </span>
          </span>
          <span className="ld-chip-score">
            {myTeam.points_for}
            <span className="ld-chip-vs">
              {myTeam.opponent_name ? `vs ${myTeam.opponent_name}` : 'no opponent'}
            </span>
          </span>
        </a>
      ))}
    </div>
  )
}

// A short list of only the games that actually involve one of your rostered
// players, so it stays scannable instead of turning into a full NFL
// scoreboard. Games that haven't kicked off or have already finished are
// filtered out server-side -- this rail only ever shows what's live now.
function LiveGamesRail({ games }) {
  return (
    <aside className="ld-live-rail">
      <h2 className="ld-live-rail-title">Live games</h2>
      {games.length === 0 ? (
        <p className="ld-status ld-live-empty">No games with your players are live right now.</p>
      ) : (
        games.map((game) => (
          <div className="ld-live-game" key={`${game.away_team}-${game.home_team}`}>
            <div className="ld-live-score">
              <span>
                {game.away_team} {game.away_score}
              </span>
              <span className="ld-live-at">at</span>
              <span>
                {game.home_team} {game.home_score}
              </span>
            </div>
            {game.detail && <p className="ld-live-detail">{game.detail}</p>}
            {[...game.away_players, ...game.home_players].map((player) => (
              <p className="ld-live-player" key={`${player.league_name}-${player.name}`}>
                {player.name}{' '}
                <span className="ld-live-player-meta">
                  {player.position} · {player.league_name}
                </span>
              </p>
            ))}
          </div>
        ))
      )}
    </aside>
  )
}

export default function DashboardPage() {
  const [leagues, setLeagues] = useState(null)
  const [syncStatus, setSyncStatus] = useState([])
  const [error, setError] = useState(null)
  const [syncing, setSyncing] = useState(false)
  const [syncError, setSyncError] = useState(null)
  const [waiverWire, setWaiverWire] = useState({})
  const [liveGames, setLiveGames] = useState([])
  const [refreshFailureCount, setRefreshFailureCount] = useState(0)

  function loadData() {
    getLeagues()
      .then((data) => {
        setLeagues(data)
        data.forEach((league) => {
          if (league.platform !== 'sleeper') return
          getWaiverWire(league.id)
            .then((players) => setWaiverWire((prev) => ({ ...prev, [league.id]: players })))
            .catch(() => setWaiverWire((prev) => ({ ...prev, [league.id]: [] })))
        })
      })
      .catch(() => setError('Could not load leagues. Log in and configure Settings first.'))
    getSyncStatus()
      .then(setSyncStatus)
      .catch(() => {})
    getNflScores()
      .then(setLiveGames)
      .catch(() => setLiveGames([]))
  }

  useEffect(loadData, [])

  // Waiver wire (trending adds) doesn't need a 60s refresh -- it's a "nice
  // to have" list that doesn't move that fast, and re-fetching it this
  // often would poll Sleeper's live trending/players endpoints regardless
  // of whether a game is actually live, undermining the point of gating
  // the backend's own fast sync to likely-live windows. Scores, sync
  // status, and live NFL games are all cheap local/cached reads, so
  // they're the only things this timer refreshes.
  useEffect(() => {
    function refreshScores() {
      Promise.all([getLeagues(), getSyncStatus(), getNflScores()])
        .then(([leaguesData, statusData, gamesData]) => {
          setLeagues(leaguesData)
          setSyncStatus(statusData)
          setLiveGames(gamesData)
          setRefreshFailureCount(0)
        })
        // A silently-stalled timer (session expired, network down) would
        // otherwise show old scores forever with no indication -- and
        // since no state changes, isDegraded below never even gets a
        // chance to recompute from fresh data. Surface it after a few
        // misses rather than on the first one, so a single dropped
        // request doesn't flash a warning.
        .catch(() => setRefreshFailureCount((n) => n + 1))
    }
    const interval = setInterval(refreshScores, LIVE_REFRESH_INTERVAL_MS)
    return () => clearInterval(interval)
  }, [])

  async function handleSyncNow() {
    setSyncing(true)
    setSyncError(null)
    try {
      await triggerSync()
      loadData()
    } catch (err) {
      setSyncError('Sync failed. Check Settings and try again.')
    } finally {
      setSyncing(false)
    }
  }

  if (error) {
    return (
      <div className="ld-page">
        <p className="ld-status">{error}</p>
      </div>
    )
  }
  if (!leagues) {
    return (
      <div className="ld-page">
        <p className="ld-status">Loading...</p>
      </div>
    )
  }

  const degradedPlatforms = syncStatus.filter(isDegraded)

  return (
    <div className="ld-page">
      <div className="ld-cover-band">
        <h1 className="ld-title">LeagueDeck</h1>
        <p className="ld-sub">your teams</p>
      </div>
      <button className="ld-button" onClick={handleSyncNow} disabled={syncing}>
        {syncing ? 'Syncing…' : 'Sync now'}
      </button>
      {syncError && (
        <p role="alert" className="ld-error">
          {syncError}
        </p>
      )}
      {degradedPlatforms.map((status) => (
        <p key={status.platform} role="alert" className="ld-alert">
          {status.platform} last synced{' '}
          {status.last_success_at ? new Date(status.last_success_at).toLocaleString() : 'never'}
          {status.last_error ? ` — ${status.last_error}` : ''} — check Settings.
        </p>
      ))}
      {refreshFailureCount >= REFRESH_FAILURE_THRESHOLD && (
        <p role="alert" className="ld-alert">
          Live updates paused — check your connection, or log in again if your session expired.
        </p>
      )}
      <ScoreStrip leagues={leagues} />
      <div className="ld-dash-layout">
        <div className="ld-leagues">
          <div className="ld-team-grid">
            {leagues.map((league) => {
              const myTeam = league.teams.find((t) => t.is_mine)
              if (!myTeam) {
                return (
                  <article className="ld-team-card" key={league.id}>
                    <div className="ld-card-head">
                      <span className="ld-card-name">{league.name}</span>
                    </div>
                    <p className="ld-status">Could not find your team in this league.</p>
                  </article>
                )
              }

              const starters = byPositionOrder(myTeam.roster.filter((p) => p.is_starter))
              const bench = byPositionOrder(myTeam.roster.filter((p) => !p.is_starter))

              // Sleeper's web app resolves "/team" to the logged-in user's own
              // team within that league — no roster_id needed. ESPN has no such
              // shorthand: its team page URL requires the numeric team ID
              // alongside the league ID, which is exactly what platform_team_id
              // already stores for ESPN leagues.
              const teamUrl =
                league.platform === 'sleeper' && league.platform_league_id
                  ? `https://sleeper.com/leagues/${league.platform_league_id}/team`
                  : league.platform === 'espn' && league.platform_league_id && myTeam.platform_team_id
                    ? `https://fantasy.espn.com/football/team?leagueId=${league.platform_league_id}&teamId=${myTeam.platform_team_id}`
                    : league.platform === 'yahoo' && league.platform_league_id && myTeam.platform_team_id
                      ? `https://football.fantasysports.yahoo.com/f1/${league.platform_league_id}/${myTeam.platform_team_id.split('.').pop()}`
                      : null
              const CardHeadTag = teamUrl ? 'a' : 'div'

              return (
                <article className="ld-team-card ld-mine" key={league.id} id={`league-card-${league.id}`}>
                  <CardHeadTag
                    className="ld-card-head"
                    {...(teamUrl ? { href: teamUrl, target: '_blank', rel: 'noopener noreferrer' } : {})}
                  >
                    <span className="ld-card-name">
                      {myTeam.name} — {league.name}
                      {myTeam.week != null ? ` (week ${myTeam.week})` : ''}
                    </span>
                    <span className="ld-card-score">
                      {myTeam.points_for} – {myTeam.opponent_points ?? '-'}
                      {myTeam.opponent_name ? ` vs ${myTeam.opponent_name}` : ''}
                    </span>
                  </CardHeadTag>
                  <LineupSection title="Starting lineup" defaultOpen>
                    <RosterList players={starters} />
                  </LineupSection>
                  <LineupSection title={`Bench (${bench.length})`} defaultOpen={false} collapsible>
                    <RosterList players={bench} />
                  </LineupSection>
                  {league.platform === 'sleeper' && (
                    <LineupSection
                      title={`Waiver wire${waiverWire[league.id] ? ` (${Math.min(waiverWire[league.id].length, 10)})` : ''}`}
                      defaultOpen={false}
                      collapsible
                    >
                      {waiverWire[league.id] === undefined ? (
                        <p className="ld-status">Loading…</p>
                      ) : waiverWire[league.id].length === 0 ? (
                        <p className="ld-status">No trending players available right now.</p>
                      ) : (
                        /* Display cap: the backend returns up to 25 trending adds
                           and typically 10-18 survive the rostered filter, which
                           would make this the tallest section on the card. Purely
                           a display concern — the response shape is unchanged. */
                        waiverWire[league.id].slice(0, 10).map((player) => {
                          const pts = formatPoints(player.actual_points, player.projected_points)
                          const isLive = player.actual_points != null
                          return (
                            <div className={`ld-waiver-row${isLive ? ' ld-live' : ''}`} key={player.player_id}>
                              <span className="ld-dot" />
                              <span className="ld-pos">{player.position}</span>
                              <span className="ld-nm">{player.name}</span>
                              <span className="ld-tm">{player.team}</span>
                              <span className="ld-pts">{pts || ' '}</span>
                              <span className="ld-trend">{player.trend_count}</span>
                            </div>
                          )
                        })
                      )}
                    </LineupSection>
                  )}
                </article>
              )
            })}
          </div>
        </div>
        <LiveGamesRail games={liveGames} />
      </div>
    </div>
  )
}
