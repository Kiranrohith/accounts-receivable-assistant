/** What every decision panel is given. */
export interface PanelProps {
  /** The id the queue's own endpoints take (the review id, payment id, dispute id...). */
  itemId: string
  /** The decision went through: the modal closes, the row leaves the list and a toast says `message`. */
  onDone: (message: string) => void
}
