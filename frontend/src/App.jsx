import { AppRoutes } from './routes.jsx'
import { WorkspaceApp } from './WorkspaceApp.jsx'
import { TeamsConfig } from './pages/TeamsConfig.jsx'
import { TeamsCustomerTab } from './pages/TeamsCustomerTab.jsx'
import { TeamsTab } from './pages/TeamsTab.jsx'

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
