import { Icon } from './Icon.jsx'
export function Empty({ title = 'Nothing here yet', children, icon = 'contacts' }) { return <div class="empty"><Icon name={icon} size={27}/><strong>{title}</strong><p>{children}</p></div> }
