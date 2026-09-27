"""Keep conversational milk-supply planning compatible with runtime write boundaries."""

from app.agent import AGENT, SERVICE_SKILL_REGISTRY


def test_plan_routing_is_proactive_but_does_not_assume_a_supply_goal() -> None:
    skill = SERVICE_SKILL_REGISTRY.get("lactation")
    assert "planning opportunity" in skill.content
    assert "without waiting for the word plan" in skill.content
    assert "do not infer that she wants to increase or reduce supply" in skill.content
    assert "one-off pumping change" in skill.content
    assert "milk-supply-assessment.md" in skill.content
    assert "milk-supply-management.md" in skill.content
    assert "return-to-work-feeding.md" in skill.content


def test_three_references_have_distinct_plan_responsibilities() -> None:
    skill = SERVICE_SKILL_REGISTRY.get("lactation")
    assessment = skill.get_reference("milk-supply-assessment").content
    management = skill.get_reference("milk-supply-management").content
    work = skill.get_reference("return-to-work-feeding").content

    assert "planning triage" in assessment
    assert "not a prescription to increase or reduce supply" in assessment
    assert "partial or complete weaning" in management
    assert "baby's age and appropriate replacement feeding" in management
    assert "an initial change, what to observe, and a revision point" in management
    assert "workday fallback" in work
    assert "real break opportunities" in work
    assert "do not fabricate clock times" in work


def test_conversational_plan_does_not_bypass_system_prompt_write_rules() -> None:
    skill = SERVICE_SKILL_REGISTRY.get("lactation")
    assert "conversational plan is not a saved App plan" in skill.content
    assert "global write-tool confirmation rules" in skill.content
    assert "Wait for the next user message" in AGENT.instructions
    assert "Do not claim a calendar entry sets a reminder or notification" in AGENT.instructions
