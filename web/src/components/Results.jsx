import SchoolCard from './SchoolCard'

// Rendering order inside "on your list": strongest honest claim first.
const CATEGORY_ORDER = [
  'guaranteed', 'likely', 'target', 'reach', 'holistic_review',
  'context_not_placed', 'unable_to_assess_on_gpa', 'unable_to_assess',
]

function byCategory(a, b) {
  return (
    CATEGORY_ORDER.indexOf(a.admissions.category) -
    CATEGORY_ORDER.indexOf(b.admissions.category)
  )
}

function money(value) {
  const sign = value < 0 ? '−' : ''
  return `${sign}$${Math.abs(Math.round(value)).toLocaleString()}`
}

function Ceiling({ ceiling }) {
  return (
    <section className="ceiling">
      <h2>What you can plausibly pay: {money(ceiling.ceiling)}/year</h2>
      <p>
        Here is the math, not a verdict from nowhere &mdash; family
        contribution <strong>{money(ceiling.family_term)}</strong> (10% of
        income above twice the poverty line, saved over ten years, spread
        across college) + student work{' '}
        <strong>{money(ceiling.work_term)}</strong> (500 hours/year at{' '}
        {ceiling.wage_is_federal_fallback
          ? 'the federal minimum wage'
          : `${ceiling.wage_state_used}'s minimum wage`}
        ) + federal subsidized loan <strong>{money(ceiling.loan_term)}</strong>.
      </p>
      <p className="fine-print">
        The family term is a policy benchmark for what college should cost a
        family like yours &mdash; it is not a claim about your bank account.
      </p>
    </section>
  )
}

export default function Results({ result }) {
  const { ceiling, on_your_list, not_on_your_list } = result
  const pricedOut = not_on_your_list.filter(
    (a) => a.affordability.verdict === 'unaffordable'
  )
  const unknownCost = not_on_your_list.filter(
    (a) => a.affordability.verdict === 'unknown'
  )

  return (
    <div className="results">
      <Ceiling ceiling={ceiling} />

      <section>
        <h2>On your list ({on_your_list.length})</h2>
        <p className="section-note">
          Schools whose published net price for your income band fits under
          your ceiling. Each carries its own separate admissions read &mdash;
          affordability and admissions never merge into one score.
        </p>
        {on_your_list.length === 0 && (
          <p className="empty">
            Nothing on the current roster fits under your ceiling. That is a
            finding about published prices, not about you.
          </p>
        )}
        {[...on_your_list].sort(byCategory).map((a) => (
          <SchoolCard key={a.school_name} assessment={a} />
        ))}
      </section>

      <section>
        <h2>Not on your list, and why ({not_on_your_list.length})</h2>
        <p className="section-note">
          Nothing here is hidden or scored down &mdash; each school keeps its
          admissions read and states exactly why it is excluded.
        </p>

        {pricedOut.length > 0 && (
          <>
            <h3 className="subhead">
              Priced out &mdash; published cost exceeds your ceiling
            </h3>
            {[...pricedOut].sort(byCategory).map((a) => (
              <SchoolCard key={a.school_name} assessment={a} />
            ))}
          </>
        )}

        {unknownCost.length > 0 && (
          <>
            <h3 className="subhead">
              Cost unknown &mdash; no published price for your situation
            </h3>
            {[...unknownCost].sort(byCategory).map((a) => (
              <SchoolCard key={a.school_name} assessment={a} />
            ))}
          </>
        )}
      </section>
    </div>
  )
}
