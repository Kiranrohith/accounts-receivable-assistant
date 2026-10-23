export interface SettingItem {
  /** The constant, as it is written in code. */
  name: string
  label: string
  /** As a person reads it ("85", "09:00 IST", "1, 15, 30"). */
  value: string
  description: string
}

export interface SettingsSection {
  key: string
  title: string
  items: SettingItem[]
}

export interface Settings {
  /** False in this version: the values are managed in code. */
  editable: boolean
  note: string
  sections: SettingsSection[]
}
