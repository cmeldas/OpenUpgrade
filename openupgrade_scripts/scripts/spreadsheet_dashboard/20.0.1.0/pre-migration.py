# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from openupgradelib import openupgrade


def _precreate_share_name(env):
    """spreadsheet.dashboard.share.name was a non stored related field
    (dashboard_id.name, translatable) and is now a stored, required, non
    translatable char computed only when empty ("<dashboard> - Share Link" in
    the language of the migration environment). Keep the value users saw in
    v19: the dashboard name in the language of the user who created the share
    link, falling back to en_US."""
    if openupgrade.column_exists(env.cr, "spreadsheet_dashboard_share", "name"):
        return
    openupgrade.add_columns(
        env,
        [
            (
                "spreadsheet.dashboard.share",
                "name",
                "char",
                None,
                "spreadsheet_dashboard_share",
            )
        ],
    )
    openupgrade.logged_query(
        env.cr,
        """
        UPDATE spreadsheet_dashboard_share share
        SET name = COALESCE(
            NULLIF(dashboard.name ->> partner.lang, ''),
            dashboard.name ->> 'en_US'
        )
        FROM spreadsheet_dashboard dashboard,
            res_users users
        JOIN res_partner partner ON partner.id = users.partner_id
        WHERE dashboard.id = share.dashboard_id
            AND users.id = share.create_uid
            AND share.name IS NULL
        """,
    )
    openupgrade.logged_query(
        env.cr,
        """
        UPDATE spreadsheet_dashboard_share share
        SET name = dashboard.name ->> 'en_US'
        FROM spreadsheet_dashboard dashboard
        WHERE dashboard.id = share.dashboard_id
            AND share.name IS NULL
        """,
    )


def _share_name_not_translated(env):
    """The v19 related field inherited translate=True from the dashboard name,
    so ir_model_fields still flags spreadsheet.dashboard.share.name as
    translated. Odoo reads these flags before loading any module
    (registry._database_translated_fields) and keeps such fields translated
    while the modules load, which converts the new varchar column into jsonb
    ({"en_US": ...}). The final pass that should make the field non translated
    again does not convert the column back, so the v20 Char field would read
    a dict. The field was never stored before: drop the stale flag, both in
    the database and in the set of the registry already read."""
    openupgrade.logged_query(
        env.cr,
        """
        UPDATE ir_model_fields SET translate = NULL
        WHERE model = 'spreadsheet.dashboard.share' AND name = 'name'
            AND translate IS NOT NULL
        """,
    )
    translated = getattr(env.registry, "_database_translated_fields", None)
    if translated is not None:
        translated.pop("spreadsheet.dashboard.share.name", None)


@openupgrade.migrate()
def migrate(env, version):
    _share_name_not_translated(env)
    _precreate_share_name(env)
