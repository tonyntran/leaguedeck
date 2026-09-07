import { useEffect, useState } from 'react'
import { getLeagues, getSyncStatus } from '../api/client'

const STALE_THRESHOLD_MINUTES = 60

function isDegraded(status) {
  if (status.last_success === false) return true
  if (!status.last_success_at) return true
  const ageMinutes = (Date.now() - new Date(status.last_success_at).getTime()) / 60000
  return ageMinutes > STALE_THRESHOLD_MINUTES
}

export default function DashboardPage() {
  const [leagues, setLeagues] = useState(null)
  const [syncStatus, setSyncStatus] = useState([])
  const [error, setError] = useState(null)

  useEffect(() => {
    getLeagues()
      .then(setLeagues)
      .catch(() => setError('Could not load leagues. Log in and configure Settings first.'))
    getSyncStatus()
      .then(setSyncStatus)
      .catch(() => {})
  }, [])

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
        <p className="ld-sub">your leagues</p>
      </div>
      {degradedPlatforms.map((status) => (
        <p key={status.platform} role="alert" className="ld-alert">
          {status.platform} last synced{' '}
          {status.last_success_at ? new Date(status.last_success_at).toLocaleString() : 'never'}
          {status.last_error ? ` — ${status.last_error}` : ''} — check Settings.
        </p>
      ))}
      <div className="ld-leagues">
        {leagues.map((league) => (
          <section key={league.id}>
            <div className="ld-section-head">
              <span className="ld-name">
                {league.name} ({league.platform}, {league.season})
              </span>
              <hr />
              {league.teams[0]?.week != null && (
                <span className="ld-wk">week {league.teams[0].week}</span>
              )}
            </div>
            {league.teams.map((team) => (
              <article key={team.id}>
                <div className={`ld-dotline ${team.is_mine ? 'ld-you' : 'ld-riv'}`}>
                  <span className="ld-lbl">
                    {team.name}
                    {team.is_mine ? ' (you)' : ''} vs {team.opponent_name ?? 'TBD'}
                  </span>
                  <span className="ld-dots" />
                  <span className="ld-val">
                    {team.points_for} – {team.opponent_points ?? '-'}
                  </span>
                </div>
                <div className="ld-roster">
                  {team.roster.map((player) => (
                    <div className="ld-dotline" key={player.player_id}>
                      <span className="ld-lbl">
                        <span className="ld-pos">{player.position}</span>
                        {player.name}
                      </span>
                      <span className="ld-dots" />
                      <span className="ld-val">{player.team}</span>
                    </div>
                  ))}
                </div>
              </article>
            ))}
          </section>
        ))}
      </div>
    </div>
  )
}
