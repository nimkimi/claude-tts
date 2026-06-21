from __future__ import annotations

# Speech-rate clamp bounds (words per minute).
RATE_MIN = 100
RATE_MAX = 400

# Min-queue batching: how many prose items must accumulate before they are read.
# 1 == read each item as it arrives (the default, unchanged behaviour).
MINQUEUE_MIN = 1
MINQUEUE_MAX = 10
