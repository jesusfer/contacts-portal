// Provides editable start, end, and notes fields for an appointment.
export function AppointmentFields({ starts, setStarts, ends, setEnds, notes, setNotes }) {
  return (
    <>
      <div class="form-grid">
        <label>
          Starts at
          <input
            type="datetime-local"
            value={starts}
            onInput={(e) => setStarts(e.currentTarget.value)}
            required
          />
        </label>
        <label>
          Ends at
          <input
            type="datetime-local"
            value={ends}
            onInput={(e) => setEnds(e.currentTarget.value)}
            required
            min={starts || undefined}
          />
        </label>
      </div>
      <p class="form-help">
        Times are shown in your local timezone ({Intl.DateTimeFormat().resolvedOptions().timeZone}).
        They are saved in UTC.
      </p>
      <label>
        Appointment notes
        <textarea
          value={notes}
          onInput={(e) => setNotes(e.currentTarget.value)}
          placeholder="An agenda, a reminder, or a little context…"
        />
      </label>
    </>
  )
}
