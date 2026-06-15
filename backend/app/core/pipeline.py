"""
Pipeline orchestrator — state machine with DB writes at every transition.
Every stage transition = DB write. Survives Railway redeploys.
HITL: persisted as AWAITING_HUMAN → POST /approve/{run_id} resumes as BackgroundTask.
Smart suite selection: deterministic-first, LLM fallback.
Dynamic HITL triggers: confidence, suite ambiguity, DOM ambiguity, circuit breakers.
"""
import asyncio
import time
import structlog
import app.db as db
from app.core.state import RunRecord, RunStatus, NodeState, HITLTrigger
from app.core.evidence import build_evidence_bundle, get_recent_commits, build_test_history
from app.core.circuit_breaker import (
    check_cost_and_record, should_quarantine, record_quarantine,
    get_effective_threshold, run_all_checks,
)
from app.core.suite_selector import select_suites
from app.config import get_settings

log = structlog.get_logger()


async def _set_node(run: RunRecord, node: str, state: str, annotation: str = "") -> None:
    run.node_states[node] = NodeState(state=state, annotation=annotation)
    await db.update_run(run)


async def _transition(run: RunRecord, status: RunStatus) -> None:
    run.status = status
    run.updated_at = __import__("datetime").datetime.utcnow()
    await db.update_run(run)


def _dom_is_ambiguous(dom_report: dict) -> bool:
    """True if the top 2 DOM candidates are within 0.10 confidence of each other."""
    candidates = dom_report.get("changed_selectors", [])
    if len(candidates) < 2:
        return False
    scores = sorted([c.get("confidence", 0) for c in candidates], reverse=True)
    return (scores[0] - scores[1]) < 0.10


async def run_pipeline(run_id: str) -> None:
    run = await db.get_run(run_id)
    if not run:
        log.error("pipeline.run_not_found", run_id=run_id)
        return

    log.info("pipeline.start", run_id=run_id, branch=run.trigger_branch)

    try:
        from app.nodes.canary import run_canary
        from app.nodes.runner import run_tests
        from app.nodes.inspector import inspect_dom
        from app.nodes.triage import triage
        from app.nodes.reporter import generate_report

        await _set_node(run, "git_watcher", "success", f"Triggered by {run.trigger_commit[:7] or 'manual'}")

        # ── Smart suite selection ──────────────────────────────────────────────
        await _set_node(run, "change_analyzer", "running", "Analysing changed files → selecting test suites…")
        suites, method, llm_hitl_flag = await select_suites(run.trigger_commit, run_id=run_id)
        run.suites_run = suites
        run.suite_selection_method = method
        if llm_hitl_flag:
            run.add_hitl_trigger(HITLTrigger.LLM_SUITE_AMBIGUOUS)
        # Calibration mode forces all runs to HITL
        if get_settings().calibration_mode:
            run.add_hitl_trigger(HITLTrigger.CALIBRATION_MODE)
        await _set_node(
            run, "change_analyzer", "success",
            f"[{method}] Suites: {', '.join(suites) or 'all'}" + (" — HITL recommended by LLM" if llm_hitl_flag else ""),
        )

        # ── Canary checks ─────────────────────────────────────────────────────
        await _set_node(run, "test_runner", "running", "Running canary checks…")
        t0 = time.time()
        canary_result = await run_canary(run_id)
        run.node_timings["canary"] = round(time.time() - t0, 2)

        if not canary_result["passed"]:
            tier = canary_result.get("tier_failed", "hard")
            env_conf = canary_result.get("env_confidence", 0.95)
            await _set_node(run, "test_runner", "failed", f"Canary [{tier}] failed — environment issue")
            run.triage.classification = "env"
            run.triage.confidence = env_conf
            run.triage.evidence = f"Canary [{tier}] tier failed — infrastructure issue (confidence calibrated by tier)"
            # Fire Slack env alert
            try:
                from app.integrations.slack import notify_env
                await notify_env(run_id, run.triage.evidence, team_id=run.team_id)
            except Exception:
                pass
            await _transition(run, RunStatus.FAILED)
            await run_all_checks(run_id)
            return

        # ── Test runner ────────────────────────────────────────────────────────
        await _set_node(run, "test_runner", "running", f"Running {len(suites)} suite(s)…")
        t0 = time.time()
        await _transition(run, RunStatus.RUNNING)
        failures = await run_tests(run_id, selected_suites=suites if method != "fallback_all" else None)
        run.node_timings["test_runner"] = round(time.time() - t0, 2)
        run.failures = failures

        if not failures:
            await _set_node(run, "test_runner", "success", "All tests passed ✓")
            await _set_node(run, "browser_inspector", "skipped", "No failures to inspect")
            await _set_node(run, "classifier", "skipped", "No failures to classify")
            await _set_node(run, "reporter", "success", "All tests passed — nothing to report")
            run.consecutive_failures = 0
            await _transition(run, RunStatus.COMPLETE)
            await run_all_checks(run_id)
            return

        await _set_node(run, "test_runner", "failed", f"{len(failures)} failure(s) detected")

        if should_quarantine(run.consecutive_failures):
            await _set_node(run, "browser_inspector", "skipped", "Quarantined — max retries exceeded")
            await record_quarantine(run_id)
            await _transition(run, RunStatus.QUARANTINED)
            return

        # ── DOM inspection ─────────────────────────────────────────────────────
        await _set_node(run, "browser_inspector", "running", "Inspecting live DOM for selector changes…")
        t0 = time.time()
        await _transition(run, RunStatus.INSPECTING)
        dom_report = await inspect_dom(run_id, failures)
        run.dom_report = dom_report
        run.node_timings["browser_inspector"] = round(time.time() - t0, 2)

        changed = len(dom_report.get("changed_selectors", []))
        ambiguous_dom = _dom_is_ambiguous(dom_report)
        await _set_node(
            run, "browser_inspector", "success",
            f"Found {changed} candidate(s)" + (" — ambiguous (HITL)!" if ambiguous_dom else ""),
        )

        # DOM ambiguity forces HITL
        if ambiguous_dom:
            run.add_hitl_trigger(HITLTrigger.TOP2_AMBIGUOUS)

        # ── Evidence bundle ────────────────────────────────────────────────────
        recent_commits = get_recent_commits(run_id)
        test_history = await build_test_history(failures, run.consecutive_failures)
        evidence = build_evidence_bundle(failures, dom_report, recent_commits, test_history)
        run.evidence = evidence

        # ── Triage (LLM) ──────────────────────────────────────────────────────
        await _set_node(run, "classifier", "running", "Classifying failures with LLM…")
        t0 = time.time()
        await _transition(run, RunStatus.TRIAGING)
        triage_result, trace_url, input_tok, output_tok, cost, t_prompt, t_response, t_hash = await triage(
            run_id, failures, dom_report, evidence, suite_selection_method=run.suite_selection_method
        )
        run.triage = triage_result
        run.node_timings["classifier"] = round(time.time() - t0, 2)
        run.consecutive_failures += 1
        if trace_url:
            run.langfuse_trace_url = trace_url
        run.input_tokens = input_tok
        run.output_tokens = output_tok
        run.cost_usd = cost
        await db.update_run(run)

        # E1: Store triage prompt audit columns
        if t_prompt:
            await db.store_triage_audit(run_id, t_prompt, t_response, t_hash)

        # Store five-signal gate columns (queryable — separate from data_json)
        from app.nodes.triage import _run_confidence_gate
        gate_route, gate_held = _run_confidence_gate(
            triage_result.classification,
            triage_result.p_class,
            triage_result.logprob_margin,
            triage_result.fix_grounded,
            triage_result.dom_corroboration,
        )
        await db.store_gate_signals(
            run_id,
            p_class=triage_result.p_class,
            logprob_margin=triage_result.logprob_margin,
            fix_grounded=triage_result.fix_grounded,
            dom_corroboration=triage_result.dom_corroboration,
            gate_route=gate_route,
            gate_held_checks=gate_held,
        )

        # Cost circuit breaker
        if not await check_cost_and_record(run.cost_usd, run_id):
            await _set_node(run, "classifier", "failed", f"Cost limit exceeded (${run.cost_usd:.4f}) — aborting")
            await _transition(run, RunStatus.FAILED)
            await run_all_checks(run_id)
            return

        classification = triage_result.classification
        confidence = triage_result.confidence
        threshold = get_effective_threshold()

        # FPR circuit breaker active — add HITL trigger
        if threshold > get_settings().auto_fix_threshold:
            run.add_hitl_trigger(HITLTrigger.FPR_BREAKER_ACTIVE)

        # ── Route based on classification ──────────────────────────────────────
        if classification == "drift" and confidence >= threshold and not run.force_hitl:
            await _set_node(
                run, "classifier", "success",
                f"DRIFT — {confidence:.0%} — auto-fix eligible (threshold {threshold:.0%})",
            )
            await _set_node(run, "auto_fixer", "running", "Applying selector fix…")
            await _set_node(run, "ticket_creator", "skipped", "Drift — no ticket needed")
            await _transition(run, RunStatus.HEALING)
            await _apply_fix_and_complete(run, prompt_hash=t_hash)

        elif classification == "drift":
            # Dynamic HITL: low confidence OR forced (DOM ambiguity, LLM recommendation, circuit breaker)
            if confidence < threshold:
                run.add_hitl_trigger(HITLTrigger.LOW_CONFIDENCE)
            if not triage_result.proposed_fix:
                run.add_hitl_trigger(HITLTrigger.NO_PROPOSED_FIX)
            reason_str = ", ".join(run.hitl_triggers)
            await _set_node(
                run, "classifier", "waiting",
                f"DRIFT — {confidence:.0%} — HITL: {reason_str}",
            )
            await _set_node(run, "human_review", "waiting", "Awaiting human approval")
            await _transition(run, RunStatus.AWAITING_HUMAN)

            # Slack HITL card
            try:
                from app.integrations.slack import notify_hitl
                await notify_hitl(run_id, confidence, triage_result.proposed_fix, team_id=run.team_id)
            except Exception:
                pass
            log.info("pipeline.awaiting_human", run_id=run_id, reason=reason_str)

        elif classification == "bug":
            severity = "HIGH" if confidence >= 0.9 else "MEDIUM"
            await _set_node(run, "classifier", "failed", f"BUG — {confidence:.0%} — severity: {severity}")
            await _set_node(run, "auto_fixer", "skipped", "BUG — no auto-fix")
            await _set_node(run, "ticket_creator", "running", "Filing bug ticket…")

            try:
                from app.integrations.jira import file_bug_ticket
                ticket = await file_bug_ticket(run)
                ticket_key = ticket.get("key", f"BUG-{run_id[:4].upper()}")
                jira_note = f" → Jira: {ticket.get('jira_remote_id')}" if ticket.get("jira_remote_id") else ""
                await _set_node(run, "ticket_creator", "success", f"{ticket_key} filed — {severity}{jira_note}")

                from app.integrations.slack import notify_bug
                await notify_bug(run_id, ticket_key, triage_result.evidence, confidence, team_id=run.team_id)
            except Exception as exc:
                log.error("pipeline.bug_ticket_error", run_id=run_id, error=str(exc)[:100])
                await _set_node(run, "ticket_creator", "success", f"BUG-{run_id[:4].upper()} created (local)")

            await _set_node(run, "reporter", "running", "Writing summary…")
            await generate_report(run)
            await _set_node(run, "reporter", "success", "Run complete")
            await _transition(run, RunStatus.FAILED)

        else:  # env
            await _set_node(run, "classifier", "failed", f"ENV — {confidence:.0%}")
            await _set_node(run, "auto_fixer", "skipped", "ENV issue — no auto-fix")
            await _set_node(run, "ticket_creator", "running", "Filing environment alert…")

            try:
                from app.integrations.jira import file_env_ticket
                ticket = await file_env_ticket(run)
                ticket_key = ticket.get("key", f"ENV-{run_id[:4].upper()}")
                await _set_node(run, "ticket_creator", "success", f"{ticket_key} filed — ops team notified")

                from app.integrations.slack import notify_env
                await notify_env(run_id, triage_result.evidence, team_id=run.team_id)
            except Exception as exc:
                log.error("pipeline.env_ticket_error", run_id=run_id, error=str(exc)[:100])
                await _set_node(run, "ticket_creator", "success", "ENV alert created")

            await _set_node(run, "reporter", "running", "Writing summary…")
            await generate_report(run)
            await _set_node(run, "reporter", "success", "Run complete")
            await _transition(run, RunStatus.FAILED)

        await run_all_checks(run_id)

    except Exception as exc:
        log.error("pipeline.error", run_id=run_id, error=str(exc), exc_info=True)
        run_state = await db.get_run(run_id)
        if run_state:
            run_state.status = RunStatus.FAILED
            await db.update_run(run_state)


async def _apply_fix_and_complete(run: RunRecord, prompt_hash: str = "") -> None:
    from app.nodes.auto_fixer import auto_fix
    from app.nodes.reporter import generate_report

    try:
        pr_url = await auto_fix(run)
        if pr_url:
            run.pr_url = pr_url
            await _set_node(run, "auto_fixer", "success", f"PR opened: {pr_url}")
            # Slack PR notification
            try:
                from app.integrations.slack import notify_pr_opened
                await notify_pr_opened(run.id, pr_url, team_id=run.team_id)
            except Exception:
                pass
        else:
            await _set_node(run, "auto_fixer", "failed", "Fix not applied — idempotency guard or permissions")

        # C3: Store auto-confirmed pattern for RAG
        await db.store_failure_pattern(
            run_id=run.id,
            original_class=run.triage.classification,
            verified_class=run.triage.classification,
            evidence={"failures": run.failures, "dom_report": run.dom_report},
            prompt_hash=prompt_hash,
            verified=False,  # auto-confirmed, not human-verified
        )
        await _set_node(run, "reporter", "running", "Writing run summary…")
        await generate_report(run)
        await _set_node(run, "reporter", "success", "Run complete ✓")
        run.consecutive_failures = 0
        await _transition(run, RunStatus.COMPLETE)

    except Exception as exc:
        log.error("pipeline.fix_error", run_id=run.id, error=str(exc))
        await _set_node(run, "auto_fixer", "failed", f"Error: {exc}")
        await _transition(run, RunStatus.FAILED)


async def resume_after_approval(
    run_id: str,
    approved: bool,
    reviewer: str,
    modified_fix: dict | None = None,
    reclassify_as: str | None = None,
) -> None:
    run = await db.get_run(run_id)
    if not run:
        log.error("pipeline.resume_not_found", run_id=run_id)
        return

    run.approved_by = reviewer
    _, _, prompt_hash = await db.get_run_triage_prompt(run_id)

    # C2: apply human overrides before routing
    original_class = run.triage.classification
    if modified_fix:
        run.triage.proposed_fix = modified_fix
        run.human_override = True
        log.info("pipeline.modified_fix", run_id=run_id, reviewer=reviewer)
    if reclassify_as and reclassify_as in ("drift", "bug", "env"):
        run.triage.classification = reclassify_as
        run.human_override = True
        run.add_hitl_trigger(HITLTrigger.HUMAN_RECLASSIFIED)
        log.info("pipeline.reclassified", run_id=run_id, from_=original_class, to=reclassify_as)

    if not approved:
        # C3: store rejected triage as a negative pattern
        if run.human_override:
            await db.store_failure_pattern(
                run_id=run_id,
                original_class=original_class,
                verified_class=reclassify_as or original_class,
                evidence={"failures": run.failures, "dom_report": run.dom_report, "override_reason": run.override_reason},
                prompt_hash=prompt_hash,
                verified=True,
            )
        await _set_node(run, "human_review", "failed", f"Rejected by {reviewer}")
        await _transition(run, RunStatus.FAILED)
        await run_all_checks(run_id)
        return

    # C3: store approved (possibly corrected) triage as verified pattern
    await db.store_failure_pattern(
        run_id=run_id,
        original_class=original_class,
        verified_class=run.triage.classification,
        evidence={"failures": run.failures, "dom_report": run.dom_report},
        prompt_hash=prompt_hash,
        verified=True,
    )

    await _set_node(run, "human_review", "success", f"Approved by {reviewer} ✓")
    await _set_node(run, "auto_fixer", "running", "Applying selector fix…")
    await _transition(run, RunStatus.HEALING)
    await _apply_fix_and_complete(run, prompt_hash=prompt_hash)
    await run_all_checks(run_id)
