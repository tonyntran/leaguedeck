import { useEffect, useState } from 'react'
import { getSleeperSettings, putSleeperSettings } from '../api/client'

export default function SettingsPage() {
  const [username, setUsername] = useState('')
  const [leagueIdsText, setLeagueIdsText] = useState('')
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    getSleeperSettings()
      .then((data) => {
        setUsername(data.username)
        setLeagueIdsText(data.league_ids.join(', '))
      })
      .catch((err) => {
        setError(
          err.status === 401
            ? 'Your session expired. Log in again to load settings.'
            : 'Could not load settings.',
        )
      })
  }, [])

  async function handleSubmit(e) {
    e.preventDefault()
    const leagueIds = leagueIdsText
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean)
    setError(null)
    setSaved(false)
    try {
      await putSleeperSettings(username, leagueIds)
      setSaved(true)
    } catch (err) {
      setError(
        err.status === 401
          ? 'Your session expired. Log in again to save settings.'
          : 'Could not save settings.',
      )
    }
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
        {error && (
          <p role="alert" className="ld-error">
            {error}
          </p>
        )}
      </form>
    </div>
  )
}
