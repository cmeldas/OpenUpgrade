# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging
from collections import defaultdict
from html import escape

from openupgradelib import openupgrade

from odoo.tools import email_normalize, email_split

_logger = logging.getLogger(__name__)


def fill_maintenance_request_user_ids(env):
    """user_id (technician) becomes the stored computed many2many user_ids.
    When the relation table is created, the ORM computes it from the equipment
    technician; set it to the technician the request really had in v19."""
    cr = env.cr
    if not openupgrade.column_exists(cr, "maintenance_request", "user_id"):
        return
    openupgrade.logged_query(
        cr,
        """
        DELETE FROM maintenance_request_res_users_rel rel
        USING maintenance_request mr
        WHERE rel.maintenance_request_id = mr.id
            AND rel.res_users_id IS DISTINCT FROM mr.user_id
        """,
    )
    openupgrade.logged_query(
        cr,
        """
        INSERT INTO maintenance_request_res_users_rel
            (maintenance_request_id, res_users_id)
        SELECT mr.id, mr.user_id
        FROM maintenance_request mr
        JOIN res_users ru ON ru.id = mr.user_id
        ON CONFLICT DO NOTHING
        """,
    )
    env["maintenance.request"].invalidate_model(["user_ids"])
    # The ORM computation of the new tracked field, run when the relation
    # table is created, logged a tracking message on every request
    # ("None -> <technician> (Technicians)", by OdooBot, dated the migration).
    # The technician did not change and the relation was reset just above:
    # drop these messages. They were created in this transaction (same
    # create_date as now()), before any user could write a request.
    openupgrade.logged_query(
        cr,
        """
        DELETE FROM mail_message
        WHERE model = 'maintenance.request'
            AND message_type = 'tracking'
            AND create_date = (now() AT TIME ZONE 'UTC')
        """,
    )


def subscribe_email_cc_partners(env, model_name, table):
    """mail.thread.cc is gone in v20 (email_cc was used for the suggested
    recipients). Keep the information by subscribing the existing partners
    having those addresses; unknown addresses are only logged, the legacy
    column email_cc stays in the table."""
    cr = env.cr
    if not openupgrade.column_exists(cr, table, "email_cc"):
        return
    cr.execute(
        f"SELECT id, email_cc FROM {table} "  # pylint: disable=sql-injection
        "WHERE COALESCE(email_cc, '') != ''"
    )
    emails_by_record = {}
    for record_id, email_cc in cr.fetchall():
        emails = {email_normalize(email) for email in email_split(email_cc)}
        emails.discard(False)
        if emails:
            emails_by_record[record_id] = emails
    all_emails = set().union(*emails_by_record.values()) if emails_by_record else set()
    if not all_emails:
        return
    partners = (
        env["res.partner"]
        .with_context(active_test=False)
        .search([("email_normalized", "in", list(all_emails))], order="active desc, id")
    )
    partner_by_email = {}
    for partner in partners:
        partner_by_email.setdefault(partner.email_normalized, partner.id)
    records_by_partners = defaultdict(list)
    for record_id, emails in emails_by_record.items():
        missing = emails - set(partner_by_email)
        if missing:
            _logger.warning(
                "%s %s: no partner for email_cc address(es) %s, kept only in "
                "the legacy column email_cc",
                model_name,
                record_id,
                ", ".join(sorted(missing)),
            )
        partner_ids = tuple(
            sorted(partner_by_email[email] for email in emails if email in partner_by_email)
        )
        if partner_ids:
            records_by_partners[partner_ids].append(record_id)
    model = env[model_name].with_context(
        tracking_disable=True, mail_notrack=True, mail_create_nolog=True
    )
    for partner_ids, record_ids in records_by_partners.items():
        model.browse(record_ids).message_subscribe(partner_ids=list(partner_ids))


def keep_equipment_location(env):
    """maintenance.equipment#location (char) was dropped by the v18 -> v19
    migration without a replacement; its values were left in the orphan column.
    v20 only has stock_maintenance's location_id (stock.location), which cannot
    be derived from free text, so the text is kept in the equipment note."""
    cr = env.cr
    if not openupgrade.column_exists(cr, "maintenance_equipment", "location"):
        return
    if "location" in env["maintenance.equipment"]._fields:
        return
    cr.execute(
        """
        SELECT id, location, note FROM maintenance_equipment
        WHERE COALESCE(TRIM(location), '') != ''
        """
    )
    for equipment_id, location, note in cr.fetchall():
        paragraph = "<p>Location: %s</p>" % escape(location.strip())
        if paragraph in (note or ""):
            continue
        openupgrade.logged_query(
            cr,
            """
            UPDATE maintenance_equipment
            SET note = %s || COALESCE(note, '')
            WHERE id = %s
            """,
            (paragraph, equipment_id),
        )
    env["maintenance.equipment"].invalidate_model(["note"])


@openupgrade.migrate()
def migrate(env, version):
    openupgrade.load_data(env, "maintenance", "20.0.1.0/noupdate_changes.xml")
    fill_maintenance_request_user_ids(env)
    subscribe_email_cc_partners(env, "maintenance.request", "maintenance_request")
    keep_equipment_location(env)
