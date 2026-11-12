# Portfolio roadmap

The implemented foundation keeps Telegram text/audio, Gemma understanding, required Laya routing, deterministic booking services and reproducible evaluation choices.

1. Complete a live synthetic-data Telegram walkthrough through text and real recordings; record model revisions and results. Automated mocks and decoder tests do not establish speech quality.
2. Expand the small bilingual corpus with informal English/Portuguese, ambiguity, corrections, accents, background noise and long recordings. Keep source/provenance and paired variants grouped across train/dev/test splits. Do not automatically collect private customer conversations.
3. Compare identical cases through Gemma+Laya and Gemma+deterministic routing. Separate transcription, extraction, fixed-input routing and final task outcomes. Report sample counts, latency distributions and provider failures.
4. Fine-tune Gemma and Laya on reviewed training data. Choose checkpoints on development data, then evaluate on a held-out test set. Keep a baseline and record the exact dataset/configuration/model revision for every comparison.
5. Publish a case study with the measured benefits and failures, booking correctness evidence, model limitations and the operational tradeoffs.

RAG, additional channels, payment/calendar integrations and a human-handoff product are outside the current scope.
