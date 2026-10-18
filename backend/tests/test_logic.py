from app.etiquette import CARD_PROMPT, route, spoken_seconds
from app.wake import chat_mention, detect_wake, is_approval, is_cancel, is_card_followup, wants_raised_answer
import pytest


def test_wake_variants_and_command_extraction():
    assert detect_wake("hey tensor does this spec conflict").command == "does this spec conflict"
    assert detect_wake("Hey Tenser, what were our active users last week?").command.startswith("what were")
    assert detect_wake("okay hey ten sir plan the tasks").command == "plan the tasks"
    assert detect_wake("Tensor, email David").command == "email David"
    assert detect_wake("the tensor library in pytorch is fast") is None
    assert detect_wake("we should denser the layout") is None
    assert detect_wake("Hey Tensor").command == ""


@pytest.mark.parametrize("phrase", [
    "Hey, tensor.", "Tensor.", "Hey. Tensor.", "Hey: Tensor!", "“Tensor.”",
    "Hi, Tenser.", "Hello Tensor.", "Okay, Tenzor.", "Hey Tensar.", "Tensir.",
    "Hey, Ten sir.", "Hey ten-zer.", "Hey, dancer.",
])
def test_standalone_transcription_variants(phrase):
    assert detect_wake(phrase).command == ""
    assert detect_wake(phrase + " What was our success rate?").command == "What was our success rate?"


@pytest.mark.parametrize("phrase", [
    "The tensor library is fast", "We use TensorFlow", "Dancer, plan the tasks",
    "The dancer is here", "The tensorboard report is ready", "Sarah, go ahead",
    "We hired a dancer", "A dancer is joining the meeting",
])
def test_wake_does_not_match_unaddressed_mentions(phrase):
    assert detect_wake(phrase) is None


def test_approval_cancel_and_followups():
    assert is_approval("Change the first one to High priority and go ahead")
    assert is_approval("send it") and is_approval("Approve all")
    assert not is_approval("don't send it yet")
    assert is_cancel("cancel that draft")
    assert is_card_followup("make it more formal") and is_card_followup("CC Sarah")
    assert not is_card_followup("I think the demo went well")
    assert wants_raised_answer("go ahead, Tensor") and wants_raised_answer("Tensor go ahead")


def test_chat_mention():
    assert chat_mention("@Tensor check the SLA table") == "check the SLA table"
    assert chat_mention("hello @Tensor") is None


def test_etiquette_routes_by_length_content_and_activity():
    short = route("Payment success rate yesterday was 98.9 percent.", "", humans_speaking=True, created_card=False)
    assert short.mode == "VOICE" and short.chat is None
    long_details = "| Rule | Spec |\n|---|---|\n| TLS 1.2+ | TLS 1.1 |"
    busy = route("Found 2 compliance conflicts in the PDF.", long_details, humans_speaking=True, created_card=False)
    assert busy.mode == "HAND_RAISE" and busy.speak is None and "TLS 1.1" in busy.chat
    assert busy.hand_topic == "Found 2 compliance conflicts in the PDF." and busy.pending_speech
    quiet = route("Found 2 compliance conflicts in the PDF. The first is TLS.", long_details, humans_speaking=False, created_card=False)
    assert quiet.mode == "VOICE_AND_CHAT" and quiet.speak.startswith("Found 2 compliance conflicts in the PDF.")
    assert spoken_seconds(quiet.speak) < 15
    card = route("Drafted two tickets.", "", humans_speaking=False, created_card=True)
    assert card.mode == "CARD_PROMPT" and card.speak == CARD_PROMPT
    monologue = route(" ".join(["word"] * 60), "", humans_speaking=False, created_card=False)
    assert monologue.mode == "VOICE_AND_CHAT" and spoken_seconds(monologue.speak) <= 15


def test_password_hashing():
    from app.accounts import hash_password, verify_password

    stored = hash_password("s3cret-pass")
    assert stored.startswith("scrypt$") and "s3cret" not in stored
    assert verify_password("s3cret-pass", stored) and not verify_password("s3cret-pasS", stored)
    assert not verify_password("x", "garbage")


def test_sql_guard_rejects_before_touching_bigquery():
    from app.config import Settings
    from app.knowledge import CompanyKnowledge, SqlRejected

    k = CompanyKnowledge(Settings(store="memory", ai_enabled=False), bq_client=object())
    for sql in ("DELETE FROM daily_metrics WHERE TRUE", "SELECT 1; DROP TABLE x",
                "SELECT * FROM `region-asia-south1`.INFORMATION_SCHEMA.JOBS"):
        try:
            k.check_sql("tensor_co_acme", sql)
            raise AssertionError(sql)
        except SqlRejected:
            pass
    assert "error" in k.query("tensor_co_acme", "UPDATE t SET a = 1")


def test_templates_generate_every_industry():
    from app import templates

    for industry, spec in templates.INDUSTRIES.items():
        data = templates.generate(industry, seed="x")
        assert set(data) == set(spec["tables"])
        for name, table in spec["tables"].items():
            assert set(data[name][0]) == {f["name"] for f in table["fields"]}, (industry, name)
        assert "TLS 1.2" in templates.handbook_html(industry, "Blue Harbor", "BLUE")
        assert all(p["email"].endswith("@blueharbor.example") for p in templates.team(industry, "Blue Harbor"))


def test_router_sends_clear_requests_straight_to_a_specialist():
    from app.agents import route

    assert route("email David the links to those new tickets and a short summary") == "email"
    assert route("turn those conflicts into Jira tickets for the security sprint and assign them to David") == "jira"
    assert route("does this spec conflict with our internal cloud security guidelines?", has_documents=True) == "document"
    assert route("does this spec conflict with our internal cloud security guidelines?") == "knowledge"  # nothing shared yet
    assert route("what were our daily active users last week?") == "knowledge"
    assert route("change the first one to highest priority", pending_types=["JIRA_TASK_PLAN"]) == "jira"
    assert route("make it more formal", pending_types=["EMAIL_DRAFT"]) == "email"
    assert route("make it shorter", pending_types=["EMAIL_DRAFT", "JIRA_TASK_PLAN"]) is None
    assert route("create jira tickets for this and email Sarah") is None  # two actions: the supervisor decides
    assert route("hello there") is None and route("can you recap the discussion?") is None



def test_meeting_questions_are_answered_from_meeting_context():
    from app.agents import route

    for q in ("who all are in the meeting?", "who is here?", "who's on the call right now", "who joined?",
              "what did Sarah say about the launch?", "what was posted in the chat?", "who asked about the SLA?",
              "can you list the participants", "summarize the meeting so far"):
        assert route(q) is None, q
    assert route("who owns the vendor SLA policy?") == "knowledge"  # a company question still goes to knowledge


async def test_dual_speech_captions_fast_and_transcribes_accurately():
    import asyncio

    from app.config import Settings
    from app.speech import DualSpeech, company_phrases

    got = []

    async def on_result(text, final):
        got.append((text, final))

    dual = DualSpeech(Settings(store="memory", ai_enabled=False), asyncio.get_running_loop(), on_result, phrases=["Tensor"])
    await dual._fast_result("hey tensor what", False)
    await dual._fast_result("Hey Tensor, what were our DAU?", True)  # fast final: still only a caption
    await dual._fast_result("and", False)
    await dual._accurate_result("Hey Tensor, what were our daily active users?", True)
    assert got == [("hey tensor what", False), ("Hey Tensor, what were our DAU?", False),
                   ("Hey Tensor, what were our DAU? and", False), ("Hey Tensor, what were our daily active users?", True)]
    dual.accurate.failures = 2  # accurate model down: fast finals become the transcript
    await dual._fast_result("go ahead", True)
    assert got[-1] == ("go ahead", True)
    assert company_phrases({"name": "Acme Fintech", "team": [{"name": "Sarah Iyer"}]})[-3:] == ["Acme Fintech", "Sarah Iyer", "Sarah"]



def test_action_questions_are_answered_from_the_action_center():
    from app.actions import ticket_list
    from app.agents import route

    for q in ("what all are the tickets open?", "give me the last ticket", "what is SEC-403 about?", "which emails did we send?",
              "list the tasks we created", "what's in the action center?"):
        assert route(q) is None, q
    assert route("create jira tickets for those risks") == "jira"  # creating still goes to the Jira agent
    card = dict(type="JIRA_TASK_PLAN", result=dict(refs=["SEC-403", "SEC-404"]), payload=dict(proposed_tasks=[
        dict(title="A", assignee="David", priority="High", story_points=5, include=True),
        dict(title="B", assignee="Rahul", priority="Low", story_points=1, include=False),
        dict(title="C", assignee="David", priority="Medium", story_points=3, include=True)]))
    assert [(t["key"], t["title"]) for t in ticket_list(card)] == [("SEC-403", "A"), ("SEC-404", "C")]  # excluded tasks get no key



def test_stop_phrases():
    from app.wake import is_stop

    for text in ("stop", "Tensor, stop", "hold on", "okay thanks", "thank you Tensor that's enough", "Hey Tensor, be quiet"):
        assert is_stop(text), text
    for text in ("we should stop the rollout until the fix lands", "the pause button in the app is broken again today", "next steps"):
        assert not is_stop(text), text
