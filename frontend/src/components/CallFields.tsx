// Provides editable status, summary, and rating fields for a call.
import { statuses } from '../utils/data.ts'
export function CallFields({ status, setStatus, summary, setSummary, rating, setRating }) {
  return (
    <>
      <label>
        Call status
        <select
          value={status}
          onChange={(e) => {
            setStatus(e.currentTarget.value)
            if (e.currentTarget.value !== 'completed') setRating('')
          }}
        >
          {statuses.map((s) => (
            <option value={s} key={s}>
              {s[0].toUpperCase() + s.slice(1)}
            </option>
          ))}
        </select>
      </label>
      <label>
        Conversation summary
        <textarea
          value={summary}
          onInput={(e) => setSummary(e.currentTarget.value)}
          placeholder="What did you discuss? What comes next?"
        />
      </label>
      <label>
        Conversation rating
        <select
          value={rating ?? ''}
          onChange={(e) => setRating(e.currentTarget.value)}
          disabled={status !== 'completed'}
        >
          <option value="">Not rated</option>
          {[1, 2, 3, 4, 5].map((n) => (
            <option value={n} key={n}>
              {n} / 5{n === 5 ? ' · Excellent' : n === 1 ? ' · Needs improvement' : ''}
            </option>
          ))}
        </select>
      </label>
      <p class="form-help">
        Keep a summary for any call status. Ratings apply only to completed calls and are cleared
        when you choose another status.
      </p>
    </>
  )
}
