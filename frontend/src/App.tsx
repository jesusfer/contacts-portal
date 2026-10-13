import { AppRoutes } from './routes.tsx'
import { WorkspaceApp } from './WorkspaceApp.tsx'
import { TeamsConfig } from './pages/TeamsConfig.tsx'
import { TeamsCustomerTab } from './pages/TeamsCustomerTab.tsx'
import { TeamsTab } from './pages/TeamsTab.tsx'

export function App() {
  return <AppRoutes
    workspace={<WorkspaceApp/>}
    pages={{
      '/teams/config/': TeamsConfig,
      '/teams/customer/': TeamsCustomerTab,
      '/teams/tab/': TeamsTab,
    }}
  />
}
