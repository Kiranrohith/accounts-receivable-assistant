import { notificationLabel, notificationTarget } from './target'

const id = '8b1f6c52-0d3a-4c1e-9a77-2f5e6b1d9c30'

describe('notificationTarget', () => {
  it('sends a collection task to its Take Action item', () => {
    expect(notificationTarget({ entity_type: 'task', entity_id: id })).toEqual({
      to: '/take-action',
      search: { section: 'collection_task', item: `collection_task:${id}` },
    })
  })

  it('sends the rest to the list that exists for them', () => {
    expect(notificationTarget({ entity_type: 'dispute', entity_id: id })).toEqual({ to: '/disputes' })
    expect(notificationTarget({ entity_type: 'invoice', entity_id: id })).toEqual({ to: '/invoices' })
    expect(notificationTarget({ entity_type: 'payment', entity_id: id })).toEqual({ to: '/payments' })
    expect(notificationTarget({ entity_type: 'reminder', entity_id: id })).toEqual({ to: '/reminders' })
  })

  it('has no link for a notice about nothing, or about something with no screen', () => {
    expect(notificationTarget({ entity_type: null, entity_id: null })).toBeNull()
    expect(notificationTarget({ entity_type: 'dispute', entity_id: null })).toBeNull()
    expect(notificationTarget({ entity_type: 'schedule', entity_id: id })).toBeNull()
  })
})

describe('notificationLabel', () => {
  it('reads each backend type in plain words and shows an unknown one as it is', () => {
    expect(notificationLabel('Assignment')).toBe('Assigned to you')
    expect(notificationLabel('ReminderReply')).toBe('Reply to a reminder')
    expect(notificationLabel('BrandNew')).toBe('BrandNew')
  })
})
