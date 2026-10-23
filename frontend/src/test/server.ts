import { setupServer } from 'msw/node'

/** The MSW server every test shares; a test adds its own handlers with server.use(...). */
export const server = setupServer()
