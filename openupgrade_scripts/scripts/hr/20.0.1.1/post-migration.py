# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from psycopg2 import sql

from openupgradelib import openupgrade

# hr.contract.type records of 19.0 kept with the same XML-ID by 20.0 as
# hr.employee.type (noupdate): XML-ID -> (19.0 sequence, 20.0 sequence,
# 19.0 en_US name, 20.0 en_US name)
_renamed_type_defaults = {
    "contract_type_interim": (1003, 5, "Interim", "Interim"),
    "contract_type_seasonal": (1004, 7, "Seasonal", "Occasional / Seasonal"),
    "contract_type_intern": (1007, 4, "Intern", "Intern"),
    "contract_type_student": (1008, 3, "Student", "Student"),
    "contract_type_apprenticeship": (1009, 6, "Apprenticeship", "Apprenticeship"),
    "contract_type_thesis": (1010, 8, "Thesis", "Thesis"),
    "contract_type_employee": (1012, 0, "Employee", "Employee"),
}

# Removed noupdate records (only deleted when nothing references them)
_deleted_xmlids = [
    "hr.contract_type_permanent",
    "hr.contract_type_temporary",
    "hr.contract_type_full_time",
    "hr.contract_type_part_time",
    "hr.contract_type_statutory",
    "hr.structure_type_employee_cp200_pfi",
    "hr.structure_type_worker",
]


def _update_renamed_employee_types(cr):
    """noupdate changes of the 7 records kept from hr.contract.type: apply the
    20.0 sequence (and the new English name) when unchanged since 19.0."""
    for name, (seq19, seq20, name19, name20) in _renamed_type_defaults.items():
        openupgrade.logged_query(
            cr,
            """
            UPDATE hr_employee_type het SET sequence = %s
            FROM ir_model_data imd
            WHERE imd.module = 'hr' AND imd.name = %s
                AND imd.model = 'hr.employee.type' AND imd.res_id = het.id
                AND het.sequence = %s
            """,
            (seq20, name, seq19),
        )
        if name19 != name20:
            openupgrade.logged_query(
                cr,
                """
                UPDATE hr_employee_type het
                SET name = het.name || jsonb_build_object('en_US', %s::text)
                FROM ir_model_data imd
                WHERE imd.module = 'hr' AND imd.name = %s
                    AND imd.model = 'hr.employee.type' AND imd.res_id = het.id
                    AND het.name->>'en_US' = %s
                """,
                (name20, name, name19),
            )


def _update_structure_type_name(cr):
    """noupdate change of hr.structure_type_employee_cp200 (name is not
    translatable): 'CP200 BE' -> 'Belgium Pay', when unchanged since 19.0."""
    openupgrade.logged_query(
        cr,
        """
        UPDATE hr_payroll_structure_type st SET name = 'Belgium Pay'
        FROM ir_model_data imd
        WHERE imd.module = 'hr' AND imd.name = 'structure_type_employee_cp200'
            AND imd.model = 'hr.payroll.structure.type' AND imd.res_id = st.id
            AND st.name = 'CP200 BE'
        """,
    )


def _remap_obsolete_types(env):
    """Statutory (19.0 contract type) is the 20.0 "Company Executive" employee
    type. Jobs keeping one of the other obsolete contract types (permanent,
    temporary, full / part time) get the "Employee" type: jobs have no
    selection to fall back on, unlike versions (see pre-migration)."""
    cr = env.cr
    executive = env.ref("hr.contract_type_company_executive", raise_if_not_found=False)
    statutory = env.ref("hr.contract_type_statutory", raise_if_not_found=False)
    if executive and statutory:
        for table in ("hr_version", "hr_job"):
            openupgrade.logged_query(
                cr,
                f"UPDATE {table} SET employee_type_id = %s WHERE employee_type_id = %s",
                (executive.id, statutory.id),
            )
    employee = env.ref("hr.contract_type_employee", raise_if_not_found=False)
    obsolete_ids = tuple(
        record.id
        for record in (
            env.ref("hr." + name, raise_if_not_found=False)
            for name in (
                "contract_type_permanent",
                "contract_type_temporary",
                "contract_type_full_time",
                "contract_type_part_time",
            )
        )
        if record
    )
    if employee and obsolete_ids:
        openupgrade.logged_query(
            cr,
            "UPDATE hr_job SET employee_type_id = %s WHERE employee_type_id IN %s",
            (employee.id, obsolete_ids),
        )


def _referenced_by(cr, table, res_id):
    """Return the first (table, column) whose foreign key points to the
    record, or None."""
    cr.execute(
        """
        SELECT cl.relname, att.attname
        FROM pg_constraint con
        JOIN pg_class cl ON cl.oid = con.conrelid
        JOIN pg_attribute att
            ON att.attrelid = con.conrelid AND att.attnum = ANY(con.conkey)
        WHERE con.contype = 'f' AND con.confrelid = %s::regclass
        """,
        (table,),
    )
    for ref_table, ref_column in cr.fetchall():
        cr.execute(
            sql.SQL("SELECT 1 FROM {} WHERE {} = %s LIMIT 1").format(
                sql.Identifier(ref_table), sql.Identifier(ref_column)
            ),
            (res_id,),
        )
        if cr.fetchone():
            return ref_table, ref_column
    return None


def _delete_unused_records(env):
    """Delete the removed noupdate records nobody uses anymore. A referenced
    one is kept (the foreign keys to these tables are SET NULL, an unlink
    would silently empty the reference)."""
    to_delete = []
    for xmlid in _deleted_xmlids:
        record = env.ref(xmlid, raise_if_not_found=False)
        if not record:
            continue
        reference = _referenced_by(env.cr, record._table, record.id)
        if reference:
            openupgrade.logger.info(
                "Keeping %s, referenced by %s.%s", xmlid, *reference
            )
        else:
            to_delete.append(xmlid)
    openupgrade.delete_records_safely_by_xml_id(env, to_delete)


def _convert_departures(env):
    """The departure of an employee is a record (hr.employee.departure) in
    20.0, hr.version.departure_reason_id / departure_date /
    departure_description are related to it. In 19.0 they were stored on the
    version and the departure was applied immediately by the wizard (employee
    archived): create one departure per employee, already applied
    (apply_date set) so that the new cron does not apply it again."""
    cr = env.cr
    if not openupgrade.column_exists(cr, "hr_version", "departure_date"):
        return
    cr.execute(
        """
        SELECT DISTINCT ON (v.employee_id)
            v.employee_id, v.departure_reason_id,
            COALESCE(v.departure_date, e.write_date::date, v.date_version),
            v.departure_description, cv.contract_date_end
        FROM hr_version v
        JOIN hr_employee e ON e.id = v.employee_id
        LEFT JOIN hr_version cv ON cv.id = e.current_version_id
        WHERE v.departure_id IS NULL
            AND (v.departure_date IS NOT NULL OR v.departure_reason_id IS NOT NULL)
        ORDER BY v.employee_id, v.departure_date DESC NULLS LAST,
            v.date_version DESC
        """
    )
    rows = cr.fetchall()
    if not rows:
        return
    cr.execute("SELECT id FROM hr_departure_reason ORDER BY sequence, id LIMIT 1")
    default_reason = cr.fetchone()
    for employee_id, reason_id, date, description, contract_end in rows:
        reason_id = reason_id or (default_reason and default_reason[0])
        if not reason_id:
            openupgrade.logger.warning(
                "No departure reason: departure of employee %s not converted",
                employee_id,
            )
            continue
        openupgrade.logged_query(
            cr,
            """
            INSERT INTO hr_employee_departure (
                employee_id, departure_reason_id, departure_description,
                dismissal_date, departure_date, action_date, apply_date,
                last_contract_date_end,
                create_uid, create_date, write_uid, write_date)
            VALUES (%s, %s, %s, %s, %s, %s + 1, %s, %s,
                1, now() AT TIME ZONE 'UTC', 1, now() AT TIME ZONE 'UTC')
            RETURNING id
            """,
            (
                employee_id,
                reason_id,
                description,
                date,
                date,
                date,
                date,
                contract_end,
            ),
        )
        departure_id = cr.fetchone()[0]
        openupgrade.logged_query(
            cr,
            """
            UPDATE hr_version SET departure_id = %s
            WHERE employee_id = %s AND departure_id IS NULL
                AND (departure_date IS NOT NULL OR departure_reason_id IS NOT NULL)
            """,
            (departure_id, employee_id),
        )


def _map_job_recruiter(cr):
    """hr.job.user_id (res.users) -> recruiter_id (hr.employee)."""
    if not openupgrade.column_exists(cr, "hr_job", "user_id"):
        return
    openupgrade.logged_query(
        cr,
        """
        UPDATE hr_job j
        SET recruiter_id = (
            SELECT e.id FROM hr_employee e
            WHERE e.user_id = j.user_id
            ORDER BY (e.company_id = j.company_id) DESC, e.active DESC, e.id
            LIMIT 1)
        WHERE j.user_id IS NOT NULL AND j.recruiter_id IS NULL
        """,
    )


@openupgrade.migrate()
def migrate(env, version):
    _update_renamed_employee_types(env.cr)
    _update_structure_type_name(env.cr)
    _remap_obsolete_types(env)
    _convert_departures(env)
    _map_job_recruiter(env.cr)
    _delete_unused_records(env)
