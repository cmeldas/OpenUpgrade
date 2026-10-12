# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from collections import defaultdict
from datetime import timedelta

from openupgradelib import openupgrade
from psycopg2.extras import Json

_added_columns = [
    ("project.collaborator", "access_mode", "selection", None, "project_collaborator"),
    (
        "project.project",
        "date_last_stage_update",
        "datetime",
        None,
        "project_project",
    ),
    ("project.project", "duration_tracking", "json", None, "project_project"),
    ("project.task", "duration_tracking", "json", None, "project_task"),
]


def fill_collaborator_access_mode(env):
    """limited_access (v19) -> access_mode (v20, required, default 'view').
    A v19 collaborator could always edit: limited access = 'edit' (only the
    tasks he follows), full access = 'advanced_edit' (v20 default of
    project._add_collaborators)."""
    has_limited = openupgrade.column_exists(
        env.cr, "project_collaborator", "limited_access"
    )
    openupgrade.logged_query(
        env.cr,
        f"""
        UPDATE project_collaborator
        SET access_mode = CASE
            WHEN {"limited_access" if has_limited else "FALSE"} THEN 'edit'
            ELSE 'advanced_edit' END
        WHERE access_mode IS NULL
        """,
    )


def _get_stage_trackings(cr, model):
    """Stage changes of the records of model, in chronological order."""
    if not openupgrade.table_exists(cr, "mail_tracking_value"):
        return {}
    cr.execute(
        """
        SELECT mm.res_id, mtv.create_date, mtv.old_value_integer
        FROM mail_tracking_value mtv
        JOIN mail_message mm ON mm.id = mtv.mail_message_id
        JOIN ir_model_fields imf ON imf.id = mtv.field_id
        WHERE mm.model = %(model)s
            AND imf.model = %(model)s AND imf.name = 'stage_id'
        ORDER BY mm.res_id, mtv.create_date, mtv.id
        """,
        {"model": model},
    )
    trackings = defaultdict(list)
    for res_id, date, old_stage_id in cr.fetchall():
        trackings[res_id].append((date, old_stage_id or 0))
    return trackings


def fill_date_last_stage_update(env):
    """project.project#date_last_stage_update is new (default now): use the
    date of the last stage change, or the creation date."""
    cr = env.cr
    trackings = _get_stage_trackings(cr, "project.project")
    for project_id, project_trackings in trackings.items():
        cr.execute(
            """
            UPDATE project_project SET date_last_stage_update = %s
            WHERE id = %s AND date_last_stage_update IS NULL
            """,
            (project_trackings[-1][0], project_id),
        )
    openupgrade.logged_query(
        cr,
        """
        UPDATE project_project
        SET date_last_stage_update = COALESCE(create_date, write_date)
        WHERE date_last_stage_update IS NULL
        """,
    )


def fill_duration_tracking(env, model, table):
    """mail.tracking.duration.mixin#duration_tracking is stored in v20 and
    incremental: {"<stage id>": minutes spent in the previous visits, "s":
    current stage, "d": since when}. v19 computed it on the fly from the stage
    tracking values; without this, the ORM would start all records at the
    migration date and lose the history of time spent in stages."""
    cr = env.cr
    trackings = _get_stage_trackings(cr, model)
    date_column = "date_last_stage_update"
    if not openupgrade.column_exists(cr, table, date_column):
        date_column = "NULL::timestamp"
    cr.execute(
        f"""
        SELECT id, create_date, stage_id, {date_column}
        FROM {table}
        WHERE duration_tracking IS NULL
        """  # pylint: disable=sql-injection
    )
    for record_id, create_date, stage_id, date_last_stage_update in cr.fetchall():
        durations = defaultdict(int)
        previous_date = create_date
        for date, old_stage_id in trackings.get(record_id, []):
            if previous_date and date:
                durations[str(old_stage_id)] += int(
                    (date - previous_date) / timedelta(minutes=1)
                )
            previous_date = date
        if not trackings.get(record_id):
            previous_date = date_last_stage_update or create_date
        value = dict(durations)
        value["s"] = stage_id or 0
        if previous_date:
            value["d"] = previous_date.strftime("%Y-%m-%d %H:%M:%S")
        cr.execute(
            f"UPDATE {table} SET duration_tracking = %s WHERE id = %s",  # pylint: disable=sql-injection
            (Json(value), record_id),
        )


@openupgrade.migrate()
def migrate(env, version):
    openupgrade.add_columns(env, _added_columns)
    fill_collaborator_access_mode(env)
    fill_date_last_stage_update(env)
    fill_duration_tracking(env, "project.project", "project_project")
    fill_duration_tracking(env, "project.task", "project_task")
