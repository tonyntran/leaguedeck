import { useEffect, useState } from 'react'
import {
  getEspnSettings,
  getSleeperSettings,
  getYahooAuthorizeUrl,
  getYahooSettings,
  putEspnSettings,
  putSleeperSettings,
  putYahooSettings,
  submitYahooAuthorizeCode,
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

  const [yahooClientId, setYahooClientId] = useState('')
  const [yahooClientSecret, setYahooClientSecret] = useState('')
  const [yahooLeagueIdsText, setYahooLeagueIdsText] = useState('')
  const [yahooClientSecretConfigured, setYahooClientSecretConfigured] = useState(false)
  const [yahooAuthorized, setYahooAuthorized] = useState(false)
  const [yahooCode, setYahooCode] = useState('')
  const [yahooSaved, setYahooSaved] = useState(false)
  const [yahooError, setYahooError] = useState(null)

  function loadYahooSettings() {
    return getYahooSettings()
      .then((data) => {
        setYahooClientId(data.client_id)
        setYahooLeagueIdsText(data.league_ids.join(', '))
        setYahooClientSecretConfigured(data.client_secret_configured)
        setYahooAuthorized(data.authorized)
      })
      .catch((err) => {
        setYahooError(
          err.status === 401
            ? 'Your session expired. Log in again to load settings.'
            : 'Could not load settings.',
        )
      })
  }

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

    loadYahooSettings()
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

  async function handleYahooSubmit(e) {
    e.preventDefault()
    const leagueIds = yahooLeagueIdsText
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean)
    setYahooError(null)
    setYahooSaved(false)
    try {
      await putYahooSettings(leagueIds, yahooClientId, yahooClientSecret)
      setYahooSaved(true)
      setYahooClientSecret('')
      await loadYahooSettings()
    } catch (err) {
      setYahooError(
        err.status === 401
          ? 'Your session expired. Log in again to save settings.'
          : 'Could not save settings.',
      )
    }
  }

  async function handleYahooAuthorizeClick() {
    setYahooError(null)
    try {
      const { url } = await getYahooAuthorizeUrl()
      window.open(url, '_blank', 'noopener,noreferrer')
    } catch (err) {
      setYahooError('Could not start Yahoo authorization. Save your Yahoo credentials first.')
    }
  }

  async function handleYahooCodeSubmit(e) {
    e.preventDefault()
    setYahooError(null)
    try {
      await submitYahooAuthorizeCode(yahooCode)
      setYahooCode('')
      await loadYahooSettings()
    } catch (err) {
      setYahooError('Could not verify that code with Yahoo. Try authorizing again.')
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

      <form className="ld-settings-form" onSubmit={handleYahooSubmit}>
        <label htmlFor="yahooClientId">Yahoo Client ID</label>
        <input
          id="yahooClientId"
          className="ld-input"
          value={yahooClientId}
          onChange={(e) => setYahooClientId(e.target.value)}
        />
        <label htmlFor="yahooClientSecret">
          Yahoo Client Secret{' '}
          {yahooClientSecretConfigured && '(already saved — leave blank to keep it)'}
        </label>
        <input
          id="yahooClientSecret"
          type="password"
          className="ld-input"
          value={yahooClientSecret}
          onChange={(e) => setYahooClientSecret(e.target.value)}
        />
        <label htmlFor="yahooLeagueIds">Yahoo league IDs (comma-separated)</label>
        <input
          id="yahooLeagueIds"
          className="ld-input"
          value={yahooLeagueIdsText}
          onChange={(e) => setYahooLeagueIdsText(e.target.value)}
        />
        <button type="submit" className="ld-button">
          Save
        </button>
        {yahooSaved && <p className="ld-saved">Saved.</p>}

        {yahooClientSecretConfigured && (
          <>
            <p className="ld-saved">{yahooAuthorized ? 'Connected ✓' : 'Not yet connected.'}</p>
            <button type="button" className="ld-button" onClick={handleYahooAuthorizeClick}>
              Authorize with Yahoo
            </button>
            <label htmlFor="yahooCode">Verification code from Yahoo</label>
            <input
              id="yahooCode"
              className="ld-input"
              value={yahooCode}
              onChange={(e) => setYahooCode(e.target.value)}
            />
            <button type="button" className="ld-button" onClick={handleYahooCodeSubmit}>
              Submit code
            </button>
          </>
        )}

        {yahooError && (
          <p role="alert" className="ld-error">
            {yahooError}
          </p>
        )}
      </form>
    </div>
  )
}
