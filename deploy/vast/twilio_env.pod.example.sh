#!/usr/bin/env bash
# Template for /workspace/.twilio_env.sh on a Vast.ai pod -- the pod's Twilio settings.
#
# Install it ON THE POD and fill in the secrets THERE, in the pod's own terminal:
#
#     cp /workspace/kolkata-care-voice-agent/deploy/vast/twilio_env.pod.example.sh /workspace/.twilio_env.sh
#     chmod 600 /workspace/.twilio_env.sh
#     nano /workspace/.twilio_env.sh          # paste the four values below
#
# Why it is filled in on the pod rather than pushed: the real file holds an Auth Token and an API key secret, and the
# push (deploy/vast/podlink.py) mirrors the working tree. Keeping credentials out of the tree entirely means they
# cannot reach the repo, a tarball, a log or a transcript -- only this pod. It survives stop/start; a recycle or
# destroy wipes it along with everything else outside the laptop (see deploy/vast/README.md).

export TELEPHONY_PROVIDER=twilio

# ---------------------------------------------------------------------------------------------------------------------
# The voice agent on THIS platform
# ---------------------------------------------------------------------------------------------------------------------
# main_pcm.py binds 10100 on Vast (deploy/vast/host_env.vast.sh), NOT the 8101 default. Getting this wrong fails
# silently in the worst way: the call connects, Twilio streams audio, and the caller hears nothing at all.
export VOICE_PCM_HOST=127.0.0.1
export VOICE_PCM_PORT=10100

# ---------------------------------------------------------------------------------------------------------------------
# Where the Twilio adapter listens. ngrok tunnels THIS port -- never 10100, which would expose /ws/audio.
# ---------------------------------------------------------------------------------------------------------------------
export TELEPHONY_LISTEN_HOST=127.0.0.1
export TELEPHONY_LISTEN_PORT=8200

# ---------------------------------------------------------------------------------------------------------------------
# Credentials -- paste these in on the pod. Twilio has two pairs and they do different jobs.
# ---------------------------------------------------------------------------------------------------------------------
# Account SID: the "AC..." value on the Twilio console home page. Needed for hand-off, and when set the adapter
# refuses streams belonging to any other Twilio account.
export TELEPHONY_USERNAME=""

# Auth Token: on the same console page, next to the Account SID. REQUIRED. It is the only thing that can verify the
# X-Twilio-Signature header, i.e. prove a connection really came from Twilio and not from someone who guessed the
# tunnel URL. Without it this pod must not take a public call -- see start_twilio.sh's interlock.
export TELEPHONY_PASSWORD=""

# API Key SID ("SK...") + its secret, from Account > API keys & tokens. OPTIONAL: it authenticates the hand-off REST
# call so the Auth Token is not needed for that. It CANNOT verify an inbound call -- it is not a substitute above.
export TELEPHONY_API_KEY=""
export TELEPHONY_API_SECRET=""

# ---------------------------------------------------------------------------------------------------------------------
# Optional
# ---------------------------------------------------------------------------------------------------------------------
# E.164 number a "transfer me to a human" request dials. Needs the Account SID plus either the Auth Token or the API
# key pair. Unset = hand-off unsupported, and the call simply ends instead.
# export TELEPHONY_TRANSFER_TO="+15551234567"

# TELEPHONY_ENDPOINT is NOT set here: deploy/vast/start_twilio.sh fills it in from the live ngrok URL each start,
# which is also what keeps it identical to the URL the signature is checked against.
