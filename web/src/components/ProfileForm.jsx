import { useState } from 'react'

const STATES = [
  'AL','AK','AZ','AR','CA','CO','CT','DE','FL','GA','HI','ID','IL','IN','IA',
  'KS','KY','LA','ME','MD','MA','MI','MN','MS','MO','MT','NE','NV','NH','NJ',
  'NM','NY','NC','ND','OH','OK','OR','PA','RI','SC','SD','TN','TX','UT','VT',
  'VA','WA','WV','WI','WY','DC',
]

// The scale travels with the number, always -- an unlabeled GPA is
// meaningless, so the form will not let one be submitted.
const SCALES = [
  { value: 'unweighted', label: 'Unweighted (max 4.0)' },
  { value: 'weighted', label: 'Weighted (runs above 4.0)' },
  { value: 'uc_weighted_capped', label: 'UC weighted-capped' },
]

export default function ProfileForm({ onSubmit, busy }) {
  const [form, setForm] = useState({
    state: 'GA',
    family_income: '28000',
    family_size: '4',
    gpa_value: '',
    gpa_scale: 'unweighted',
    class_rank_percentile: '',
    applicant_cycle: '',
  })

  const set = (key) => (event) =>
    setForm((prev) => ({ ...prev, [key]: event.target.value }))

  function submit(event) {
    event.preventDefault()
    const profile = {
      state: form.state,
      family_income: Number(form.family_income),
      family_size: Number(form.family_size),
    }
    if (form.gpa_value !== '') {
      profile.gpa_value = form.gpa_value
      profile.gpa_scale = form.gpa_scale
    }
    if (form.class_rank_percentile !== '') {
      profile.class_rank_percentile = form.class_rank_percentile
    }
    if (form.applicant_cycle !== '') {
      profile.applicant_cycle = form.applicant_cycle
    }
    onSubmit(profile)
  }

  return (
    <form className="profile-form" onSubmit={submit}>
      <div className="field">
        <label htmlFor="state">Home state</label>
        <select id="state" value={form.state} onChange={set('state')}>
          {STATES.map((s) => (
            <option key={s}>{s}</option>
          ))}
        </select>
      </div>

      <div className="field">
        <label htmlFor="income">Family income (yearly, $)</label>
        <input
          id="income" type="number" min="0" step="1000" required
          value={form.family_income} onChange={set('family_income')}
        />
      </div>

      <div className="field">
        <label htmlFor="size">Family size</label>
        <input
          id="size" type="number" min="1" max="15" required
          value={form.family_size} onChange={set('family_size')}
        />
      </div>

      <div className="field">
        <label htmlFor="gpa">GPA (optional)</label>
        <input
          id="gpa" type="number" step="0.01" min="0" max="6"
          placeholder="e.g. 3.6"
          value={form.gpa_value} onChange={set('gpa_value')}
        />
      </div>

      <div className="field">
        <label htmlFor="scale">GPA scale</label>
        <select
          id="scale" value={form.gpa_scale} onChange={set('gpa_scale')}
          disabled={form.gpa_value === ''}
        >
          {SCALES.map((s) => (
            <option key={s.value} value={s.value}>{s.label}</option>
          ))}
        </select>
      </div>

      <div className="field">
        <label htmlFor="rank">Class rank: top N% (optional)</label>
        <input
          id="rank" type="number" step="0.1" min="0.1" max="100"
          placeholder="e.g. 5 for top 5%"
          value={form.class_rank_percentile}
          onChange={set('class_rank_percentile')}
        />
      </div>

      <div className="field">
        <label htmlFor="cycle">Applying for (optional)</label>
        <select id="cycle" value={form.applicant_cycle} onChange={set('applicant_cycle')}>
          <option value="">not sure yet</option>
          <option value="fall-2026">Fall 2026</option>
          <option value="fall-2027">Fall 2027</option>
        </select>
      </div>

      <button type="submit" disabled={busy}>
        {busy ? 'Checking…' : 'See my schools'}
      </button>
    </form>
  )
}
