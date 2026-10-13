import { Loading } from './Loading.tsx'
import { ErrorNotice } from './ErrorNotice.tsx'
export function GraphPagination({ resource }) {
  return <>{resource.loading && <Loading/>}<ErrorNotice error={resource.error} retry={resource.retry}/>{resource.next && !resource.error && <div class="panel-body"><button class="button secondary" disabled={resource.loading} onClick={resource.more}>Load more</button></div>}</>
}
