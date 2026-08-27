// One school, two verdicts, side by side -- never merged.

const CATEGORY_LABELS = {
  guaranteed: 'Guaranteed',
  likely: 'Likely',
  target: 'Target',
  reach: 'Reach',
  holistic_review: 'Holistic review',
  context_not_placed: 'Not placed yet',
  unable_to_assess: 'Unable to assess',
  unable_to_assess_on_gpa: 'No GPA signal',
}

const VERDICT_LABELS = {
  affordable: 'Affordable',
  unaffordable: 'Priced out',
  unknown: 'Cost unknown',
}

function money(value) {
  const sign = value < 0 ? '−' : ''
  return `${sign}$${Math.abs(Math.round(value)).toLocaleString()}`
}

export default function SchoolCard({ assessment }) {
  const { school_name, admissions, affordability } = assessment
  // "The school publishes no GPA" and "we have not ingested this school yet"
  // are different facts; the pill must not conflate them.
  const admissionsLabel =
    admissions.basis === 'no_data_ingested'
      ? 'No data yet'
      : CATEGORY_LABELS[admissions.category] || admissions.category
  return (
    <article className="school-card">
      <header>
        <h3>{school_name}</h3>
        <div className="pills">
          <span className={`pill admissions-${admissions.category}`}>
            {admissionsLabel}
          </span>
          <span className={`pill afford-${affordability.verdict}`}>
            {VERDICT_LABELS[affordability.verdict]}
            {affordability.net_price != null &&
              ` · ${money(affordability.net_price)}/yr`}
          </span>
        </div>
      </header>

      <p className="reason">{admissions.reason}</p>
      <p className="reason afford-reason">{affordability.reason}</p>

      {affordability.net_price != null && affordability.net_price < 0 && (
        <p className="callout good">
          This net price is <strong>negative</strong>: for families in your
          income band, grant aid at this school averaged more than the full
          cost of attending.
        </p>
      )}

      {admissions.next_step && (
        <p className="callout">{admissions.next_step}</p>
      )}

      {affordability.verdict === 'unknown' && (
        <p className="callout">
          Check this school&apos;s own net price calculator for a number that
          fits your situation &mdash; Pontis will not guess it for you.
        </p>
      )}
    </article>
  )
}
