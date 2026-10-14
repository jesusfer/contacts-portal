export function Badge({ status }: { status: string }) {
  return <span class={`badge ${status}`}>{status}</span>
}
