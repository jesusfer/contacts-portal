// Displays call-performance metrics in a table within a panel.
import { Panel } from './Panel.tsx'
import { Empty } from './Empty.tsx'

interface KpiRow {
  id: number
  name: string
  total_calls: number
  completed_calls: number
  average_rating: number | null
}

export function KpiTable({ title, rows }: { title: string; rows: KpiRow[] }) {
  return (
    <Panel title={title}>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Calls</th>
              <th>Completed</th>
              <th>Avg. rating</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <td>{row.name}</td>
                <td>{row.total_calls}</td>
                <td>{row.completed_calls}</td>
                <td>{row.average_rating == null ? '—' : Number(row.average_rating).toFixed(1)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!rows.length && (
          <Empty title="Your insights will grow here">
            Start logging conversations to see results.
          </Empty>
        )}
      </div>
    </Panel>
  )
}
