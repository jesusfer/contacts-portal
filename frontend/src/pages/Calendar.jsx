import { useState } from 'preact/hooks'
import { date, dateTime, time, useResource } from '../lib/data.js'
import { AppointmentRow } from '../components/AppointmentRow.jsx'
import { Empty } from '../components/Empty.jsx'
import { ErrorNotice } from '../components/ErrorNotice.jsx'
import { Icon } from '../components/Icon.jsx'
import { Loading } from '../components/Loading.jsx'
import { PageHeading } from '../components/PageHeading.jsx'
import { Panel } from '../components/Panel.jsx'

export function Calendar({ version, openCreate }) {
  const r = useResource('appointments/', version), [month,setMonth] = useState(() => new Date(new Date().getFullYear(),new Date().getMonth(),1)), [agenda,setAgenda] = useState('next')
  const appointments = r.data?.appointments || []
  const first = new Date(month.getFullYear(),month.getMonth(),1), start = new Date(first)
  start.setDate(start.getDate() - ((first.getDay()+6)%7))
  const cells = Array.from({length:42},(_,i) => { const d = new Date(start); d.setDate(d.getDate()+i); return d })
  const dayKey = d => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`
  const now = new Date()
  const next = appointments.filter(a => ['scheduled', 'rescheduled'].includes(a.call.status) && new Date(a.ends_at) >= now).sort((a,b) => new Date(a.starts_at) - new Date(b.starts_at))
  const past = appointments.filter(a => new Date(a.ends_at) < now).sort((a,b) => new Date(b.starts_at) - new Date(a.starts_at))
  const list = agenda === 'next' ? next : past
  return <><PageHeading eyebrow="Calendar" title="Make time for connection." subtitle="A clear view of what's ahead and what came before."><button class="button" onClick={() => openCreate('appointment')}><Icon name="plus" size={16}/>New appointment</button></PageHeading><ErrorNotice error={r.error} retry={r.retry}/>{r.loading ? <Loading/> : !r.error && <><section class="panel"><div class="calendar-header"><h2>{month.toLocaleDateString(undefined,{month:'long',year:'numeric'})}</h2><div class="actions"><button class="button secondary" aria-label="Previous month" onClick={() => setMonth(new Date(month.getFullYear(),month.getMonth()-1,1))}>←</button><button class="button secondary" onClick={() => setMonth(new Date(now.getFullYear(),now.getMonth(),1))}>Today</button><button class="button secondary" aria-label="Next month" onClick={() => setMonth(new Date(month.getFullYear(),month.getMonth()+1,1))}>→</button></div></div><div class="calendar-grid">{['Mon','Tue','Wed','Thu','Fri','Sat','Sun'].map(day => <div class="weekday" key={day}>{day}</div>)}{cells.map(d => <div key={dayKey(d)} class={`calendar-day ${d.getMonth() !== month.getMonth() ? 'other' : ''} ${dayKey(d) === dayKey(now) ? 'today-cell' : ''}`}><span class="day-number" aria-label={date(d, {year:'numeric',month:'long'})}>{d.getDate()}</span>{appointments.filter(a => dayKey(new Date(a.starts_at)) === dayKey(d)).sort((a,b) => new Date(a.starts_at)-new Date(b.starts_at)).map(a => <a class="calendar-event" href={`#/appointments/${a.id}`} key={a.id} title={`${a.call.contact.name} · ${dateTime(a.starts_at)}`}>{time(a.starts_at)} {a.call.contact.name}</a>)}</div>)}</div></section><Panel title="Your agenda" count={list.length} action={<div class="tabs" role="group" aria-label="Agenda timeframe"><button class={agenda === 'next' ? 'selected' : ''} aria-pressed={agenda === 'next'} onClick={() => setAgenda('next')}>Upcoming</button><button class={agenda === 'past' ? 'selected' : ''} aria-pressed={agenda === 'past'} onClick={() => setAgenda('past')}>Past</button></div>}>{list.map(a => <div key={a.id}><div class="agenda-heading">{date(a.starts_at, { weekday:'long',year:'numeric' })}</div><AppointmentRow appointment={a}/></div>)}{!list.length && <Empty title={agenda === 'next' ? 'Your calendar has room' : 'No past appointments'} icon="calendar">{agenda === 'next' ? 'Schedule a conversation to get started.' : 'Previous appointments will be listed here.'}</Empty>}</Panel></>}</>
}
