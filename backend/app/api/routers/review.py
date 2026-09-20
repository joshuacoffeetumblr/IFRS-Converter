"""Review questions and declared business activities (API spec §2).

These two endpoints are what make the ``NEEDS_FACT`` path of spec §5 real: a
rule says which fact it is missing, the user supplies it, and classification is
re-run so the answer takes effect immediately rather than at some later step
the user has to remember to trigger.
"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, SessionDep, ensure_not_finalized, parse_uuid
from app.api.errors import NotFoundError, UnprocessableStateError
from app.api.routers.classifications import (
    question_response,
    questions_with_stakes,
    summarize,
)
from app.api.schemas.classification import (
    ActivitiesResponse,
    ActivityResponse,
    AnswerRequest,
    AnswerResponse,
    QuestionsResponse,
    SetActivityRequest,
)
from app.domain.enums import ActivityType
from app.models import BusinessActivity, Ifrs18Classification, Project, ReviewQuestion, User
from app.repositories.projects import ProjectRepository
from app.repositories.review import ActivityRepository, QuestionRepository
from app.services.classification import ClassificationError, classify_project
from app.services.review import Answer, ReviewError, answer_question, set_activity

router = APIRouter(tags=["review"])


async def _project(session: SessionDep, project_id: str, user: CurrentUser) -> Project:
    project = await ProjectRepository(session).by_id(parse_uuid(project_id, "Project"), user.id)
    if project is None:
        raise NotFoundError("Project")
    return project


def _activity_response(activity: BusinessActivity) -> ActivityResponse:
    activity_type = ActivityType(activity.activity_type)
    return ActivityResponse(
        id=activity.id,
        activity_type=activity_type,
        is_main_business_activity=activity.is_main_business_activity,
        description=activity.description,
        source=activity.source,
        confirmed_by_user=activity.confirmed_by_user,
        confirmed_at=activity.confirmed_at,
        is_specified=activity_type.is_specified_main_business_activity,
    )


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/questions", response_model=QuestionsResponse)
async def list_questions(
    project_id: str, session: SessionDep, user: CurrentUser
) -> QuestionsResponse:
    """The facts the rules are waiting on, and what turns on each."""
    project = await _project(session, project_id, user)
    items = await questions_with_stakes(session, project)
    return QuestionsResponse(
        items=items,
        open_count=sum(1 for item in items if item.blocks_finalization and not item.is_resolved),
    )


@router.post("/projects/{project_id}/questions/{question_id}/answer", response_model=AnswerResponse)
async def answer(
    project_id: str,
    question_id: str,
    payload: AnswerRequest,
    session: SessionDep,
    user: CurrentUser,
) -> AnswerResponse:
    """Answer a question, then re-run classification so the answer takes effect.

    Re-running the whole project rather than "only the affected rules" is
    deliberate: one engine run is cheap, and a partial re-run is a second
    implementation of the classification order that could drift from the first.
    Human decisions are preserved, so nothing a reviewer settled is lost.
    """
    project = await _project(session, project_id, user)
    ensure_not_finalized(project)
    question = await QuestionRepository(session).by_id(
        parse_uuid(question_id, "Question"), project.id
    )
    if question is None:
        raise NotFoundError("Question")

    try:
        await answer_question(
            session,
            project=project,
            question=question,
            answer=Answer(
                answer=payload.answer,
                resolved_category=payload.resolved_category,
                resolved_subcategory=payload.resolved_subcategory,
                undue_cost_or_effort=payload.undue_cost_or_effort,
                note=payload.note,
            ),
            actor=user,
        )
    except ReviewError as exc:
        raise UnprocessableStateError(
            title="Answer rejected", code=exc.code, detail=exc.reason
        ) from exc

    run = await _reclassify(session, project=project, user=user)
    return AnswerResponse(
        question=question_response(await _reload(session, question, project)),
        summary=summarize(
            list(run) if run is not None else [],
            await QuestionRepository(session).for_project(project.id),
        ),
    )


async def _reload(
    session: AsyncSession, question: ReviewQuestion, project: Project
) -> ReviewQuestion:
    """Re-read the question after the re-run, which may have changed it."""
    refreshed = await QuestionRepository(session).by_id(question.id, project.id)
    return refreshed if refreshed is not None else question


async def _reclassify(
    session: AsyncSession, *, project: Project, user: User
) -> list[Ifrs18Classification] | None:
    """Re-run the engine, preserving human decisions.

    A project that has not been classified yet has nothing to re-run, and
    saying so is not an error: the answer is still recorded and will be used
    the first time classification runs.
    """
    try:
        run = await classify_project(
            session, project=project, preserve_user_overrides=True, actor_id=user.id
        )
    except ClassificationError:
        return None
    return list(run.classifications)


# ---------------------------------------------------------------------------
# Business activities
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/business-activities", response_model=ActivitiesResponse)
async def list_activities(
    project_id: str, session: SessionDep, user: CurrentUser
) -> ActivitiesResponse:
    """What has been declared about this entity, and by whom.

    Nothing is inferred from an industry code (spec §10): an activity that
    nobody has confirmed simply is not here.
    """
    project = await _project(session, project_id, user)
    activities = await ActivityRepository(session).for_project(project.company_id, project.id)
    return ActivitiesResponse(items=[_activity_response(item) for item in activities])


@router.put(
    "/projects/{project_id}/business-activities/{activity_type}",
    response_model=ActivityResponse,
)
async def put_activity(
    project_id: str,
    activity_type: ActivityType,
    payload: SetActivityRequest,
    session: SessionDep,
    user: CurrentUser,
) -> ActivityResponse:
    """Confirm, deny or withdraw a main business activity (spec §10).

    Only a person reaches this endpoint, so anything set here is
    ``confirmed_by_user``. Setting it to null withdraws the confirmation and
    returns the fact to unknown, which blocks the rules that depend on it
    rather than reading as "no".
    """
    project = await _project(session, project_id, user)
    ensure_not_finalized(project)
    activity = await set_activity(
        session,
        project=project,
        activity_type=activity_type,
        is_main_business_activity=payload.is_main_business_activity,
        description=payload.description,
        actor=user,
    )
    # A confirmed activity changes what the rules decide, so the project's
    # classifications are brought back into line with it immediately.
    await _reclassify(session, project=project, user=user)
    return _activity_response(activity)
