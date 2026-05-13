"""Proposals router — unified PI evaluation of collaboration proposals."""

import base64
import json
import logging
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.database import get_db
from src.dependencies import get_current_user
from src.models import (
    AgentRegistry,
    MatchmakerProposal,
    PiProposalEvaluation,
    ThreadDecision,
    User,
)

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="templates")

# NIH score descriptor labels — shown in templates
SCORE_DESCRIPTORS = {
    1: "Exceptional",
    2: "Outstanding",
    3: "Excellent",
    4: "Very Good",
    5: "Good",
    6: "Satisfactory",
    7: "Fair",
    8: "Marginal",
    9: "Poor",
}


# ---------------------------------------------------------------------------
# Token encoding / decoding — hides proposal origin from the URL
# ---------------------------------------------------------------------------


def _encode_token(proposal_type: str, proposal_id: uuid.UUID) -> str:
    prefix = "a" if proposal_type == "agent" else "m"
    raw = f"{prefix}_{proposal_id.hex}".encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _decode_token(token: str) -> tuple[str, uuid.UUID]:
    padding = 4 - len(token) % 4
    if padding != 4:
        token += "=" * padding
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
        prefix, hex_id = raw.split("_", 1)
        if prefix not in ("a", "m"):
            raise ValueError("Unknown prefix")
        proposal_type = "agent" if prefix == "a" else "matchmaker"
        return proposal_type, uuid.UUID(hex=hex_id)
    except ValueError:
        raise
    except Exception:
        raise ValueError("Invalid proposal token")


def _encode_group_token(tokens: list[str]) -> str:
    """Encode an ordered list of proposal tokens into a single URL-safe group token."""
    raw = json.dumps(tokens).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _decode_group_token(group_token: str) -> list[str]:
    padding = 4 - len(group_token) % 4
    if padding != 4:
        group_token += "=" * padding
    try:
        raw = base64.urlsafe_b64decode(group_token.encode())
        return json.loads(raw)
    except Exception:
        raise ValueError("Invalid group token")


def _extract_title(text: str | None) -> str | None:
    if not text:
        return None
    for line in text.strip().splitlines():
        line = line.strip()
        if line.startswith("#"):
            return re.sub(r"^#+\s*", "", line).strip() or None
        if line:
            return line[:120]
    return None


def _template_context(request: Request, user: User, **kwargs) -> dict:
    ctx = {
        "request": request,
        "current_user": user,
        "active_page": "proposals",
        "score_descriptors": SCORE_DESCRIPTORS,
    }
    ctx.update(kwargs)
    return ctx


# ---------------------------------------------------------------------------
# Proposal list
# ---------------------------------------------------------------------------


@router.get("", response_class=HTMLResponse)
async def proposals_list(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Unified list of all collaboration proposals involving the current user."""
    if current_user.access_status != "allowed":
        return RedirectResponse(url="/access-pending", status_code=302)

    success = request.query_params.get("success")

    # 1. Find the user's agents (any status — needed for name resolution)
    agent_result = await db.execute(
        select(AgentRegistry).where(AgentRegistry.user_id == current_user.id)
    )
    user_agents = agent_result.scalars().all()
    user_agent_ids = {a.agent_id for a in user_agents if a.status == "active"}

    # Names this user is known by in CLI-generated matchmaker proposals
    match_names: set[str] = {current_user.name}
    for a in user_agents:
        match_names.add(a.pi_name)

    # 2. Agent proposals involving any of this user's agents
    agent_proposals: list[ThreadDecision] = []
    if user_agent_ids:
        ap_result = await db.execute(
            select(ThreadDecision).where(
                ThreadDecision.outcome == "proposal",
                ThreadDecision.hidden == False,
                (ThreadDecision.agent_a.in_(user_agent_ids))
                | (ThreadDecision.agent_b.in_(user_agent_ids)),
            )
        )
        agent_proposals = ap_result.scalars().all()

    # 3. Matchmaker proposals where this user is PI A or PI B
    #    Matches FK (web-UI path) or name (CLI path — pi_a_id/pi_b_id are NULL)
    mp_result = await db.execute(
        select(MatchmakerProposal)
        .options(selectinload(MatchmakerProposal.pi_a), selectinload(MatchmakerProposal.pi_b))
        .where(
            MatchmakerProposal.hidden == False,
            (MatchmakerProposal.pi_a_id == current_user.id)
            | (MatchmakerProposal.pi_b_id == current_user.id)
            | (MatchmakerProposal.pi_a_name.in_(match_names))
            | (MatchmakerProposal.pi_b_name.in_(match_names))
        )
    )
    matchmaker_proposals = mp_result.scalars().all()

    # 4. Existing evaluations for this user
    td_eval_result = await db.execute(
        select(PiProposalEvaluation.thread_decision_id).where(
            PiProposalEvaluation.user_id == current_user.id,
            PiProposalEvaluation.thread_decision_id.isnot(None),
        )
    )
    evaluated_thread_ids = {r[0] for r in td_eval_result}

    mm_eval_result = await db.execute(
        select(PiProposalEvaluation.matchmaker_proposal_id).where(
            PiProposalEvaluation.user_id == current_user.id,
            PiProposalEvaluation.matchmaker_proposal_id.isnot(None),
        )
    )
    evaluated_mm_ids = {r[0] for r in mm_eval_result}

    # 5. Resolve collaborator names for agent proposals
    all_agent_ids: set[str] = set()
    for p in agent_proposals:
        all_agent_ids.add(p.agent_a)
        all_agent_ids.add(p.agent_b)

    agent_reg_map: dict[str, AgentRegistry] = {}
    if all_agent_ids:
        ar_result = await db.execute(
            select(AgentRegistry)
            .options(selectinload(AgentRegistry.user))
            .where(AgentRegistry.agent_id.in_(all_agent_ids))
        )
        agent_reg_map = {a.agent_id: a for a in ar_result.scalars().all()}

    # Build user_id → AgentRegistry map for matchmaker collaborator profile links
    mm_collab_user_ids = {
        uid
        for p in matchmaker_proposals
        for uid in (p.pi_a_id, p.pi_b_id)
        if uid is not None
    }
    user_to_agent_map: dict = {}
    if mm_collab_user_ids:
        ua_result = await db.execute(
            select(AgentRegistry).where(AgentRegistry.user_id.in_(mm_collab_user_ids))
        )
        user_to_agent_map = {a.user_id: a for a in ua_result.scalars().all()}

    # 6. Build flat items list
    flat_items: list[dict] = []

    for p in agent_proposals:
        other_id = p.agent_b if p.agent_a in user_agent_ids else p.agent_a
        other_ar = agent_reg_map.get(other_id)
        if other_ar and other_ar.user:
            collaborator = other_ar.user.name
        elif other_ar:
            collaborator = other_ar.pi_name or other_id
        else:
            collaborator = other_id

        flat_items.append(
            {
                "token": _encode_token("agent", p.id),
                "title": _extract_title(p.summary_text) or "Collaboration Proposal",
                "collaborator": collaborator,
                "collaborator_agent_id": other_ar.agent_id if other_ar else None,
                "has_evaluation": p.id in evaluated_thread_ids,
            }
        )

    for p in matchmaker_proposals:
        is_pi_a = (p.pi_a_id == current_user.id) or (p.pi_a_name in match_names)
        collaborator = p.name_b if is_pi_a else p.name_a
        collab_user_id = p.pi_b_id if is_pi_a else p.pi_a_id
        collab_ar = user_to_agent_map.get(collab_user_id) if collab_user_id else None
        flat_items.append(
            {
                "token": _encode_token("matchmaker", p.id),
                "title": p.title,
                "collaborator": collaborator,
                "collaborator_agent_id": collab_ar.agent_id if collab_ar else None,
                "has_evaluation": p.id in evaluated_mm_ids,
            }
        )

    # 7. Group by collaborator
    groups_dict: dict[str, dict] = {}
    for item in flat_items:
        collab = item["collaborator"]
        if collab not in groups_dict:
            groups_dict[collab] = {
                "tokens": [],
                "evaluated_count": 0,
                "total": 0,
                "collaborator_agent_id": item.get("collaborator_agent_id"),
            }
        elif not groups_dict[collab]["collaborator_agent_id"]:
            groups_dict[collab]["collaborator_agent_id"] = item.get("collaborator_agent_id")
        groups_dict[collab]["tokens"].append(item["token"])
        groups_dict[collab]["total"] += 1
        if item["has_evaluation"]:
            groups_dict[collab]["evaluated_count"] += 1

    # Stable token order within each group; sort groups by collaborator name
    groups = []
    for collab, g in sorted(groups_dict.items()):
        g["tokens"].sort()
        groups.append(
            {
                "collaborator": collab,
                "collaborator_agent_id": g["collaborator_agent_id"],
                "total": g["total"],
                "evaluated_count": g["evaluated_count"],
                "group_token": _encode_group_token(g["tokens"]),
                "all_evaluated": g["evaluated_count"] == g["total"],
            }
        )

    return templates.TemplateResponse(
        request,
        "proposals/list.html",
        _template_context(
            request,
            current_user,
            groups=groups,
            flash_message="Evaluation submitted." if success else None,
            flash_type="success" if success else None,
        ),
    )


# ---------------------------------------------------------------------------
# Evaluation form — GET
# ---------------------------------------------------------------------------


@router.get("/{token}/evaluate", response_class=HTMLResponse)
async def evaluate_form(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.access_status != "allowed":
        return RedirectResponse(url="/access-pending", status_code=302)

    try:
        proposal_type, proposal_id = _decode_token(token)
    except ValueError:
        raise HTTPException(status_code=404, detail="Proposal not found")

    title, collaborator, body_md = await _load_proposal_display(
        proposal_type, proposal_id, current_user, db
    )

    # Load existing evaluation for pre-fill
    if proposal_type == "agent":
        eval_filter = (
            PiProposalEvaluation.user_id == current_user.id,
            PiProposalEvaluation.thread_decision_id == proposal_id,
        )
    else:
        eval_filter = (
            PiProposalEvaluation.user_id == current_user.id,
            PiProposalEvaluation.matchmaker_proposal_id == proposal_id,
        )

    eval_result = await db.execute(select(PiProposalEvaluation).where(*eval_filter))
    existing = eval_result.scalar_one_or_none()

    return templates.TemplateResponse(
        request,
        "proposals/evaluate.html",
        _template_context(
            request,
            current_user,
            token=token,
            title=title,
            collaborator=collaborator,
            body_md=body_md,
            evaluation=existing,
        ),
    )


# ---------------------------------------------------------------------------
# Evaluation form — POST (upsert)
# ---------------------------------------------------------------------------


@router.post("/{token}/evaluate")
async def evaluate_submit(
    token: str,
    request: Request,
    score_overall_impact: int = Form(...),
    comments_overall: str = Form(""),
    # Criterion scores are optional — hidden from the PI form but preserved in DB
    score_significance: int | None = Form(None),
    score_innovation: int | None = Form(None),
    score_approach: int | None = Form(None),
    score_investigators: int | None = Form(None),
    score_environment: int | None = Form(None),
    comments_significance: str = Form(""),
    comments_innovation: str = Form(""),
    comments_approach: str = Form(""),
    comments_investigators: str = Form(""),
    comments_environment: str = Form(""),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.access_status != "allowed":
        return RedirectResponse(url="/access-pending", status_code=302)

    try:
        proposal_type, proposal_id = _decode_token(token)
    except ValueError:
        raise HTTPException(status_code=404, detail="Proposal not found")

    # Validate access (raises 403 if not authorized)
    await _load_proposal_display(proposal_type, proposal_id, current_user, db)

    # Validate overall impact score
    if not 1 <= score_overall_impact <= 9:
        raise HTTPException(status_code=400, detail="Overall impact score must be 1–9")

    # Validate criterion scores only if provided
    criterion_scores = {
        "significance": score_significance,
        "innovation": score_innovation,
        "approach": score_approach,
        "investigators": score_investigators,
        "environment": score_environment,
    }
    for name, val in criterion_scores.items():
        if val is not None and not 1 <= val <= 9:
            raise HTTPException(status_code=400, detail=f"Score for {name} must be 1–9")

    # Upsert
    if proposal_type == "agent":
        eval_filter = (
            PiProposalEvaluation.user_id == current_user.id,
            PiProposalEvaluation.thread_decision_id == proposal_id,
        )
    else:
        eval_filter = (
            PiProposalEvaluation.user_id == current_user.id,
            PiProposalEvaluation.matchmaker_proposal_id == proposal_id,
        )

    existing_result = await db.execute(select(PiProposalEvaluation).where(*eval_filter))
    ev = existing_result.scalar_one_or_none()
    is_update = ev is not None

    now = datetime.now(timezone.utc)

    if is_update:
        ev.score_significance = score_significance
        ev.score_innovation = score_innovation
        ev.score_approach = score_approach
        ev.score_investigators = score_investigators
        ev.score_environment = score_environment
        ev.score_overall_impact = score_overall_impact
        ev.comments_significance = comments_significance.strip() or None
        ev.comments_innovation = comments_innovation.strip() or None
        ev.comments_approach = comments_approach.strip() or None
        ev.comments_investigators = comments_investigators.strip() or None
        ev.comments_environment = comments_environment.strip() or None
        ev.comments_overall = comments_overall.strip()
        ev.updated_at = now
    else:
        ev = PiProposalEvaluation(
            user_id=current_user.id,
            proposal_type=proposal_type,
            thread_decision_id=proposal_id if proposal_type == "agent" else None,
            matchmaker_proposal_id=proposal_id if proposal_type == "matchmaker" else None,
            score_significance=score_significance,       # None when not submitted
            score_innovation=score_innovation,
            score_approach=score_approach,
            score_investigators=score_investigators,
            score_environment=score_environment,
            score_overall_impact=score_overall_impact,
            comments_significance=comments_significance.strip() or None,
            comments_innovation=comments_innovation.strip() or None,
            comments_approach=comments_approach.strip() or None,
            comments_investigators=comments_investigators.strip() or None,
            comments_environment=comments_environment.strip() or None,
            comments_overall=comments_overall.strip(),
        )
        db.add(ev)

    await db.commit()

    action = "updated" if is_update else "submitted"
    logger.info(
        "PiProposalEvaluation %s: user=%s (id=%s) proposal_type=%s proposal_id=%s "
        "overall_impact=%d at=%s",
        action,
        current_user.name,
        current_user.id,
        proposal_type,
        proposal_id,
        score_overall_impact,
        now.isoformat(),
    )

    return RedirectResponse(url="/proposals?success=1", status_code=302)


# ---------------------------------------------------------------------------
# Shared helper — load proposal and verify PI access
# ---------------------------------------------------------------------------


async def _load_proposal_display(
    proposal_type: str,
    proposal_id: uuid.UUID,
    current_user: User,
    db: AsyncSession,
) -> tuple[str, str, str]:
    """Return (title, collaborator_name, body_md) or raise HTTPException."""
    if proposal_type == "agent":
        result = await db.execute(
            select(ThreadDecision).where(ThreadDecision.id == proposal_id)
        )
        td = result.scalar_one_or_none()
        if not td:
            raise HTTPException(status_code=404, detail="Proposal not found")

        # Verify current user owns one of the two agents
        ar_result = await db.execute(
            select(AgentRegistry)
            .options(selectinload(AgentRegistry.user))
            .where(
                AgentRegistry.user_id == current_user.id,
                AgentRegistry.agent_id.in_([td.agent_a, td.agent_b]),
            )
        )
        my_agent = ar_result.scalars().first()
        if not my_agent:
            raise HTTPException(status_code=403, detail="Not authorized")

        other_id = td.agent_b if td.agent_a == my_agent.agent_id else td.agent_a
        other_ar_result = await db.execute(
            select(AgentRegistry)
            .options(selectinload(AgentRegistry.user))
            .where(AgentRegistry.agent_id == other_id)
        )
        other_ar = other_ar_result.scalar_one_or_none()
        if other_ar and other_ar.user:
            collaborator = other_ar.user.name
        elif other_ar:
            collaborator = other_ar.pi_name or other_id
        else:
            collaborator = other_id

        title = _extract_title(td.summary_text) or "Collaboration Proposal"
        body_md = td.summary_text or ""
        return title, collaborator, body_md

    else:  # matchmaker
        result = await db.execute(
            select(MatchmakerProposal)
            .options(selectinload(MatchmakerProposal.pi_a), selectinload(MatchmakerProposal.pi_b))
            .where(MatchmakerProposal.id == proposal_id)
        )
        mp = result.scalar_one_or_none()
        if not mp:
            raise HTTPException(status_code=404, detail="Proposal not found")

        # Resolve all names this user may appear under (FK or CLI name)
        ar_result = await db.execute(
            select(AgentRegistry).where(AgentRegistry.user_id == current_user.id)
        )
        my_agent = ar_result.scalar_one_or_none()
        match_names: set[str] = {current_user.name}
        if my_agent:
            match_names.add(my_agent.pi_name)

        is_pi_a = (mp.pi_a_id == current_user.id) or (mp.pi_a_name in match_names)
        is_pi_b = (mp.pi_b_id == current_user.id) or (mp.pi_b_name in match_names)
        if not (is_pi_a or is_pi_b):
            raise HTTPException(status_code=403, detail="Not authorized")

        collaborator = mp.name_b if is_pi_a else mp.name_a
        return mp.title, collaborator, mp.proposal_md


# ---------------------------------------------------------------------------
# Group evaluation flow — sequential step-by-step through all proposals
# with a collaborator (GET + POST)
# ---------------------------------------------------------------------------


@router.get("/group/{group_token}/{step}", response_class=HTMLResponse)
async def evaluate_group_form(
    group_token: str,
    step: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.access_status != "allowed":
        return RedirectResponse(url="/access-pending", status_code=302)

    try:
        tokens = _decode_group_token(group_token)
    except ValueError:
        raise HTTPException(status_code=404, detail="Group not found")

    if not tokens or step < 0 or step >= len(tokens):
        raise HTTPException(status_code=404, detail="Step out of range")

    token = tokens[step]
    try:
        proposal_type, proposal_id = _decode_token(token)
    except ValueError:
        raise HTTPException(status_code=404, detail="Proposal not found")

    title, collaborator, body_md = await _load_proposal_display(
        proposal_type, proposal_id, current_user, db
    )

    if proposal_type == "agent":
        eval_filter = (
            PiProposalEvaluation.user_id == current_user.id,
            PiProposalEvaluation.thread_decision_id == proposal_id,
        )
    else:
        eval_filter = (
            PiProposalEvaluation.user_id == current_user.id,
            PiProposalEvaluation.matchmaker_proposal_id == proposal_id,
        )
    eval_result = await db.execute(select(PiProposalEvaluation).where(*eval_filter))
    existing = eval_result.scalar_one_or_none()

    return templates.TemplateResponse(
        request,
        "proposals/evaluate.html",
        _template_context(
            request,
            current_user,
            token=token,
            title=title,
            collaborator=collaborator,
            body_md=body_md,
            evaluation=existing,
            group_token=group_token,
            step=step,
            total_steps=len(tokens),
        ),
    )


@router.get("/group/{group_token}", response_class=HTMLResponse)
async def evaluate_group_start(
    group_token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Redirect to first step of a group evaluation."""
    return RedirectResponse(url=f"/proposals/group/{group_token}/0", status_code=302)


@router.post("/group/{group_token}/{step}")
async def evaluate_group_submit(
    group_token: str,
    step: int,
    request: Request,
    score_overall_impact: int = Form(...),
    comments_overall: str = Form(""),
    score_significance: int | None = Form(None),
    score_innovation: int | None = Form(None),
    score_approach: int | None = Form(None),
    score_investigators: int | None = Form(None),
    score_environment: int | None = Form(None),
    comments_significance: str = Form(""),
    comments_innovation: str = Form(""),
    comments_approach: str = Form(""),
    comments_investigators: str = Form(""),
    comments_environment: str = Form(""),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.access_status != "allowed":
        return RedirectResponse(url="/access-pending", status_code=302)

    try:
        tokens = _decode_group_token(group_token)
    except ValueError:
        raise HTTPException(status_code=404, detail="Group not found")

    if not tokens or step < 0 or step >= len(tokens):
        raise HTTPException(status_code=404, detail="Step out of range")

    token = tokens[step]
    try:
        proposal_type, proposal_id = _decode_token(token)
    except ValueError:
        raise HTTPException(status_code=404, detail="Proposal not found")

    await _load_proposal_display(proposal_type, proposal_id, current_user, db)

    if not 1 <= score_overall_impact <= 9:
        raise HTTPException(status_code=400, detail="Overall impact score must be 1–9")
    for name, val in {
        "significance": score_significance,
        "innovation": score_innovation,
        "approach": score_approach,
        "investigators": score_investigators,
        "environment": score_environment,
    }.items():
        if val is not None and not 1 <= val <= 9:
            raise HTTPException(status_code=400, detail=f"Score for {name} must be 1–9")
    if proposal_type == "agent":
        eval_filter = (
            PiProposalEvaluation.user_id == current_user.id,
            PiProposalEvaluation.thread_decision_id == proposal_id,
        )
    else:
        eval_filter = (
            PiProposalEvaluation.user_id == current_user.id,
            PiProposalEvaluation.matchmaker_proposal_id == proposal_id,
        )

    existing_result = await db.execute(select(PiProposalEvaluation).where(*eval_filter))
    ev = existing_result.scalar_one_or_none()
    is_update = ev is not None
    now = datetime.now(timezone.utc)

    if is_update:
        ev.score_significance = score_significance
        ev.score_innovation = score_innovation
        ev.score_approach = score_approach
        ev.score_investigators = score_investigators
        ev.score_environment = score_environment
        ev.score_overall_impact = score_overall_impact
        ev.comments_significance = comments_significance.strip() or None
        ev.comments_innovation = comments_innovation.strip() or None
        ev.comments_approach = comments_approach.strip() or None
        ev.comments_investigators = comments_investigators.strip() or None
        ev.comments_environment = comments_environment.strip() or None
        ev.comments_overall = comments_overall.strip()
        ev.updated_at = now
    else:
        ev = PiProposalEvaluation(
            user_id=current_user.id,
            proposal_type=proposal_type,
            thread_decision_id=proposal_id if proposal_type == "agent" else None,
            matchmaker_proposal_id=proposal_id if proposal_type == "matchmaker" else None,
            score_significance=score_significance,
            score_innovation=score_innovation,
            score_approach=score_approach,
            score_investigators=score_investigators,
            score_environment=score_environment,
            score_overall_impact=score_overall_impact,
            comments_significance=comments_significance.strip() or None,
            comments_innovation=comments_innovation.strip() or None,
            comments_approach=comments_approach.strip() or None,
            comments_investigators=comments_investigators.strip() or None,
            comments_environment=comments_environment.strip() or None,
            comments_overall=comments_overall.strip(),
        )
        db.add(ev)

    await db.commit()

    logger.info(
        "PiProposalEvaluation %s: user=%s proposal_type=%s proposal_id=%s impact=%d step=%d/%d",
        "updated" if is_update else "submitted",
        current_user.name, proposal_type, proposal_id, score_overall_impact,
        step + 1, len(tokens),
    )

    next_step = step + 1
    if next_step < len(tokens):
        return RedirectResponse(
            url=f"/proposals/group/{group_token}/{next_step}", status_code=302
        )
    return RedirectResponse(url="/proposals?success=1", status_code=302)
