# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging
from collections import defaultdict

from openupgradelib import openupgrade

from odoo.tools import email_normalize, email_split

_logger = logging.getLogger(__name__)


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
        active_test=False,
        tracking_disable=True,
        mail_notrack=True,
        mail_create_nolog=True,
    )
    for partner_ids, record_ids in records_by_partners.items():
        model.browse(record_ids).message_subscribe(partner_ids=list(partner_ids))


@openupgrade.migrate()
def migrate(env, version):
    # email layout of two templates and t-esc -> t-out in the assignation
    # template; the ir.access part is recreated from the v20 data (base)
    openupgrade.load_data(env, "project", "20.0.1.4/noupdate_changes.xml")
    subscribe_email_cc_partners(env, "project.task", "project_task")
    subscribe_email_cc_partners(env, "project.update", "project_update")
