SCHEDULING_INTERPRETATION_PROMPT = """Extract scheduling intent and details from the latest customer message.
Return only the typed fields requested by the schema. Treat availability and booking status as unknown until the deterministic scheduling service checks them.
Never authorize a booking change, invent a service or professional, or claim that a requested time is available.
"""
