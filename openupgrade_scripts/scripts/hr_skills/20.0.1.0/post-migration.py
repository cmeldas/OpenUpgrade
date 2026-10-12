# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from psycopg2 import sql

from openupgradelib import openupgrade


def _is_referenced(cr, table, res_id):
    """True when a foreign key of any table points to the record."""
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
            return True
    return False


@openupgrade.migrate()
def migrate(env, version):
    # Removed noupdate activity type ("Upload Certification"), kept when an
    # activity, a message or a plan still uses it
    xmlid = "hr_skills.mail_activity_data_upload_certification"
    activity_type = env.ref(xmlid, raise_if_not_found=False)
    if activity_type and not _is_referenced(
        env.cr, activity_type._table, activity_type.id
    ):
        openupgrade.delete_records_safely_by_xml_id(env, [xmlid])
