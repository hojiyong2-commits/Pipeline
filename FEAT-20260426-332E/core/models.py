"""TypedDict model definitions for AFM-Kitting email parsing and mapping."""

from typing import List, TypedDict


class KitRow(TypedDict):
    """A single row parsed from the AFM-Kitting email HTML table."""

    customer: str
    project_id: str
    sn: str
    kit_place: str
    remark: str
    planned_date: str


class MappedRow(TypedDict):
    """A single row ready to be written into Excel A."""

    month: int          # A열: target_date.month
    day: int            # B열: target_date.day
    pm: str             # D열: Korean PM name
    sn: str             # E열: S/N from email
    location: str       # F열: Customer from email
    po: str             # G열: Customer Order Lines IF column
    project_id: str     # H열: Project ID from email
    contract: str       # I열: Customer Order Lines F column
    incoterm: str       # J열: Customer Order Lines FP column
    note: str           # K열: 창고/포장반 (dims) #line_nos
