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
    <form onSubmit={handleSubmit}>
      <h1>LeagueDeck</h1>
      <label htmlFor="password">Password</label>
      <input
        id="password"
        type="password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
      />
      <button type="submit">Log in</button>
      {error && <p>{error}</p>}
    </form>
  )
}
