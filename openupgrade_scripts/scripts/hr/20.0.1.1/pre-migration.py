# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from psycopg2 import sql
from psycopg2.extras import Json

from openupgradelib import openupgrade

_renamed_models = [("hr.contract.type", "hr.employee.type")]

_renamed_tables = [("hr_contract_type", "hr_employee_type")]

# hr.contract.type became hr.employee.type (same XML-IDs hr.contract_type_*),
# the contract type of a version / a job is now its employee type. The field
# of hr.employee is the non stored inherited one (no column), renamed for its
# references (filters, exports, tracking values).
_renamed_fields = [
    ("hr.version", "hr_version", "contract_type_id", "employee_type_id"),
    ("hr.job", "hr_job", "contract_type_id", "employee_type_id"),
    ("hr.employee", "hr_employee", "contract_type_id", "employee_type_id"),
]

# Columns of other tables still holding the technical name of the old model
# (not covered by openupgrade.rename_models)
_model_name_columns = [
    ("ir_act_window", "res_model"),
    ("ir_act_client", "res_model"),
    ("ir_act_report_xml", "model"),
    ("ir_ui_view", "model"),
    ("ir_embedded_actions", "parent_res_model"),
    ("mail_activity_type", "res_model"),
    ("mail_activity_plan", "res_model"),
    ("res_users_settings_embedded_action", "res_model"),
    ("base_import_mapping", "res_model"),
    ("sms_template", "model"),
    ("snailmail_letter", "model"),
]

# hr.contract.type records of 19.0 that are no employee types in 20.0
OBSOLETE_CONTRACT_TYPES = (
    "contract_type_permanent",
    "contract_type_temporary",
    "contract_type_full_time",
    "contract_type_part_time",
    "contract_type_statutory",
)

# hr.version.employee_type (selection, removed) -> hr.employee.type XML-ID.
# Values without a 20.0 counterpart get their own record (see
# _get_or_create_employee_type).
EMPLOYEE_TYPE_XMLIDS = {
    "employee": "contract_type_employee",
    "student": "contract_type_student",
    "trainee": "contract_type_intern",
}


def _rename_contract_type_model(cr):
    if not openupgrade.table_exists(cr, "hr_contract_type"):
        return
    openupgrade.rename_models(cr, _renamed_models)
    openupgrade.rename_tables(cr, _renamed_tables)
    for table, column in _model_name_columns:
        if openupgrade.column_exists(cr, table, column):
            openupgrade.logged_query(
                cr,
                sql.SQL("UPDATE {table} SET {column} = %s WHERE {column} = %s").format(
                    table=sql.Identifier(table), column=sql.Identifier(column)
                ),
                ("hr.employee.type", "hr.contract.type"),
            )


def _xmlid_res_ids(cr, names):
    cr.execute(
        """
        SELECT name, res_id FROM ir_model_data
        WHERE module = 'hr' AND model = 'hr.employee.type' AND name IN %s
        """,
        (tuple(names),),
    )
    return dict(cr.fetchall())


def _get_or_create_employee_type(cr, value):
    """Return the hr.employee.type for a value of the removed selection
    hr.version.employee_type, creating it (with the translated label of the
    selection value) when 20.0 has no matching record."""
    xmlid_name = EMPLOYEE_TYPE_XMLIDS.get(value, "openupgrade_employee_type_%s" % value)
    res_id = _xmlid_res_ids(cr, [xmlid_name]).get(xmlid_name)
    if res_id:
        cr.execute("SELECT id FROM hr_employee_type WHERE id = %s", (res_id,))
        if cr.fetchone():
            return res_id
        cr.execute(
            "DELETE FROM ir_model_data WHERE module = 'hr' AND name = %s",
            (xmlid_name,),
        )
    cr.execute(
        """
        SELECT ifs.name FROM ir_model_fields_selection ifs
        JOIN ir_model_fields imf ON imf.id = ifs.field_id
        WHERE imf.model = 'hr.version' AND imf.name = 'employee_type'
            AND ifs.value = %s
        """,
        (value,),
    )
    row = cr.fetchone()
    name = row[0] if row and row[0] else {"en_US": value.capitalize()}
    openupgrade.logged_query(
        cr,
        """
        INSERT INTO hr_employee_type (
            name, code, sequence, create_uid, create_date, write_uid, write_date)
        VALUES (%s, %s, 10, 1, now() AT TIME ZONE 'UTC', 1, now() AT TIME ZONE 'UTC')
        RETURNING id
        """,
        (Json(name), name.get("en_US") or value),
    )
    res_id = cr.fetchone()[0]
    # noupdate, otherwise ir.model.data._process_end() deletes it at the end
    # of the update of hr (records of hr_employee_type_data.xml are noupdate
    # too and forcecreate keeps the existing record)
    openupgrade.logged_query(
        cr,
        """
        INSERT INTO ir_model_data (
            module, name, model, res_id, noupdate,
            create_uid, create_date, write_uid, write_date)
        VALUES ('hr', %s, 'hr.employee.type', %s, TRUE,
            1, now() AT TIME ZONE 'UTC', 1, now() AT TIME ZONE 'UTC')
        """,
        (xmlid_name, res_id),
    )
    return res_id


def _map_employee_type(env):
    """hr.version.employee_type (required selection) and contract_type_id
    (many2one to hr.contract.type) are merged into employee_type_id (many2one
    to hr.employee.type, the renamed hr.contract.type).

    * fixed_term (new) is set for versions with a contract end date (20.0 sets
      it the same way, see hr.employee._create_version) or with the 19.0
      contract type "Temporary";
    * a contract type that still is an employee type in 20.0 (or a custom one)
      is kept, the obsolete ones (permanent, temporary, full / part time) are
      replaced by the mapping of the selection; "Statutory" is mapped to the
      new "Company Executive" type in post-migration.
    """
    cr = env.cr
    if not openupgrade.column_exists(cr, "hr_version", "employee_type"):
        return
    legacy = openupgrade.get_legacy_name("employee_type")
    openupgrade.rename_columns(cr, {"hr_version": [("employee_type", legacy)]})
    # the field is removed but its column was required
    openupgrade.logged_query(
        cr,
        sql.SQL("ALTER TABLE hr_version ALTER COLUMN {} DROP NOT NULL").format(
            sql.Identifier(legacy)
        ),
    )
    obsolete = _xmlid_res_ids(cr, OBSOLETE_CONTRACT_TYPES)
    openupgrade.add_columns(
        env, [("hr.version", "fixed_term", "boolean", None, "hr_version")]
    )
    openupgrade.logged_query(
        cr,
        """
        UPDATE hr_version
        SET fixed_term = (contract_date_end IS NOT NULL
            OR employee_type_id IS NOT DISTINCT FROM %s)
        WHERE fixed_term IS NULL
        """,
        (obsolete.get("contract_type_temporary") or 0,),
    )
    replaced_ids = tuple(
        res_id
        for name, res_id in obsolete.items()
        if name != "contract_type_statutory"
    ) or (0,)
    cr.execute(
        sql.SQL(
            """
            SELECT DISTINCT {legacy} FROM hr_version
            WHERE {legacy} IS NOT NULL
                AND (employee_type_id IS NULL OR employee_type_id IN %s)
            """
        ).format(legacy=sql.Identifier(legacy)),
        (replaced_ids,),
    )
    for (value,) in cr.fetchall():
        type_id = _get_or_create_employee_type(cr, value)
        openupgrade.logged_query(
            cr,
            sql.SQL(
                """
                UPDATE hr_version SET employee_type_id = %s
                WHERE {legacy} = %s
                    AND (employee_type_id IS NULL OR employee_type_id IN %s)
                """
            ).format(legacy=sql.Identifier(legacy)),
            (type_id, value, replaced_ids),
        )


def _get_fully_flexible_calendar(cr, company_id):
    """Same as resource pre-migration get_fully_flexible_calendar() (the
    resource tables are already updated here)."""
    cr.execute(
        """
        SELECT id FROM resource_calendar
        WHERE calendar_type = 'undefined'
            AND COALESCE(hours_per_week, 0) = 0
            AND COALESCE(hours_per_day, 0) = 0
            AND company_id IS NOT DISTINCT FROM %s
        ORDER BY active DESC, id
        LIMIT 1
        """,
        (company_id,),
    )
    row = cr.fetchone()
    if row:
        return row[0]
    openupgrade.logged_query(
        cr,
        """
        INSERT INTO resource_calendar (
            name, company_id, active, calendar_type, hours_per_day,
            hours_per_week, full_time_required_hours,
            create_uid, create_date, write_uid, write_date)
        VALUES (
            'Fully Flexible', %s, TRUE, 'undefined', 0, 0, 0,
            1, now() AT TIME ZONE 'UTC', 1, now() AT TIME ZONE 'UTC')
        RETURNING id
        """,
        (company_id,),
    )
    calendar_id = cr.fetchone()[0]
    # resource is already updated: set the stored compute the ORM computed
    # for the calendars created in resource pre-migration (no full time
    # reference -> rate 1)
    if openupgrade.column_exists(cr, "resource_calendar", "work_time_rate"):
        cr.execute(
            "UPDATE resource_calendar SET work_time_rate = 1.0 WHERE id = %s",
            (calendar_id,),
        )
    return calendar_id


def _fill_version_calendar(cr):
    """hr.version.resource_calendar_id is now required. A version without
    working schedule was fully flexible in 19.0 (is_fully_flexible)."""
    cr.execute(
        """
        SELECT DISTINCT COALESCE(v.company_id, e.company_id)
        FROM hr_version v
        LEFT JOIN hr_employee e ON e.id = v.employee_id
        WHERE v.resource_calendar_id IS NULL
        """
    )
    for (company_id,) in cr.fetchall():
        calendar_id = _get_fully_flexible_calendar(cr, company_id)
        openupgrade.logged_query(
            cr,
            """
            UPDATE hr_version v SET resource_calendar_id = %s
            FROM hr_version v2
            LEFT JOIN hr_employee e ON e.id = v2.employee_id
            WHERE v.id = v2.id
                AND v.resource_calendar_id IS NULL
                AND COALESCE(v2.company_id, e.company_id) IS NOT DISTINCT FROM %s
            """,
            (calendar_id, company_id),
        )


def _fill_version_tz(env):
    """hr.version.tz was related to employee_id.tz (the resource) in 19.0, it
    is now a required stored field whose default would be the timezone of the
    migrating user. Take the timezone of the employee, then the one of the
    working schedule (resource.calendar.tz, removed in 20.0), of the company,
    and 'UTC' as 20.0 does."""
    cr = env.cr
    openupgrade.add_columns(
        env, [("hr.version", "tz", "selection", None, "hr_version")]
    )
    calendar_tz = (
        "cal.tz"
        if openupgrade.column_exists(cr, "resource_calendar", "tz")
        else "NULL"
    )
    company_tz = (
        "rc.tz" if openupgrade.column_exists(cr, "res_company", "tz") else "NULL"
    )
    openupgrade.logged_query(
        cr,
        f"""
        UPDATE hr_version v
        SET tz = COALESCE(rr.tz, {calendar_tz}, {company_tz}, 'UTC')
        FROM hr_version v2
        LEFT JOIN hr_employee e ON e.id = v2.employee_id
        LEFT JOIN resource_resource rr ON rr.id = e.resource_id
        LEFT JOIN resource_calendar cal ON cal.id = v2.resource_calendar_id
        LEFT JOIN res_company rc ON rc.id = COALESCE(v2.company_id, e.company_id)
        WHERE v.id = v2.id AND v.tz IS NULL
        """,
    )


def _move_version_fields_to_employee(env):
    """additional_note and contract_template_id were related to the current
    version in 19.0 and are stored on the employee in 20.0. hr_employee still
    has an additional_note column from <= 18.0 with stale values, which the
    ORM would reuse."""
    cr = env.cr
    if openupgrade.column_exists(cr, "hr_version", "additional_note"):
        openupgrade.logged_query(
            cr,
            """
            UPDATE hr_employee e SET additional_note = v.additional_note
            FROM hr_version v
            WHERE v.id = e.current_version_id
                AND e.additional_note IS DISTINCT FROM v.additional_note
            """,
        )
    openupgrade.add_columns(
        env,
        [("hr.employee", "contract_template_id", "many2one", None, "hr_employee")],
    )
    openupgrade.logged_query(
        cr,
        """
        UPDATE hr_employee e SET contract_template_id = v.contract_template_id
        FROM hr_version v
        WHERE v.id = e.current_version_id
            AND v.contract_template_id IS NOT NULL
            AND e.contract_template_id IS NULL
        """,
    )


def _fix_version_magic_columns(cr):
    """OpenUpgrade 19.0 (hr/19.0.1.1 fill_hr_version) inserted the versions of
    employees without contract without create_uid / create_date."""
    openupgrade.logged_query(
        cr,
        """
        UPDATE hr_version
        SET create_date = COALESCE(write_date, now() AT TIME ZONE 'UTC'),
            create_uid = COALESCE(create_uid, write_uid, 1)
        WHERE create_date IS NULL
        """,
    )
    openupgrade.logged_query(
        cr,
        """
        UPDATE hr_version SET write_uid = COALESCE(create_uid, 1)
        WHERE write_uid IS NULL
        """,
    )


def _map_removed_selection_values(cr):
    # hr.version.sex: 'other' removed (20.0 would empty the value anyway)
    openupgrade.logged_query(
        cr, "UPDATE hr_version SET sex = NULL WHERE sex = 'other'"
    )
    # mail.activity.plan.template.responsible_type: 'coach' removed. The coach
    # is computed from the manager by default (hr.version._compute_coach).
    openupgrade.logged_query(
        cr,
        """
        UPDATE mail_activity_plan_template SET responsible_type = 'manager'
        WHERE responsible_type = 'coach'
        """,
    )


def _fill_job_company(cr):
    """hr.job.company_id is now required: take the company of the department,
    else the first company."""
    openupgrade.logged_query(
        cr,
        """
        UPDATE hr_job j
        SET company_id = COALESCE(
            (SELECT d.company_id FROM hr_department d WHERE d.id = j.department_id),
            (SELECT min(id) FROM res_company))
        WHERE j.company_id IS NULL
        """,
    )


@openupgrade.migrate()
def migrate(env, version):
    cr = env.cr
    _rename_contract_type_model(cr)
    openupgrade.rename_fields(env, _renamed_fields)
    _map_employee_type(env)
    _fill_version_calendar(cr)
    _fill_version_tz(env)
    _move_version_fields_to_employee(env)
    _fix_version_magic_columns(cr)
    _map_removed_selection_values(cr)
    _fill_job_company(cr)
