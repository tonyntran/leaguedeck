import { useEffect, useState } from 'react'
import { getLeagues, getSyncStatus, getWaiverWire, triggerSync } from '../api/client'

const STALE_THRESHOLD_MINUTES = 60

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

function RosterList({ players }) {
  return (
    <div>
      {players.map((player) => (
        <div className="ld-roster-row" key={player.player_id}>
          <span className="ld-pos">{player.position}</span>
          <span className="ld-nm">{player.name}</span>
          <span className="ld-tm">{player.team}</span>
        </div>
      ))}
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

  function loadData() {
    getLeagues()
      .then((data) => {
        setLeagues(data)
        data.forEach((league) => {
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

            return (
              <article className="ld-team-card ld-mine" key={league.id}>
                <div className="ld-card-head">
                  <span className="ld-card-name">
                    {myTeam.name} — {league.name}
                    {myTeam.week != null ? ` (week ${myTeam.week})` : ''}
                  </span>
                  <span className="ld-card-score">
                    {myTeam.points_for} – {myTeam.opponent_points ?? '-'}
                    {myTeam.opponent_name ? ` vs ${myTeam.opponent_name}` : ''}
                  </span>
                </div>
                <div className="ld-lineup-section">
                  <h3 className="ld-lineup-label">Starting lineup</h3>
                  <RosterList players={starters} />
                </div>
                <div className="ld-lineup-section ld-bench">
                  <h3 className="ld-lineup-label">Bench</h3>
                  <RosterList players={bench} />
                </div>
                <div className="ld-lineup-section">
                  <h3 className="ld-lineup-label">Waiver wire</h3>
                  {waiverWire[league.id] === undefined ? (
                    <p className="ld-status">Loading…</p>
                  ) : waiverWire[league.id].length === 0 ? (
                    <p className="ld-status">No trending players available right now.</p>
                  ) : (
                    waiverWire[league.id].map((player) => (
                      <div className="ld-waiver-row" key={player.player_id}>
                        <span className="ld-pos">{player.position}</span>
                        <span className="ld-nm">{player.name}</span>
                        <span className="ld-tm">{player.team}</span>
                        <span className="ld-trend">{player.trend_count}</span>
                      </div>
                    ))
                  )}
                </div>
              </article>
            )
          })}
        </div>
      </div>
    </div>
  )
}
