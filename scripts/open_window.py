"""Operational handshake: open the 24-hour freeform window.

Sends the pre-approved hello_world template. Carries no case content and does
not touch the gate — there is nothing to classify. Reply to it on the handset
and the gated demo can then send real content as freeform text.
"""
import sys

from anbu_care.comms import transport

to = sys.argv[1] if len(sys.argv) > 1 else "+16692167706"
r = transport.open_session(to)
print(f"delivered  {r.delivered}")
print(f"provider   {r.provider_id} / {r.provider_status} / http {r.http_status}")
print(f"detail     {r.detail}")
