// Same-origin: nginx (prod) and the Vite dev-server proxy (dev) both forward
// the API paths to the backend, so relative URLs work over localhost, a LAN
// IP, or a Tailscale hostname alike.
const BASE_URL = ''

async function request(path, options = {}) {
  const resp = await fetch(`${BASE_URL}${path}`, {
    ...options,
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...options.headers },
  })
  if (!resp.ok) {
    const error = new Error(`Request to ${path} failed with ${resp.status}`)
    error.status = resp.status
    throw error
  }
  return resp.status === 204 ? null : resp.json()
}

export function login(password) {
  return request('/auth/login', { method: 'POST', body: JSON.stringify({ password }) })
}

export function logout() {
  return request('/auth/logout', { method: 'POST' })
}

export function getSleeperSettings() {
  return request('/settings/sleeper')
}

export function putSleeperSettings(username, leagueIds) {
  return request('/settings/sleeper', {
    method: 'PUT',
    body: JSON.stringify({ username, league_ids: leagueIds }),
  })
}

export function getEspnSettings() {
  return request('/settings/espn')
}

export function putEspnSettings(leagueIds, espnS2, swid) {
  const body = { league_ids: leagueIds }
  // Only send the cookie fields if the user actually typed something this
  // time -- the backend's optional-secret PUT contract treats a present-
  // but-blank field the same as an overwrite, so a league-IDs-only edit
  // must omit them entirely to avoid wiping stored credentials.
  if (espnS2) body.espn_s2 = espnS2
  if (swid) body.swid = swid
  return request('/settings/espn', {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}

export function getLeagues() {
  return request('/leagues')
}

export function getSyncStatus() {
  return request('/sync-status')
}

export function triggerSync() {
  return request('/sync-status/run', { method: 'POST' })
}

export function getWaiverWire(leagueId) {
  return request(`/leagues/${leagueId}/waiver-wire`)
}
