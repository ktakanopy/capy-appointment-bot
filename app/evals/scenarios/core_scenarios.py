from __future__ import annotations

from app.evals.models import EvaluationScenario


CORE_SCENARIOS = [
    EvaluationScenario(
        scenario_id="search-service-date",
        title="Find a service for tomorrow",
        utterance="Can I book a relaxation massage tomorrow?",
        context={"service_names": ["Relaxation massage"], "today": "2026-10-19"},
        expected={"intent": "search", "service_name": "Relaxation massage", "local_date": "2026-10-20"},
        category="booking-discovery",
    ),
    EvaluationScenario(
        scenario_id="search-professional-time",
        title="Extract a requested professional and local time",
        utterance="Schedule a relaxation massage with Ana Costa on 2026-10-22 at 10:30",
        context={"service_names": ["Relaxation massage"], "professional_names": ["Ana Costa", "Rui Mendes"]},
        expected={"intent": "search", "service_name": "Relaxation massage", "professional_name": "Ana Costa", "local_date": "2026-10-22", "local_time": "10:30"},
        category="booking-discovery",
    ),
    EvaluationScenario(
        scenario_id="cancel-booking",
        title="Understand a cancellation request",
        utterance="Please cancel my booking",
        expected={"intent": "cancel"},
        category="booking-management",
    ),
    EvaluationScenario(
        scenario_id="reschedule-booking",
        title="Understand a reminder reply asking to reschedule",
        utterance="Can I move my appointment to tomorrow?",
        context={"today": "2026-10-19"},
        expected={"intent": "reschedule", "local_date": "2026-10-20"},
        category="reminder-reply",
    ),
    EvaluationScenario(
        scenario_id="select-offered-slot",
        title="Select a previously offered time by ordinal",
        utterance="The second one works for me",
        context={"has_slots": True},
        expected={"intent": "select_slot", "slot_index": 2},
        category="slot-selection",
    ),
    EvaluationScenario(
        scenario_id="provide-customer-details",
        title="Extract customer name and phone for booking",
        utterance="Jordan Lee +5511999998888",
        expected={"customer_name": "Jordan Lee", "phone": "+5511999998888"},
        category="booking-creation",
    ),
]
