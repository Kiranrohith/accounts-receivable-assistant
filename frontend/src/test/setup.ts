import '@testing-library/jest-dom/vitest'
import { cleanup, configure } from '@testing-library/react'
import { afterAll, afterEach, beforeAll } from 'vitest'

import { server } from './server'

// The first render of a route compiles its chunk: under a parallel run that can take longer than the default second.
configure({ asyncUtilTimeout: 10_000 })

// jsdom has no layout engine: Radix and the DataTable ask for these.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver ??= ResizeObserverStub as unknown as typeof ResizeObserver
Element.prototype.scrollIntoView ??= () => {}
Element.prototype.hasPointerCapture ??= () => false
Element.prototype.releasePointerCapture ??= () => {}

// jsdom has no blob URLs: the document viewer makes one for the file it fetched.
URL.createObjectURL ??= () => 'blob:test'
URL.revokeObjectURL ??= () => {}

beforeAll(() => server.listen({ onUnhandledFrame: 'error' }))
afterEach(() => {
  cleanup()
  sessionStorage.clear()
  server.resetHandlers()
})
afterAll(() => server.close())
