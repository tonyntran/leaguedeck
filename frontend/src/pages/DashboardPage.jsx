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

  if (error) return <p>{error}</p>
  if (!leagues) return <p>Loading...</p>

  const degradedPlatforms = syncStatus.filter(isDegraded)

  return (
    <div>
      <h1>My Leagues</h1>
      {degradedPlatforms.map((status) => (
        <p key={status.platform} role="alert">
          {status.platform} last synced{' '}
          {status.last_success_at ? new Date(status.last_success_at).toLocaleString() : 'never'}
          {status.last_error ? ` — ${status.last_error}` : ''} — check Settings.
        </p>
      ))}
      {leagues.map((league) => (
        <section key={league.id}>
          <h2>
            {league.name} ({league.platform}, {league.season})
          </h2>
          {league.teams.map((team) => (
            <article key={team.id}>
              <h3>
                {team.name} {team.is_mine ? '(mine)' : ''}
              </h3>
              <p>
                Week {team.week}: {team.points_for} pts vs {team.opponent_name ?? 'TBD'} (
                {team.opponent_points ?? '-'} pts)
              </p>
              <ul>
                {team.roster.map((player) => (
                  <li key={player.player_id}>
                    {player.name} — {player.position} ({player.team})
                  </li>
                ))}
              </ul>
            </article>
          ))}
        </section>
      ))}
    </div>
  )
}
