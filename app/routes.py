import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app.activity import InvalidDayError, build_activity_heatmap, build_day_detail, parse_day
from app.campaigns import build_campaign_activity, build_campaign_list, build_campaign_overview, last_activity_for
from app.csrf import csrf_field, csrf_token
from app.db import DEMO_MODE, get_session
from app.export import (
    build_hunt_markdown,
    build_json_export,
    create_sqlite_backup,
    markdown_export_filename,
)
from app.insights import build_insights
from app.lifecycle import lifecycle_view
from app.markdown_render import render_markdown
from app.models import Campaign, CampaignHunt, CampaignStatus, Exclusion, Hunt, HuntStatus, RetirementReason, Run, RunOutcome
from app.services import (
    DELETE_CONFIRMATION_WORD,
    create_campaign,
    create_hunt,
    delete_hunt,
    link_hunt_to_campaign,
    log_run,
    move_hunt,
    reactivate_hunt,
    retire_hunt,
    set_campaign_status,
    unlink_hunt_from_campaign,
    update_campaign,
    update_campaign_conclusion,
    update_hunt,
)

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
templates.env.filters["markdown"] = render_markdown
templates.env.globals["demo_mode"] = DEMO_MODE
templates.env.globals["lifecycle_view"] = lifecycle_view
templates.env.globals["csrf_token"] = csrf_token
templates.env.globals["csrf_field"] = csrf_field

RetireContext = Literal["board", "detail"]


def _hunt_main_response(request: Request, hunt: Hunt):
    return templates.TemplateResponse(
        request, "partials/hunt_main.html", {"hunt": hunt, "run_outcomes": list(RunOutcome)}
    )


def _new_hunt_form_response(
    request: Request,
    *,
    title: str,
    hypothesis: str,
    priority: str,
    error: str | None,
    status_code: int = 200,
):
    return templates.TemplateResponse(
        request,
        "hunt_new.html",
        {"title": title, "hypothesis": hypothesis, "priority": priority, "error": error},
        status_code=status_code,
    )


def _hunt_form_values(hunt: Hunt) -> dict[str, str]:
    return {
        "title": hunt.title,
        "hypothesis": hunt.hypothesis,
        "priority": str(hunt.priority),
        "data_sources": hunt.data_sources,
        "attack_techniques": hunt.attack_techniques,
        "query": hunt.query,
        "notes": hunt.notes,
        "cadence_days": "" if hunt.cadence_days is None else str(hunt.cadence_days),
        "next_run": "" if hunt.next_run is None else hunt.next_run.isoformat(),
    }


def _edit_hunt_form_response(
    request: Request,
    *,
    hunt_id: int,
    hunt_title: str,
    values: dict[str, str],
    error: str | None,
    status_code: int = 200,
):
    return templates.TemplateResponse(
        request,
        "hunt_edit.html",
        {"hunt_id": hunt_id, "hunt_title": hunt_title, "values": values, "error": error},
        status_code=status_code,
    )


def _get_hunt_or_404(session: Session, hunt_id: int) -> Hunt:
    hunt = session.get(Hunt, hunt_id)
    if hunt is None:
        raise HTTPException(status_code=404, detail="Hunt not found")
    return hunt


def _board_columns(session: Session) -> list[tuple[HuntStatus, list[Hunt]]]:
    hunts = session.exec(select(Hunt)).all()
    by_status: dict[HuntStatus, list[Hunt]] = {status: [] for status in HuntStatus}
    for hunt in hunts:
        by_status[hunt.status].append(hunt)
    return [(status, by_status[status]) for status in HuntStatus]


@router.get("/")
def board(
    request: Request,
    deleted: str | None = Query(None),
    session: Session = Depends(get_session),
):
    return templates.TemplateResponse(
        request, "board.html", {"columns": _board_columns(session), "deleted_title": deleted}
    )


@router.get("/hunts/new")
def new_hunt_form(request: Request):
    return _new_hunt_form_response(request, title="", hypothesis="", priority="", error=None)


@router.post("/hunts")
def create_hunt_route(
    request: Request,
    title: str = Form(""),
    hypothesis: str = Form(""),
    priority: str = Form(""),
    session: Session = Depends(get_session),
):
    try:
        priority_value = int(priority)
    except ValueError:
        return _new_hunt_form_response(
            request,
            title=title,
            hypothesis=hypothesis,
            priority=priority,
            error="Priority is required and must be a whole number between 1 and 5.",
            status_code=422,
        )

    try:
        hunt = create_hunt(title, hypothesis, priority_value)
    except ValueError as exc:
        return _new_hunt_form_response(
            request,
            title=title,
            hypothesis=hypothesis,
            priority=priority,
            error=str(exc),
            status_code=422,
        )

    session.add(hunt)
    session.commit()
    session.refresh(hunt)
    return RedirectResponse(url=f"/hunts/{hunt.id}", status_code=303)


@router.post("/hunts/{hunt_id}/status")
def move_hunt_status(
    hunt_id: int,
    request: Request,
    status: HuntStatus = Form(...),
    session: Session = Depends(get_session),
):
    hunt = _get_hunt_or_404(session, hunt_id)
    try:
        move_hunt(hunt, status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.add(hunt)
    session.commit()
    return templates.TemplateResponse(
        request, "partials/board_update.html", {"columns": _board_columns(session)}
    )


@router.post("/hunts/{hunt_id}/move")
def move_hunt_from_detail(
    hunt_id: int,
    request: Request,
    status: HuntStatus = Form(...),
    session: Session = Depends(get_session),
):
    hunt = _get_hunt_or_404(session, hunt_id)
    try:
        move_hunt(hunt, status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.add(hunt)
    session.commit()
    return _hunt_main_response(request, hunt)


@router.get("/hunts/{hunt_id}/retire-dialog")
def retire_dialog(
    hunt_id: int,
    request: Request,
    context: RetireContext = Query("board"),
    session: Session = Depends(get_session),
):
    hunt = _get_hunt_or_404(session, hunt_id)
    return templates.TemplateResponse(
        request,
        "partials/retire_dialog.html",
        {"hunt": hunt, "retirement_reasons": list(RetirementReason), "context": context},
    )


@router.post("/hunts/{hunt_id}/retire")
def retire_hunt_route(
    hunt_id: int,
    request: Request,
    reason: RetirementReason = Form(...),
    note: str = Form(""),
    context: RetireContext = Form("board"),
    session: Session = Depends(get_session),
):
    hunt = _get_hunt_or_404(session, hunt_id)
    retire_hunt(hunt, reason, note)
    session.add(hunt)
    session.commit()
    if context == "detail":
        return templates.TemplateResponse(
            request,
            "partials/retire_detail_response.html",
            {"hunt": hunt, "run_outcomes": list(RunOutcome)},
        )
    return templates.TemplateResponse(
        request, "partials/retire_response.html", {"columns": _board_columns(session)}
    )


@router.post("/hunts/{hunt_id}/reactivate")
def reactivate_hunt_route(hunt_id: int, request: Request, session: Session = Depends(get_session)):
    hunt = _get_hunt_or_404(session, hunt_id)
    try:
        reactivate_hunt(hunt)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.add(hunt)
    session.commit()
    return _hunt_main_response(request, hunt)


def _delete_hunt_page(request: Request, hunt: Hunt, *, error: str | None = None, status_code: int = 200):
    return templates.TemplateResponse(
        request,
        "hunt_delete.html",
        {"hunt": hunt, "confirmation_word": DELETE_CONFIRMATION_WORD, "error": error},
        status_code=status_code,
    )


@router.get("/hunts/{hunt_id}/delete")
def confirm_delete_hunt(hunt_id: int, request: Request, session: Session = Depends(get_session)):
    return _delete_hunt_page(request, _get_hunt_or_404(session, hunt_id))


@router.post("/hunts/{hunt_id}/delete")
def delete_hunt_route(
    hunt_id: int,
    request: Request,
    confirmation: str = Form(""),
    session: Session = Depends(get_session),
):
    hunt = _get_hunt_or_404(session, hunt_id)
    title = hunt.title
    try:
        delete_hunt(session, hunt, confirmation)
    except ValueError as exc:
        return _delete_hunt_page(request, hunt, error=str(exc), status_code=422)
    session.commit()
    return RedirectResponse(url=f"/?{urlencode({'deleted': title})}", status_code=303)


@router.get("/hunts/{hunt_id}")
def hunt_detail(hunt_id: int, request: Request, session: Session = Depends(get_session)):
    hunt = _get_hunt_or_404(session, hunt_id)
    return templates.TemplateResponse(
        request, "hunt_detail.html", {"hunt": hunt, "run_outcomes": list(RunOutcome)}
    )


@router.get("/hunts/{hunt_id}/edit")
def edit_hunt_form(hunt_id: int, request: Request, session: Session = Depends(get_session)):
    hunt = _get_hunt_or_404(session, hunt_id)
    return _edit_hunt_form_response(
        request, hunt_id=hunt.id, hunt_title=hunt.title, values=_hunt_form_values(hunt), error=None
    )


@router.post("/hunts/{hunt_id}/edit")
def update_hunt_route(
    hunt_id: int,
    request: Request,
    title: str = Form(""),
    hypothesis: str = Form(""),
    priority: str = Form(""),
    data_sources: str = Form(""),
    attack_techniques: str = Form(""),
    query: str = Form(""),
    notes: str = Form(""),
    cadence_days: str = Form(""),
    next_run: str = Form(""),
    session: Session = Depends(get_session),
):
    hunt = _get_hunt_or_404(session, hunt_id)

    submitted = {
        "title": title,
        "hypothesis": hypothesis,
        "priority": priority,
        "data_sources": data_sources,
        "attack_techniques": attack_techniques,
        "query": query,
        "notes": notes,
        "cadence_days": cadence_days,
        "next_run": next_run,
    }

    def fail(message: str):
        return _edit_hunt_form_response(
            request, hunt_id=hunt.id, hunt_title=hunt.title, values=submitted,
            error=message, status_code=422,
        )

    try:
        priority_value = int(priority)
    except ValueError:
        return fail("Priority is required and must be a whole number between 1 and 5.")

    if cadence_days.strip() == "":
        cadence_value = None
    else:
        try:
            cadence_value = int(cadence_days)
        except ValueError:
            return fail("Cadence must be a whole number of days, or left blank.")

    if next_run.strip() == "":
        next_run_value = None
    else:
        try:
            next_run_value = date.fromisoformat(next_run.strip())
        except ValueError:
            return fail("Next run must be a valid date, or left blank.")

    try:
        update_hunt(
            hunt,
            title=title,
            hypothesis=hypothesis,
            priority=priority_value,
            data_sources=data_sources,
            attack_techniques=attack_techniques,
            query=query,
            notes=notes,
            cadence_days=cadence_value,
            next_run=next_run_value,
        )
    except ValueError as exc:
        return fail(str(exc))

    session.add(hunt)
    session.commit()
    return RedirectResponse(url=f"/hunts/{hunt.id}", status_code=303)


@router.post("/hunts/{hunt_id}/runs")
def create_run(
    hunt_id: int,
    request: Request,
    outcome: RunOutcome = Form(...),
    notes: str = Form(""),
    duration_minutes: int | None = Form(None),
    search_from: str = Form(""),
    search_to: str = Form(""),
    result_count: str = Form(""),
    session: Session = Depends(get_session),
):
    hunt = _get_hunt_or_404(session, hunt_id)

    run_form_values = {
        "outcome": outcome.value,
        "duration_minutes": "" if duration_minutes is None else str(duration_minutes),
        "search_from": search_from,
        "search_to": search_to,
        "result_count": result_count,
        "notes": notes,
    }

    def fail(message: str):
        return templates.TemplateResponse(
            request,
            "partials/hunt_main.html",
            {
                "hunt": hunt,
                "run_outcomes": list(RunOutcome),
                "run_form_error": message,
                "run_form_values": run_form_values,
            },
            status_code=422,
        )

    search_from_value: datetime | None = None
    search_to_value: datetime | None = None
    if search_from.strip():
        try:
            search_from_value = datetime.fromisoformat(search_from.strip())
        except ValueError:
            return fail("Search from must be a valid date and time.")
    if search_to.strip():
        try:
            search_to_value = datetime.fromisoformat(search_to.strip())
        except ValueError:
            return fail("Search to must be a valid date and time.")

    result_count_value: int | None = None
    if result_count.strip():
        try:
            result_count_value = int(result_count.strip())
        except ValueError:
            return fail("Result count must be a whole number.")

    try:
        run = log_run(
            hunt,
            outcome,
            notes=notes,
            duration_minutes=duration_minutes,
            search_from=search_from_value,
            search_to=search_to_value,
            result_count=result_count_value,
        )
    except ValueError as exc:
        return fail(str(exc))

    session.add(run)
    session.add(hunt)
    session.commit()
    return _hunt_main_response(request, hunt)


@router.get("/hunts/{hunt_id}/runs/{run_id}/row")
def run_row(
    hunt_id: int,
    run_id: int,
    request: Request,
    expanded: bool = False,
    session: Session = Depends(get_session),
):
    run = session.get(Run, run_id)
    if run is None or run.hunt_id != hunt_id:
        raise HTTPException(status_code=404, detail="Run not found")
    return templates.TemplateResponse(
        request, "partials/run_row.html", {"hunt": run.hunt, "run": run, "expanded": expanded}
    )


@router.post("/hunts/{hunt_id}/exclusions")
def create_exclusion(
    hunt_id: int,
    request: Request,
    value: str = Form(...),
    reason: str = Form(...),
    session: Session = Depends(get_session),
):
    hunt = _get_hunt_or_404(session, hunt_id)
    exclusion = Exclusion(hunt_id=hunt.id, value=value, reason=reason)
    session.add(exclusion)
    session.commit()
    return _hunt_main_response(request, hunt)


@router.post("/hunts/{hunt_id}/exclusions/{exclusion_id}/deactivate")
def deactivate_exclusion(
    hunt_id: int,
    exclusion_id: int,
    request: Request,
    session: Session = Depends(get_session),
):
    hunt = _get_hunt_or_404(session, hunt_id)
    exclusion = session.get(Exclusion, exclusion_id)
    if exclusion is None or exclusion.hunt_id != hunt_id:
        raise HTTPException(status_code=404, detail="Exclusion not found")
    exclusion.active = False
    session.add(exclusion)
    session.commit()
    return _hunt_main_response(request, hunt)


def _get_campaign_or_404(session: Session, campaign_id: int) -> Campaign:
    campaign = session.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return campaign


def _new_campaign_form_response(
    request: Request,
    *,
    values: dict[str, str],
    error: str | None,
    status_code: int = 200,
):
    return templates.TemplateResponse(
        request, "campaign_new.html", {"values": values, "error": error}, status_code=status_code
    )


def _edit_campaign_form_response(
    request: Request,
    *,
    campaign: Campaign,
    values: dict[str, str],
    error: str | None,
    status_code: int = 200,
):
    return templates.TemplateResponse(
        request,
        "campaign_edit.html",
        {"campaign": campaign, "values": values, "error": error},
        status_code=status_code,
    )


def _campaign_overview_response(request: Request, campaign: Campaign, *, hunt_picker_query: str = ""):
    return templates.TemplateResponse(
        request,
        "partials/campaign_overview.html",
        {
            "campaign": campaign,
            "overview": build_campaign_overview(campaign),
            "hunt_picker_query": hunt_picker_query,
        },
    )


@router.get("/campaigns")
def campaigns_page(request: Request, session: Session = Depends(get_session)):
    return templates.TemplateResponse(request, "campaigns.html", {"rows": build_campaign_list(session)})


@router.get("/campaigns/new")
def new_campaign_form(request: Request):
    return _new_campaign_form_response(
        request, values={"title": "", "objective": "", "scope": "", "notes": "", "conclusion": ""}, error=None
    )


@router.post("/campaigns")
def create_campaign_route(
    request: Request,
    title: str = Form(""),
    objective: str = Form(""),
    scope: str = Form(""),
    notes: str = Form(""),
    conclusion: str = Form(""),
    session: Session = Depends(get_session),
):
    submitted = {"title": title, "objective": objective, "scope": scope, "notes": notes, "conclusion": conclusion}
    try:
        campaign = create_campaign(title, objective, scope=scope, notes=notes, conclusion=conclusion)
    except ValueError as exc:
        return _new_campaign_form_response(request, values=submitted, error=str(exc), status_code=422)

    session.add(campaign)
    session.commit()
    session.refresh(campaign)
    return RedirectResponse(url=f"/campaigns/{campaign.id}", status_code=303)


@router.get("/campaigns/{campaign_id}")
def campaign_detail(
    campaign_id: int, request: Request, tab: str = "overview", session: Session = Depends(get_session)
):
    campaign = _get_campaign_or_404(session, campaign_id)
    tab = tab if tab in ("overview", "activity") else "overview"
    context = {"campaign": campaign, "tab": tab, "last_activity": last_activity_for(campaign)}
    if tab == "activity":
        context["activity"] = build_campaign_activity(campaign)
    else:
        context["overview"] = build_campaign_overview(campaign)
        context["hunt_picker_query"] = ""
    return templates.TemplateResponse(request, "campaign_detail.html", context)


@router.get("/campaigns/{campaign_id}/edit")
def edit_campaign_form(campaign_id: int, request: Request, session: Session = Depends(get_session)):
    campaign = _get_campaign_or_404(session, campaign_id)
    values = {
        "title": campaign.title,
        "objective": campaign.objective,
        "scope": campaign.scope,
        "notes": campaign.notes,
        "conclusion": campaign.conclusion,
        "status": campaign.status.value,
    }
    return _edit_campaign_form_response(request, campaign=campaign, values=values, error=None)


@router.post("/campaigns/{campaign_id}/edit")
def update_campaign_route(
    campaign_id: int,
    request: Request,
    title: str = Form(""),
    objective: str = Form(""),
    scope: str = Form(""),
    notes: str = Form(""),
    conclusion: str = Form(""),
    status: CampaignStatus = Form(...),
    session: Session = Depends(get_session),
):
    campaign = _get_campaign_or_404(session, campaign_id)
    submitted = {
        "title": title,
        "objective": objective,
        "scope": scope,
        "notes": notes,
        "conclusion": conclusion,
        "status": status.value,
    }
    try:
        update_campaign(campaign, title=title, objective=objective, scope=scope, notes=notes, conclusion=conclusion)
    except ValueError as exc:
        return _edit_campaign_form_response(
            request, campaign=campaign, values=submitted, error=str(exc), status_code=422
        )
    set_campaign_status(campaign, status)

    session.add(campaign)
    session.commit()
    return RedirectResponse(url=f"/campaigns/{campaign.id}", status_code=303)


@router.post("/campaigns/{campaign_id}/status")
def update_campaign_status_route(
    campaign_id: int,
    request: Request,
    status: CampaignStatus = Form(...),
    session: Session = Depends(get_session),
):
    campaign = _get_campaign_or_404(session, campaign_id)
    set_campaign_status(campaign, status)
    session.add(campaign)
    session.commit()
    return templates.TemplateResponse(
        request, "partials/campaign_header.html", {"campaign": campaign, "last_activity": last_activity_for(campaign)}
    )


@router.post("/campaigns/{campaign_id}/conclusion")
def update_campaign_conclusion_route(
    campaign_id: int,
    request: Request,
    conclusion: str = Form(""),
    session: Session = Depends(get_session),
):
    campaign = _get_campaign_or_404(session, campaign_id)
    update_campaign_conclusion(campaign, conclusion)
    session.add(campaign)
    session.commit()
    return _campaign_overview_response(request, campaign)


@router.get("/campaigns/{campaign_id}/hunt-picker")
def hunt_picker(campaign_id: int, request: Request, q: str = "", session: Session = Depends(get_session)):
    campaign = _get_campaign_or_404(session, campaign_id)
    linked_ids = {link.hunt_id for link in campaign.memberships}
    query = q.strip().lower()
    candidates = session.exec(select(Hunt)).all()
    results = [
        hunt
        for hunt in candidates
        if hunt.id not in linked_ids and (not query or query in hunt.title.lower())
    ]
    results.sort(key=lambda hunt: hunt.title.lower())
    return templates.TemplateResponse(
        request,
        "partials/hunt_picker_results.html",
        {"campaign": campaign, "results": results[:10], "hunt_picker_query": q},
    )


@router.post("/campaigns/{campaign_id}/hunts")
def link_hunt_route(
    campaign_id: int,
    request: Request,
    hunt_id: int = Form(...),
    session: Session = Depends(get_session),
):
    campaign = _get_campaign_or_404(session, campaign_id)
    hunt = _get_hunt_or_404(session, hunt_id)
    try:
        link = link_hunt_to_campaign(campaign, hunt)
    except ValueError:
        return _campaign_overview_response(request, campaign)

    session.add(link)
    session.add(campaign)
    session.commit()
    return _campaign_overview_response(request, campaign)


@router.post("/campaigns/{campaign_id}/hunts/{hunt_id}/unlink")
def unlink_hunt_route(
    campaign_id: int,
    hunt_id: int,
    request: Request,
    session: Session = Depends(get_session),
):
    campaign = _get_campaign_or_404(session, campaign_id)
    link = session.exec(
        select(CampaignHunt).where(CampaignHunt.campaign_id == campaign_id, CampaignHunt.hunt_id == hunt_id)
    ).first()
    if link is None:
        raise HTTPException(status_code=404, detail="Membership not found")

    unlink_hunt_from_campaign(campaign, link)
    session.delete(link)
    session.add(campaign)
    session.commit()
    session.refresh(campaign)
    return _campaign_overview_response(request, campaign)


@router.get("/insights")
def insights_page(request: Request, range: str = "30", session: Session = Depends(get_session)):
    data = build_insights(session, range)
    data["heatmap"] = build_activity_heatmap(session)
    return templates.TemplateResponse(request, "insights.html", data)


@router.get("/insights/activity/day")
def insights_activity_day(
    request: Request,
    day: str = Query("", alias="date"),
    session: Session = Depends(get_session),
):
    try:
        selected = parse_day(day)
    except InvalidDayError as exc:
        return templates.TemplateResponse(
            request, "partials/activity_day_error.html", {"message": str(exc)}, status_code=400
        )
    return templates.TemplateResponse(
        request, "partials/activity_day.html", {"detail": build_day_detail(session, selected)}
    )


@router.get("/export")
def export_page(request: Request):
    return templates.TemplateResponse(request, "export.html", {})


@router.get("/export/json")
def export_json(session: Session = Depends(get_session)):
    payload = build_json_export(session)
    body = json.dumps(payload, indent=2)
    filename = f"hunt-tracker-export-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/export/backup")
def export_backup():
    try:
        backup_path = create_sqlite_backup()
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        data = backup_path.read_bytes()
    finally:
        backup_path.unlink(missing_ok=True)

    filename = f"hunts-backup-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.db"
    return Response(
        content=data,
        media_type="application/vnd.sqlite3",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/hunts/{hunt_id}/export/markdown")
def export_hunt_markdown(hunt_id: int, session: Session = Depends(get_session)):
    hunt = _get_hunt_or_404(session, hunt_id)
    body = build_hunt_markdown(hunt)
    filename = markdown_export_filename(hunt)
    return Response(
        content=body,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
