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
    <div className="ld-page">
      <div className="ld-cover-band">
        <h1 className="ld-title">LeagueDeck</h1>
        <p className="ld-sub">settings</p>
      </div>
      <form className="ld-settings-form" onSubmit={handleSubmit}>
        <label htmlFor="username">Sleeper username</label>
        <input
          id="username"
          className="ld-input"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
        />
        <label htmlFor="leagueIds">Sleeper league IDs (comma-separated)</label>
        <input
          id="leagueIds"
          className="ld-input"
          value={leagueIdsText}
          onChange={(e) => setLeagueIdsText(e.target.value)}
        />
        <button type="submit" className="ld-button">
          Save
        </button>
        {saved && <p className="ld-saved">Saved.</p>}
      </form>
    </div>
  )
}
