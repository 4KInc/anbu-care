"""Tools exposed to agents.

Each agent gets its own module and only that module's tools are attached to it —
isolated tool scopes are the point, so the WhatsApp agent cannot reach the
claim submitter and the triage agent cannot send messages.
"""
