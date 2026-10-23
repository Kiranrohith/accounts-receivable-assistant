import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { renderWithClient } from '@/test/render'

import { ReasonDialog } from './ReasonDialog'

describe('ReasonDialog', () => {
  it('keeps the confirm button disabled until a reason is typed, then passes the trimmed text', async () => {
    const onConfirm = vi.fn()
    renderWithClient(<ReasonDialog open onOpenChange={() => {}} title="Reject payment" confirmLabel="Reject payment" onConfirm={onConfirm} />)
    const confirm = screen.getByRole('button', { name: 'Reject payment' })
    expect(confirm).toBeDisabled()

    await userEvent.type(screen.getByLabelText(/reason/i), '   ')
    expect(confirm).toBeDisabled() // whitespace is not a reason

    await userEvent.type(screen.getByLabelText(/reason/i), '  wrong customer  ')
    expect(confirm).toBeEnabled()
    await userEvent.click(confirm)
    expect(onConfirm).toHaveBeenCalledWith('wrong customer')
  })
})
