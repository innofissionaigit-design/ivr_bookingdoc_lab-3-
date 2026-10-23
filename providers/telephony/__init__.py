"""providers/telephony/ -- one module per telephony provider, selected with TELEPHONY_PROVIDER=<module name>.

What this package is for: each module here implements telephony/adapter.py's TelephonyProvider/TelephonyCall for one phone
system and exposes `create_provider(settings)`. Today it holds only `wavfile`, an offline test adapter; the client's real
provider adapter is added here once they say which system they use.
"""
