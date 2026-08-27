import { useState } from 'react'
import { fetchMatch } from './api'
import ProfileForm from './components/ProfileForm'
import Results from './components/Results'

export default function App() {
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  async function handleSubmit(profile) {
    setBusy(true)
    setError(null)
    try {
      setResult(await fetchMatch(profile))
    } catch (exc) {
      setResult(null)
      setError(exc.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="app">
      <header className="masthead">
        <h1>Pontis</h1>
        <p>
          Which colleges can you both <em>get into</em> and <em>afford</em>?
          Two separate answers per school &mdash; sticker prices lie, and
          Pontis never blends the two into a score.
        </p>
      </header>

      <ProfileForm onSubmit={handleSubmit} busy={busy} />

      {error && <p className="error" role="alert">{error}</p>}
      {result && <Results result={result} />}

      {!result && !error && (
        <p className="empty">
          Enter what you know &mdash; every field beyond state, income, and
          family size is optional, and Pontis will say honestly what it can
          and cannot assess from what you give it.
        </p>
      )}
    </div>
  )
}
