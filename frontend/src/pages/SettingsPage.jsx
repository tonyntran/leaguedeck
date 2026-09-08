import { useEffect, useState } from 'react'
import {
  getEspnSettings,
  getSleeperSettings,
  putEspnSettings,
  putSleeperSettings,
} from '../api/client'

export default function SettingsPage() {
  const [username, setUsername] = useState('')
  const [leagueIdsText, setLeagueIdsText] = useState('')
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState(null)

  const [espnS2, setEspnS2] = useState('')
  const [swid, setSwid] = useState('')
  const [espnLeagueIdsText, setEspnLeagueIdsText] = useState('')
  const [espnS2Configured, setEspnS2Configured] = useState(false)
  const [swidConfigured, setSwidConfigured] = useState(false)
  const [espnSaved, setEspnSaved] = useState(false)
  const [espnError, setEspnError] = useState(null)

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

    getEspnSettings()
      .then((data) => {
        setEspnLeagueIdsText(data.league_ids.join(', '))
        setEspnS2Configured(data.espn_s2_configured)
        setSwidConfigured(data.swid_configured)
      })
      .catch((err) => {
        setEspnError(
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

  async function handleEspnSubmit(e) {
    e.preventDefault()
    const leagueIds = espnLeagueIdsText
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean)
    setEspnError(null)
    setEspnSaved(false)
    try {
      await putEspnSettings(leagueIds, espnS2, swid)
      setEspnSaved(true)
      // Cookie fields are never echoed back by GET -- clear them from the
      // form and rely on the "configured" labels to show they're stored.
      setEspnS2('')
      setSwid('')
      const data = await getEspnSettings()
      setEspnS2Configured(data.espn_s2_configured)
      setSwidConfigured(data.swid_configured)
    } catch (err) {
      setEspnError(
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

      <form className="ld-settings-form" onSubmit={handleEspnSubmit}>
        <label htmlFor="espnS2">
          ESPN espn_s2 cookie {espnS2Configured && '(already saved — leave blank to keep it)'}
        </label>
        <input
          id="espnS2"
          type="password"
          className="ld-input"
          value={espnS2}
          onChange={(e) => setEspnS2(e.target.value)}
        />
        <label htmlFor="swid">
          ESPN SWID cookie {swidConfigured && '(already saved — leave blank to keep it)'}
        </label>
        <input
          id="swid"
          type="password"
          className="ld-input"
          value={swid}
          onChange={(e) => setSwid(e.target.value)}
        />
        <label htmlFor="espnLeagueIds">ESPN league IDs (comma-separated)</label>
        <input
          id="espnLeagueIds"
          className="ld-input"
          value={espnLeagueIdsText}
          onChange={(e) => setEspnLeagueIdsText(e.target.value)}
        />
        <button type="submit" className="ld-button">
          Save
        </button>
        {espnSaved && <p className="ld-saved">Saved.</p>}
        {espnError && (
          <p role="alert" className="ld-error">
            {espnError}
          </p>
        )}
      </form>
    </div>
  )
}
