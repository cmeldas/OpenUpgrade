# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from html import escape

from openupgradelib import openupgrade

_added_columns = [
    ("maintenance.request", "state", "selection", None, "maintenance_request"),
]


def fill_maintenance_request_state(env):
    """New required maintenance.request#state replaces three v19 concepts:
    archive (= cancelled, button "Cancel"), maintenance.stage#done (the request
    is finished) and kanban_state (blocked / ready for next stage)."""
    cr = env.cr
    has_stage_done = openupgrade.column_exists(cr, "maintenance_stage", "done")
    has_archive = openupgrade.column_exists(cr, "maintenance_request", "archive")
    has_kanban = openupgrade.column_exists(cr, "maintenance_request", "kanban_state")
    openupgrade.logged_query(
        cr,
        f"""
        UPDATE maintenance_request mr
        SET state = CASE
            WHEN {"mr.archive" if has_archive else "FALSE"} THEN 'cancelled'
            WHEN {"ms.done" if has_stage_done else "FALSE"} THEN 'done'
            WHEN {"mr.kanban_state = 'blocked'" if has_kanban else "FALSE"}
                THEN 'changes_requested'
            WHEN {"mr.kanban_state = 'done'" if has_kanban else "FALSE"}
                THEN 'approved'
            ELSE 'normal'
        END
        FROM maintenance_request mr2
        LEFT JOIN maintenance_stage ms ON ms.id = mr2.stage_id
        WHERE mr2.id = mr.id AND mr.state IS NULL
        """,
    )


def fill_schedule_from_request_date(env):
    """request_date is gone; v20 uses schedule_date (or create_date) instead,
    e.g. for the MTBF of the equipment (done corrective requests). Keep the
    date for the finished requests that had no schedule. Open requests stay
    unscheduled: a schedule would put them in the calendar and in the
    "scheduled" counters of the team. schedule_date and schedule_end must be
    both set or both empty (_check_schedule_interval)."""
    if not openupgrade.column_exists(env.cr, "maintenance_request", "request_date"):
        return
    openupgrade.logged_query(
        env.cr,
        """
        UPDATE maintenance_request
        SET schedule_date = request_date::timestamp,
            schedule_end = request_date::timestamp + INTERVAL '1 hour' * (
                CASE WHEN COALESCE(duration, 0) = 0 THEN 1 ELSE duration END)
        WHERE schedule_date IS NULL AND request_date IS NOT NULL
            AND state = 'done'
        """,
    )
    openupgrade.logged_query(
        env.cr,
        """
        UPDATE maintenance_request
        SET schedule_end = schedule_date + INTERVAL '1 hour' * (
                CASE WHEN COALESCE(duration, 0) = 0 THEN 1 ELSE duration END)
        WHERE schedule_date IS NOT NULL AND schedule_end IS NULL
        """,
    )


def move_instructions_to_text(env):
    """instruction_type, instruction_pdf and instruction_google_slide are gone,
    only instruction_text (html) remains. The Google Slide URL is appended as a
    link to the text, the PDF stays as a regular attachment of the request."""
    cr = env.cr
    if openupgrade.column_exists(cr, "maintenance_request", "instruction_google_slide"):
        cr.execute(
            """
            SELECT id, instruction_google_slide, instruction_text
            FROM maintenance_request
            WHERE COALESCE(instruction_google_slide, '') != ''
            """
        )
        for request_id, url, text in cr.fetchall():
            if url in (text or "") or escape(url) in (text or ""):
                continue
            link = '<p><a href="{0}" target="_blank">{0}</a></p>'.format(escape(url))
            openupgrade.logged_query(
                cr,
                """
                UPDATE maintenance_request
                SET instruction_text = COALESCE(instruction_text, '') || %s
                WHERE id = %s
                """,
                (link, request_id),
            )
    openupgrade.logged_query(
        cr,
        """
        UPDATE ir_attachment
        SET res_field = NULL
        WHERE res_model = 'maintenance.request'
            AND res_field = 'instruction_pdf'
        """,
    )


@openupgrade.migrate()
def migrate(env, version):
    openupgrade.add_columns(env, _added_columns)
    fill_maintenance_request_state(env)
    fill_schedule_from_request_date(env)
    move_instructions_to_text(env)
