import { RouterProvider } from '@tanstack/react-router'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import { Providers } from './app/providers'
import { router } from './app/router'
import { restoreSession } from './features/auth'
import './index.css'

// The route guards read the session synchronously, so it is settled (signed in with a user, or signed out) before the
// first screen renders: a reload or a new tab never flashes the login page at someone who is still signed in.
void restoreSession().then(() => {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <Providers>
        <RouterProvider router={router} />
      </Providers>
    </StrictMode>,
  )
})
