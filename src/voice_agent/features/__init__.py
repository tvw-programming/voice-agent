"""Optional agent features, one file per feature.

filler.py        speak a short line while a slow tool runs
class_search.py  search students by class, division and roll number
spelling.py      assemble spelled-out names ("P R I Y A", "P for Pune")
phonetic.py      sound-based matching for Indian names
audit_log.py     tamper-evident SQLite log of PIN attempts and lookups
staff_pins.py    one PIN per staff member, caught before the LLM sees it

Each file is self-contained on purpose (small helpers are duplicated rather
than shared) so a feature can be read, tested or removed on its own.
"""
