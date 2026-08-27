// The one place the frontend talks to the backend.

export async function fetchMatch(profile) {
  const response = await fetch('/api/match', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(profile),
  })
  const body = await response.json()
  if (!response.ok) {
    // 422s carry the engine's own explanation (e.g. "an unweighted GPA
    // cannot exceed 4.0"); surface it verbatim.
    const detail =
      typeof body.detail === 'string'
        ? body.detail
        : body.detail?.map((d) => d.msg).join('; ') || 'request failed'
    throw new Error(detail)
  }
  return body
}
