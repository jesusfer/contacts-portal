// Renders a status label with a status-specific CSS class.
export function Badge({ status }: { status: string }) {
  return <span class={`badge ${status}`}>{status}</span>
}
