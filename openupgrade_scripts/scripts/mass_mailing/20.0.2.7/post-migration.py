# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from openupgradelib import openupgrade


def _mailing_filter_ids(env):
    """mailing_filter_id (many2one) became the many2many mailing_filter_ids,
    keep the selected favorite filter."""
    if not openupgrade.column_exists(env.cr, "mailing_mailing", "mailing_filter_id"):
        return
    openupgrade.logged_query(
        env.cr,
        """
        INSERT INTO mail_mass_mailing_filter_rel (mailing_mailing_id, mailing_filter_id)
        SELECT mailing.id, mailing.mailing_filter_id
        FROM mailing_mailing mailing
        JOIN mailing_filter filter ON filter.id = mailing.mailing_filter_id
        ON CONFLICT DO NOTHING
        """,
    )


@openupgrade.migrate()
def migrate(env, version):
    # mailing_contact_view_form_split_name (noupdate, inactive unless the
    # first/last name split is enabled) anchored last_name on create_date,
    # 20.0 anchors it on country_id
    openupgrade.load_data(env, "mass_mailing", "20.0.2.7/noupdate_changes.xml")
    _mailing_filter_ids(env)
