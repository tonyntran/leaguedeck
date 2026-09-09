import { useEffect, useState } from 'react'
import { getLeagues, getSyncStatus, getWaiverWire, triggerSync } from '../api/client'

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
        return (
          <div className="ld-roster-row" key={player.player_id}>
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

export default function DashboardPage() {
  const [leagues, setLeagues] = useState(null)
  const [syncStatus, setSyncStatus] = useState([])
  const [error, setError] = useState(null)
  const [syncing, setSyncing] = useState(false)
  const [syncError, setSyncError] = useState(null)
  const [waiverWire, setWaiverWire] = useState({})
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
  }

  useEffect(loadData, [])

  // Waiver wire (trending adds) doesn't need a 60s refresh -- it's a "nice
  // to have" list that doesn't move that fast, and re-fetching it this
  // often would poll Sleeper's live trending/players endpoints regardless
  // of whether a game is actually live, undermining the point of gating
  // the backend's own fast sync to likely-live windows. Scores and sync
  // status are both cheap local reads, so they're the only things this
  // timer refreshes.
  useEffect(() => {
    function refreshScores() {
      Promise.all([getLeagues(), getSyncStatus()])
        .then(([leaguesData, statusData]) => {
          setLeagues(leaguesData)
          setSyncStatus(statusData)
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
              <article className="ld-team-card ld-mine" key={league.id}>
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
                <div className="ld-lineup-section">
                  <h3 className="ld-lineup-label">Starting lineup</h3>
                  <RosterList players={starters} />
                </div>
                <div className="ld-lineup-section ld-bench">
                  <h3 className="ld-lineup-label">Bench</h3>
                  <RosterList players={bench} />
                </div>
                {league.platform === 'sleeper' && (
                <div className="ld-lineup-section">
                  <h3 className="ld-lineup-label">Waiver wire</h3>
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
                      return (
                        <div className="ld-waiver-row" key={player.player_id}>
                          <span className="ld-pos">{player.position}</span>
                          <span className="ld-nm">{player.name}</span>
                          <span className="ld-tm">{player.team}</span>
                          <span className="ld-pts">{pts || ' '}</span>
                          <span className="ld-trend">{player.trend_count}</span>
                        </div>
                      )
                    })
                  )}
                </div>
                )}
              </article>
            )
          })}
        </div>
      </div>
    </div>
  )
}
