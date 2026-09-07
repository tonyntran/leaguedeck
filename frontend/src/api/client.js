const BASE_URL = 'http://localhost:8000'

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

export function getLeagues() {
  return request('/leagues')
}

export function getSyncStatus() {
  return request('/sync-status')
}
