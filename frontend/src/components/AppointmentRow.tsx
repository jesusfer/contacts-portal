// Displays an appointment summary row linked to its detail page.
import { date, time } from '../utils/data.ts'
import { Icon } from './Icon.tsx'
export function AppointmentRow({ appointment }) {
  return (
    <a class="list-row" href={`#/appointments/${appointment.id}`}>
      <div class="date-tile">
        {date(appointment.starts_at, { month: 'short', day: undefined })}
        <strong>{new Date(appointment.starts_at).getDate()}</strong>
      </div>
      <div class="row-main">
        <strong>{appointment.call.contact.name}</strong>
        <small>{appointment.call.contact.company?.name || appointment.call.contact.email}</small>
      </div>
      <div class="row-end">
        <strong style="font-size:12px">{time(appointment.starts_at)}</strong>
        <div class="muted">{time(appointment.ends_at)}</div>
      </div>
      <Icon name="arrow" size={14} />
    </a>
  )
}
