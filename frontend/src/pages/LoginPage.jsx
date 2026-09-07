import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { login } from '../api/client'

export default function LoginPage() {
  const [password, setPassword] = useState('')
  const [error, setError] = useState(null)
  const navigate = useNavigate()

  async function handleSubmit(e) {
    e.preventDefault()
    setError(null)
    try {
      await login(password)
      navigate('/')
    } catch (err) {
      setError('Incorrect password')
    }
  }

  return (
    <div className="ld-login-screen">
      <div className="ld-cover-band">
        <h1 className="ld-wordmark">LeagueDeck</h1>
        <p className="ld-tagline">the gameday program, delivered</p>
      </div>
      <div className="ld-login-content">
        <form className="ld-login-card" onSubmit={handleSubmit}>
          <label htmlFor="password" className="sr-only">
            Password
          </label>
          <input
            id="password"
            className="ld-input"
            type="password"
            placeholder="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <button type="submit" className="ld-button">
            Log in
          </button>
          {error && <p className="ld-error">{error}</p>}
        </form>
      </div>
    </div>
  )
}
