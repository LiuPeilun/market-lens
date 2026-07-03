import { createBrowserRouter, RouterProvider } from 'react-router-dom'

import { DashboardPage } from '@/pages/dashboard'

const router = createBrowserRouter([
  {
    path: '/',
    element: <DashboardPage />,
  },
])

export default function App() {
  return <RouterProvider router={router} />
}
