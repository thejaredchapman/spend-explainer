import io
import json
import subprocess
import sys
import unittest
import urllib.error
from decimal import Decimal
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "evals"))

from spend_explainer import client  # noqa: E402
from spend_explainer.analytics import cents_to_usd, load_dataset, parse_dataset  # noqa: E402
from spend_explainer.analyze import InsufficientData, analyze  # noqa: E402
from spend_explainer.pipeline import SOURCE_FALLBACK, SOURCE_MODEL, decide  # noqa: E402
from spend_explainer.prompt import build_prompt  # noqa: E402
from spend_explainer.verify import verify  # noqa: E402

SCENARIOS = ("quiet", "one_day_outlier", "model_mix_shift", "cache_regression", "volume_increase", "broad_growth")


def fixture(name):
    return analyze(load_dataset(ROOT / "fixtures" / name))


def recorded(name, kind):
    return (ROOT / "recorded" / name / f"{kind}.txt").read_text()


def good_answer(name, **changes):
    return json.dumps({**json.loads(recorded(name, "good")), **changes})


def row(day, amount="100.000000", model="claude-opus-5-5"):
    window = {"starting_at": f"{day}T00:00:00Z", "ending_at": _next(day)}
    return {**window, "group": {"workspace_id": "wrkspc_a", "api_key_id": "apikey_a", "model": model},
            "cost": {"amount": amount}}


def _next(day):
    return f"{day[:8]}{int(day[8:]) + 1:02d}T00:00:00Z"


class AnalyticsParsing(unittest.TestCase):
    def test_amount_is_cents_not_dollars(self):
        self.assertEqual(cents_to_usd("41280.000000"), Decimal("412.80"))

    def test_rows_past_the_watermark_are_dropped(self):
        cost = {"data": [row("2026-10-01"), row("2026-10-02")], "data_refreshed_at": "2026-10-02T04:00:00Z"}
        dataset = parse_dataset({"data": []}, cost)
        self.assertEqual([item.day for item in dataset.rows], ["2026-10-01"])
        self.assertEqual(dataset.dropped_days, ("2026-10-02",))

    def test_null_model_gets_a_label(self):
        dataset = parse_dataset({"data": []}, {"data": [row("2026-10-01", model=None)], "data_refreshed_at": None})
        self.assertEqual(dataset.rows[0].model, "(none)")

    def test_a_day_with_no_row_counts_as_zero(self):
        # The group is missing on the middle baseline days, so its median must be 0, not 100.
        days = ["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05", "2026-10-06"]
        rows = [row(day, "10000.000000") for day in days]
        rows += [
            {**row(day, "10000.000000"), "group": {"workspace_id": "wrkspc_b", "api_key_id": "apikey_b", "model": "m"}}
            for day in (days[0], days[-1])
        ]
        analysis = analyze(parse_dataset({"data": []}, {"data": rows, "data_refreshed_at": None}))
        ranked = {item["value"]: item for item in analysis.facts["by_dimension"]["workspace_id"]}
        self.assertEqual(ranked["wrkspc_b"]["baseline_daily_usd"], 0.0)


class SpikeDetection(unittest.TestCase):
    def test_each_scenario_reaches_its_expected_verdict(self):
        for name in SCENARIOS:
            with self.subTest(name):
                expected = json.loads((ROOT / "fixtures" / name / "expected.json").read_text())
                verdict = fixture(name).verdict
                self.assertEqual(verdict.status, expected["status"])
                self.assertEqual(verdict.primary_cause, expected["primary_cause"])
                self.assertEqual(verdict.driver_value, expected["driver"])

    def test_causes_are_what_the_scenarios_planted(self):
        self.assertEqual(fixture("cache_regression").verdict.primary_cause, "cache_regression")
        self.assertEqual(fixture("model_mix_shift").verdict.primary_cause, "model_mix_shift")
        self.assertEqual(fixture("volume_increase").verdict.primary_cause, "volume_increase")
        self.assertEqual(fixture("broad_growth").verdict.primary_cause, "broad_growth")
        self.assertEqual(fixture("one_day_outlier").verdict.status, "no_spike")

    def test_too_few_days_is_refused_rather_than_guessed(self):
        dataset = parse_dataset({"data": []}, {"data": [row("2026-10-01")], "data_refreshed_at": None})
        with self.assertRaises(InsufficientData):
            analyze(dataset)


class PromptSafety(unittest.TestCase):
    def test_verdict_is_not_in_the_facts(self):
        for name in SCENARIOS:
            with self.subTest(name):
                facts = fixture(name).facts
                self.assertNotIn("verdict", facts)
                self.assertNotIn(fixture(name).verdict.primary_cause, json.dumps(facts))

    def test_prompt_contains_the_data(self):
        prompt = build_prompt(fixture("quiet").facts)
        self.assertIn("<spend_data>", prompt)
        self.assertIn("wrkspc_01SYNTHSUPPORT", prompt)


class Verifier(unittest.TestCase):
    def test_every_good_answer_passes(self):
        for name in SCENARIOS:
            with self.subTest(name):
                self.assertTrue(verify(recorded(name, "good"), fixture(name)).passed)

    def test_every_bad_answer_fails_the_expected_check(self):
        expected = {"wrong_number": "numbers", "wrong_cause": "conclusion", "invented_id": "ids", "fenced_json": "json"}
        for name in SCENARIOS:
            for kind, check in expected.items():
                with self.subTest(f"{name}/{kind}"):
                    result = verify(recorded(name, kind), fixture(name))
                    self.assertIn(check, [item.name for item in result.failures])

    def test_a_number_of_the_wrong_kind_is_rejected(self):
        analysis = fixture("cache_regression")
        percent = analysis.facts["totals"]["percent_change_pct"]
        result = verify(good_answer("cache_regression", explanation=f"Spend rose by ${percent}."), analysis)
        self.assertIn("numbers", [item.name for item in result.failures])

    def test_rounded_numbers_are_accepted(self):
        analysis = fixture("cache_regression")
        window = analysis.facts["totals"]["window_daily_usd"]
        result = verify(good_answer("cache_regression", explanation=f"Spend reached about ${round(window)}."), analysis)
        self.assertTrue(result.passed, result.failures)

    def test_negative_changes_are_accepted_in_any_written_form(self):
        # Found by the first live eval: all three models wrote these correctly and were rejected.
        analysis = fixture("one_day_outlier")
        total = analysis.facts["totals"]
        dollars = abs(total["increase_daily_usd"])
        percent = abs(total["percent_change_pct"])
        for text in (f"a change of $-{dollars} (-{percent}%)", f"a change of -${dollars}", f"it fell by ${dollars}"):
            with self.subTest(text):
                result = verify(good_answer("one_day_outlier", explanation=text), analysis)
                self.assertTrue(result.passed, result.failures)

    def test_a_threshold_may_be_cited(self):
        answer = good_answer("cache_regression", explanation="This is above the 1.5x spike threshold.")
        self.assertTrue(verify(answer, fixture("cache_regression")).passed)

    def test_dates_and_ids_are_not_treated_as_numbers(self):
        answer = good_answer(
            "cache_regression",
            explanation="On 2026-10-15, wrkspc_01SYNTHSUPPORT using claude-sonnet-5-5 changed.",
        )
        self.assertTrue(verify(answer, fixture("cache_regression")).passed)

    def test_a_spike_claimed_on_quiet_data_is_rejected(self):
        answer = good_answer(
            "quiet", status="spike", primary_cause="volume_increase",
            driver={"dimension": "workspace_id", "value": "wrkspc_01SYNTHSEARCH"},
        )
        result = verify(answer, fixture("quiet"))
        self.assertEqual({item.name for item in result.failures}, {"conclusion", "driver"})

    def test_wrong_driver_is_rejected(self):
        answer = good_answer("cache_regression", driver={"dimension": "workspace_id", "value": "wrkspc_01SYNTHSEARCH"})
        self.assertIn("driver", [item.name for item in verify(answer, fixture("cache_regression")).failures])

    def test_missing_field_is_rejected(self):
        answer = json.loads(recorded("quiet", "good"))
        del answer["headline"]
        self.assertIn("schema", [item.name for item in verify(json.dumps(answer), fixture("quiet")).failures])

    def test_non_object_json_is_rejected(self):
        self.assertFalse(verify("[1, 2]", fixture("quiet")).passed)


class Fallback(unittest.TestCase):
    def test_bad_answer_is_replaced(self):
        analysis = fixture("cache_regression")
        result = decide(analysis, recorded("cache_regression", "wrong_number"))
        self.assertEqual(result.source, SOURCE_FALLBACK)
        self.assertNotIn("9999", json.dumps(result.report))

    def test_good_answer_is_shown(self):
        result = decide(fixture("cache_regression"), recorded("cache_regression", "good"))
        self.assertEqual(result.source, SOURCE_MODEL)

    def test_no_answer_still_gives_a_report(self):
        self.assertEqual(decide(fixture("quiet"), None).source, SOURCE_FALLBACK)

    def test_empty_answer_falls_back(self):
        self.assertEqual(decide(fixture("quiet"), "").source, SOURCE_FALLBACK)

    def test_every_fallback_passes_its_own_verifier(self):
        for name in SCENARIOS:
            with self.subTest(name):
                analysis = fixture(name)
                report = decide(analysis, None).report
                self.assertTrue(verify(json.dumps(report), analysis).passed)


class ModelClient(unittest.TestCase):
    def test_thinking_blocks_are_ignored(self):
        response = {"content": [{"type": "thinking", "thinking": "x"}, {"type": "text", "text": "{}"}]}
        self.assertEqual(client.extract_text(response), "{}")

    def test_missing_key_is_a_clear_error(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(client.SpendClientError):
                client.call_model("hello")

    def test_request_omits_parameters_haiku_rejects(self):
        captured = {}

        def fake_send(request):
            captured["body"] = json.loads(request.data)
            return {"content": [{"type": "text", "text": "ok"}]}

        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test"}), mock.patch.object(client, "_send", fake_send):
            client.call_model("hello")
        self.assertNotIn("temperature", captured["body"])
        self.assertEqual(captured["body"]["model"], "claude-haiku-5-5")


class AnalyticsClient(unittest.TestCase):
    START, END = "2026-10-01T00:00:00Z", "2026-10-18T00:00:00Z"

    def fetch(self, send):
        with mock.patch.dict("os.environ", {"ANTHROPIC_ADMIN_API_KEY": "test"}):
            return client.fetch_analytics(self.START, self.END, send=send)

    def test_pages_are_followed_and_required_headers_sent(self):
        requests = []

        def send(request):
            requests.append(request)
            if "page=" in request.full_url:
                return {"data": [{"n": 2}], "has_more": False, "data_refreshed_at": "w"}
            return {"data": [{"n": 1}], "has_more": True, "next_page": "abc", "data_refreshed_at": "w"}

        result = self.fetch(send)
        self.assertEqual(len(result["usage"]["data"]), 2)
        self.assertEqual(requests[0].get_header("Anthropic-beta"), "analytics-usage-cost-2026-08-24")
        self.assertIn("group_by%5B%5D=workspace_id", requests[0].full_url)
        self.assertIn("time_bucket_width=1d", requests[0].full_url)

    def test_expired_cursor_restarts_from_the_first_page(self):
        calls = []

        def send(request):
            calls.append(request.full_url)
            if len(calls) == 2:
                raise urllib.error.HTTPError(request.full_url, 410, "Gone", {}, io.BytesIO())
            if "page=" in request.full_url:
                return {"data": [{"n": 2}], "has_more": False}
            return {"data": [{"n": 1}], "has_more": True, "next_page": "abc"}

        result = self.fetch(send)
        self.assertEqual(len(result["usage"]["data"]), 2)  # no duplicates from the first attempt

    def test_span_over_31_days_is_refused(self):
        with mock.patch.dict("os.environ", {"ANTHROPIC_ADMIN_API_KEY": "test"}):
            with self.assertRaises(client.SpendClientError):
                client.fetch_analytics("2026-09-01T00:00:00Z", "2026-10-15T00:00:00Z", send=lambda r: {})


class EvalSuite(unittest.TestCase):
    def test_cases_on_disk_match_the_current_prompt(self):
        from build_cases import build_cases

        self.assertEqual(json.loads((ROOT / "evals" / "test_cases.json").read_text()), build_cases())

    def test_scoring_counts_shown_fallback_and_errors(self):
        from score_results import score

        run = {"results": [
            {"test_case": {"name": "quiet"}, "cells": {
                "good": {"response_text": recorded("quiet", "good")},
                "bad": {"response_text": recorded("quiet", "wrong_number")},
                "down": {"error": "timeout", "response_text": None},
            }},
        ]}
        scores = score(run)
        self.assertEqual(scores["good"]["shown"], 1)
        self.assertEqual(scores["bad"]["failures"]["numbers"], 1)
        self.assertEqual(scores["down"]["errors"], 1)


class ClaudeCliRunner(unittest.TestCase):
    CASE = {"name": "quiet", "prompt": "p", "checks": [{"type": "json_valid"}]}

    def test_a_reply_is_scored_in_evalforge_shape(self):
        from run_claude_cli import run_all

        reply = {"is_error": False, "result": recorded("quiet", "good"), "total_cost_usd": 0.01}
        run = run_all([self.CASE], ["model-a"], call=lambda prompt, model: reply)
        cell = run["results"][0]["cells"]["model-a"]
        self.assertEqual(cell["response_text"], reply["result"])
        self.assertTrue(cell["checks"][0]["passed"])

    def test_a_failed_call_becomes_an_error_cell(self):
        from run_claude_cli import run_cell

        def fail(prompt, model):
            raise RuntimeError("not logged in")

        cell = run_cell(self.CASE, "model-a", call=fail)
        self.assertEqual(cell["error"], "not logged in")
        self.assertIsNone(cell["response_text"])

    def test_an_error_reply_becomes_an_error_cell(self):
        from run_claude_cli import run_cell

        cell = run_cell(self.CASE, "model-a", call=lambda p, m: {"is_error": True, "result": "limit reached"})
        self.assertEqual(cell["error"], "limit reached")


class CommandLine(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(ROOT / "explain_spend.py"), *args], capture_output=True, text=True, cwd=ROOT,
        )

    def test_demo_needs_no_key_and_succeeds(self):
        result = self.run_cli("demo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("deterministic fallback", result.stdout)

    def test_demo_all_has_no_unexpected_outcomes(self):
        result = self.run_cli("demo", "--all")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("Unexpected outcomes: 0", result.stdout)

    def test_verify_exit_code_reports_the_outcome(self):
        folder = ROOT / "recorded" / "quiet"
        self.assertEqual(self.run_cli("verify", "--scenario", "quiet", "--answer", str(folder / "good.txt")).returncode, 0)
        self.assertEqual(self.run_cli("verify", "--scenario", "quiet", "--answer", str(folder / "wrong_cause.txt")).returncode, 1)

    def test_verify_summary_hides_rejected_values(self):
        answer = str(ROOT / "recorded" / "cache_regression" / "wrong_number.txt")
        args = ("verify", "--scenario", "cache_regression", "--answer", answer)
        self.assertIn("9999", self.run_cli(*args).stdout)
        summary = self.run_cli(*args, "--summary")
        self.assertNotIn("9999", summary.stdout)
        self.assertIn("[FAIL] numbers", summary.stdout)
        self.assertEqual(summary.returncode, 1)

    def test_project_multiplies_the_daily_increase(self):
        increase = fixture("volume_increase").facts["totals"]["increase_daily_usd"]
        output = self.run_cli("project", "--scenario", "volume_increase", "--days", "30").stdout
        self.assertIn(f"${increase * 30:,.2f}", output)
        self.assertIn("not a forecast", output)

    def test_prompt_command_never_prints_the_verdict(self):
        output = self.run_cli("prompt", "--scenario", "cache_regression").stdout
        self.assertNotIn('"verdict"', output)

    def test_run_without_a_key_still_gives_a_report(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "explain_spend.py"), "run", "--scenario", "quiet"],
            capture_output=True, text=True, cwd=ROOT, env={"PATH": "/usr/bin:/bin"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("deterministic fallback", result.stdout)


if __name__ == "__main__":
    unittest.main()
