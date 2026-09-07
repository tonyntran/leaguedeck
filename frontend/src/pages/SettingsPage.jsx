import { useEffect, useState } from 'react'
import { getSleeperSettings, putSleeperSettings } from '../api/client'

export default function SettingsPage() {
  const [username, setUsername] = useState('')
  const [leagueIdsText, setLeagueIdsText] = useState('')
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    getSleeperSettings().then((data) => {
      setUsername(data.username)
      setLeagueIdsText(data.league_ids.join(', '))
    })
  }, [])

  async function handleSubmit(e) {
    e.preventDefault()
    const leagueIds = leagueIdsText
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean)
    await putSleeperSettings(username, leagueIds)
    setSaved(true)
  }

  return (
    <form onSubmit={handleSubmit}>
      <h1>Settings</h1>
      <label htmlFor="username">Sleeper username</label>
      <input id="username" value={username} onChange={(e) => setUsername(e.target.value)} />
      <label htmlFor="leagueIds">Sleeper league IDs (comma-separated)</label>
      <input
        id="leagueIds"
        value={leagueIdsText}
        onChange={(e) => setLeagueIdsText(e.target.value)}
      />
      <button type="submit">Save</button>
      {saved && <p>Saved.</p>}
    </form>
  )
}
