from __future__ import annotations

from products.structured_extraction_agent import StructuredExtractionAgent, validate_extraction


def test_validate_extraction_reports_missing_required_fields():
    errors = validate_extraction({"vendor": "Acme"})
    assert any("amount" in e for e in errors)
    assert any("date" in e for e in errors)


def test_validate_extraction_reports_malformed_amount_and_date():
    errors = validate_extraction({"vendor": "Acme", "amount": "$1,250.00", "date": "07/20/2026"})
    assert any("amount" in e for e in errors)
    assert any("date" in e for e in errors)


def test_validate_extraction_passes_on_well_formed_fields():
    errors = validate_extraction({"vendor": "Acme", "amount": "1250.00", "date": "2026-07-20"})
    assert errors == []


def test_extraction_succeeds_on_first_try_when_output_is_already_valid():
    def clean_extract(_text, *, previous_errors):
        return {"vendor": "Acme", "amount": "1250.00", "date": "2026-07-20"}

    agent = StructuredExtractionAgent(clean_extract)
    result = agent.extract("some invoice text")

    assert result.is_valid
    assert result.attempts == 1


def test_extraction_retries_using_previous_errors_as_feedback():
    calls = []

    def eventually_correct_extract(_text, *, previous_errors):
        calls.append(list(previous_errors))
        if not previous_errors:
            return {"vendor": "Acme", "amount": "not-a-number", "date": "2026-07-20"}
        return {"vendor": "Acme", "amount": "1250.00", "date": "2026-07-20"}

    agent = StructuredExtractionAgent(eventually_correct_extract)
    result = agent.extract("some invoice text")

    assert result.is_valid
    assert result.attempts == 2
    assert calls[0] == []
    assert any("amount" in e for e in calls[1])


def test_extraction_gives_up_after_max_consecutive_errors():
    def always_broken_extract(_text, *, previous_errors):
        return {"vendor": "Acme", "amount": "bad", "date": "bad"}

    agent = StructuredExtractionAgent(always_broken_extract)
    result = agent.extract("garbled text")

    assert result.is_valid is False
    assert result.attempts == agent.limits.max_consecutive_errors(agent.task_name)


def test_filing_gate_passes_for_valid_extraction():
    def clean_extract(_text, *, previous_errors):
        return {"vendor": "Acme", "amount": "1250.00", "date": "2026-07-20"}

    agent = StructuredExtractionAgent(clean_extract)
    result = agent.extract("text")

    decision = agent.filing_gate(result).run()
    assert decision.passed is True


def test_filing_gate_blocks_invalid_extraction_with_specific_reasons():
    def always_broken_extract(_text, *, previous_errors):
        return {"vendor": "Acme", "amount": "bad", "date": "bad"}

    agent = StructuredExtractionAgent(always_broken_extract)
    result = agent.extract("text")

    decision = agent.filing_gate(result).run()
    assert decision.passed is False
    assert any("amount" in blocker for blocker in decision.blockers)
