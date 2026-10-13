import { initials } from '../utils/data.ts'
export function Contact({ contact }) { return <div class="contact-cell"><span class="avatar">{initials(contact?.name)}</span><span><strong>{contact?.name || 'Unknown contact'}</strong><small>{contact?.company?.name || contact?.email}</small></span></div> }
