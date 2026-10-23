"""providers/telephony/sip.py -- reserved adapter slot for SIP (NOT IMPLEMENTED).

What this file is for
---------------------
The name 'sip' is registered (providers/telephony/registry.py) so it can be selected and documented, but no transport exists:
the repository does not yet know the client's actual setup, and guessing would produce an adapter that connects incorrectly.
Selecting it stops at start-up with a clear error. Nothing here pretends to work.

When the client confirms they use this, implement it HERE only (telephony/ and the AI stay unchanged): a TelephonyProvider whose
serve() accepts calls and a TelephonyCall per call (see telephony/adapter.py and providers/telephony/websocket.py for a worked
example), then set implemented=True in the registry.

Information required from the client first:
  - SIP server/registrar address and transport (UDP/TCP/TLS), account or trunk credentials
  - how calls arrive (registration, trunk, inbound INVITE) and the RTP codec (PCMU/PCMA/...), packetisation and ports
  - NAT/firewall placement; transfer (REFER) and DTMF requirements
Endpoint placeholder: place your url here
"""

from __future__ import annotations

from telephony.adapter import TelephonyProvider
from telephony.config import ProviderNotImplementedError, ProviderSettings


# sourav: this provider is recognised but has no transport yet. Flow: always raise a precise, secret-free error; the settings
# are not inspected or logged.
def create_provider(settings: ProviderSettings) -> TelephonyProvider:
    raise ProviderNotImplementedError(
        "Provider 'sip' is recognized but no SIP transport has been configured/implemented yet. "
        "Ask the client for the details listed in telephony/README.md (Information required from client), then implement "
        "providers/telephony/sip.py."
    )
