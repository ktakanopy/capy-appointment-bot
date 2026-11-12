# Conversation graph

A turn loads its catalog and checkpoint draft, interprets sanitized text with Gemma, merges scheduling fields, and asks Laya for an action. Deterministic constraints route to clarification, availability, selection, management, catalog help, lookup or a bounded unsupported response. Execution calls owned domain operations through `SchedulingActions`; persistence adds the reply to bounded history.

Text and complete audio transcripts enter the same interpretation path. Audio chunk outputs never reach execution. Gemma and Laya are required for normal turns; evaluations explicitly substitute deterministic implementations. Neither provider can authorize an account, invent an offered slot or bypass booking policy.

`prepare_action` dispatches to `_prepare_create`, `_prepare_reschedule` or `_prepare_booking_status_change`. These functions check the required slot and booking references before routing to `execute`. `CustomerSchedulingActions` connects those workflow operations to services for the trusted customer; `SchedulingActions` is the protocol also implemented by evaluation fixtures.

The Redis checkpoint is the only conversation store. It contains draft, offered slots, safe extraction/decision fields and at most 24 messages. Tokens and private customer/booking objects are absent. The workflow requires a valid result instead of silently inventing a fallback reply for a malformed graph result.

An unsupported response is not an operational human handoff; there is no staffed handoff queue.
