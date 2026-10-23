from pydantic import BaseModel


class SettingItem(BaseModel):
    name: str  # the constant, as it is written in code
    label: str  # what a person calls it
    value: str  # as a person reads it ("85", "09:00 IST", "1, 15, 30 days")
    description: str


class SettingsSection(BaseModel):
    key: str
    title: str
    items: list[SettingItem]


class SettingsOut(BaseModel):
    editable: bool  # false in this version: the values are managed in code
    note: str
    sections: list[SettingsSection]
