"""telephony/ -- provider-independent telephony connector for the voice agent (see telephony/README.md).

What this package is for: it lets a phone call talk to main_pcm.py over the existing /ws/audio protocol without changing the
agent. Modules: adapter (provider interface), audio (format conversion), agent_link (WebSocket to the agent), config
(environment settings), loader (finds the provider adapter), connector (the bridge). Provider-specific code lives in
providers/telephony/, never here.
"""
