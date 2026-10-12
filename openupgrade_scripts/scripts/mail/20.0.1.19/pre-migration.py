# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

from openupgradelib import openupgrade

_logger = logging.getLogger(__name__)

# Snapshot of the ir.model.fields rows referenced by tracking values, taken
# before any module of the 20.0 graph can remove the fields (see end-migration)
TRACKING_FIELD_TABLE = "openupgrade_legacy_20_0_mail_tracking_field"

_tracking_xmlids = [
    (
        "mail.action_view_mail_tracking_value",
        "mail_tracking.action_view_mail_tracking_value",
    ),
    ("mail.menu_mail_tracking_value", "mail_tracking.menu_mail_tracking_value"),
    (
        "mail.view_mail_tracking_value_form",
        "mail_tracking.view_mail_tracking_value_form",
    ),
    (
        "mail.view_mail_tracking_value_tree",
        "mail_tracking.view_mail_tracking_value_tree",
    ),
    (
        "mail.field_mail_message__tracking_value_ids",
        "mail_tracking.field_mail_message__tracking_value_ids",
    ),
    (
        "mail.field_mail_mail__tracking_value_ids",
        "mail_tracking.field_mail_mail__tracking_value_ids",
    ),
]

_renamed_fields = [
    ("mail.message", "mail_message", "starred_partner_ids", "bookmarked_partner_ids"),
]

_renamed_tables = [
    (
        "mail_message_res_partner_starred_rel",
        "mail_message_res_partner_bookmarked_rel",
    ),
]


def _move_tracking_values_to_mail_tracking(cr):
    """mail.tracking.value moved from mail to the new module mail_tracking,
    which is not auto_install: install it (and its auto_install bridges with
    the installed modules), so that the model keeps its data and identity.
    The framework may already have flagged the module through
    apriori.split_modules, the queries are idempotent."""
    openupgrade.logged_query(
        cr,
        """
        UPDATE ir_module_module
        SET state = 'to install'
        WHERE name = 'mail_tracking' AND state IN ('uninstalled', 'to remove')
        """,
    )
    for bridge, dependency in (
        ("mail_tracking_sms", "sms"),
        ("mail_tracking_mass_mailing", "mass_mailing"),
    ):
        openupgrade.logged_query(
            cr,
            """
            UPDATE ir_module_module bridge
            SET state = 'to install'
            FROM ir_module_module dependency
            WHERE bridge.name = %s
                AND bridge.state IN ('uninstalled', 'to remove')
                AND dependency.name = %s
                AND dependency.state IN ('installed', 'to upgrade', 'to install')
            """,
            (bridge, dependency),
        )
    openupgrade.update_module_moved_models(
        cr, "mail.tracking.value", "mail", "mail_tracking"
    )
    openupgrade.rename_xmlids(cr, _tracking_xmlids)


def _snapshot_tracking_fields(cr):
    """Odoo 20 renders tracking values into the body of the message at
    creation time; historical values are rendered in end-migration. Their
    ir.model.fields rows can disappear in between (removed fields or models,
    e.g. stock.scrap), the FK then sets field_id to NULL. Keep the field id in
    a legacy column and a copy of the field definitions."""
    legacy_column = openupgrade.get_legacy_name("field_id")
    if not openupgrade.column_exists(cr, "mail_tracking_value", legacy_column):
        openupgrade.copy_columns(
            cr, {"mail_tracking_value": [("field_id", None, None)]}
        )
    if not openupgrade.table_exists(cr, TRACKING_FIELD_TABLE):
        openupgrade.logged_query(
            cr,
            f"""
            CREATE TABLE {TRACKING_FIELD_TABLE} AS
            SELECT imf.id, imf.model, imf.name, imf.ttype, imf.relation,
                imf.field_description
            FROM ir_model_fields imf
            WHERE imf.id IN (
                SELECT DISTINCT field_id
                FROM mail_tracking_value
                WHERE field_id IS NOT NULL
            )
            """,
        )
        openupgrade.logged_query(
            cr, f"ALTER TABLE {TRACKING_FIELD_TABLE} ADD PRIMARY KEY (id)"
        )


def _tracking_currency_to_field_info(cr):
    """mail.tracking.value.currency_id is gone in 20.0, the currency of a
    monetary tracking is stored in field_info['currency_id'] instead
    (mail.track.mixin._create_mail_tracking_values)."""
    if not openupgrade.column_exists(cr, "mail_tracking_value", "currency_id"):
        return
    openupgrade.logged_query(
        cr,
        """
        UPDATE mail_tracking_value
        SET field_info = COALESCE(field_info, '{}'::jsonb)
            || jsonb_build_object('currency_id', currency_id)
        WHERE currency_id IS NOT NULL
            AND NOT COALESCE(field_info, '{}'::jsonb) ? 'currency_id'
        """,
    )


def _channel_member_xmlids(cr):
    """The membership of the admin in the administrators channel is module
    data in 20.0 (noupdate): give the xmlid to the existing member, otherwise
    loading the data violates discuss_channel_member_partner_unique."""
    cr.execute(
        """
        SELECT member.id
        FROM discuss_channel_member member
        JOIN ir_model_data channel_xmlid
            ON channel_xmlid.model = 'discuss.channel'
            AND channel_xmlid.module = 'mail'
            AND channel_xmlid.name = 'channel_admin'
            AND channel_xmlid.res_id = member.channel_id
        JOIN ir_model_data partner_xmlid
            ON partner_xmlid.model = 'res.partner'
            AND partner_xmlid.module = 'base'
            AND partner_xmlid.name = 'partner_admin'
            AND partner_xmlid.res_id = member.partner_id
        WHERE NOT EXISTS (
            SELECT 1 FROM ir_model_data
            WHERE module = 'mail'
                AND name = 'channel_member_channel_admin_partner_admin'
        )
        """
    )
    row = cr.fetchone()
    if row:
        openupgrade.add_xmlid(
            cr,
            "mail",
            "channel_member_channel_admin_partner_admin",
            "discuss.channel.member",
            row[0],
            noupdate=True,
        )


def _activity_type_chaining(cr):
    """chaining_type (suggest / trigger), triggered_next_type_id and the
    suggested_next_type_ids many2many are replaced by the single many2one
    suggested_next_type_id (always a suggestion). Keep the triggered type, or
    the first suggested type by sequence."""
    if not openupgrade.column_exists(cr, "mail_activity_type", "chaining_type"):
        return
    if not openupgrade.column_exists(
        cr, "mail_activity_type", "suggested_next_type_id"
    ):
        openupgrade.logged_query(
            cr,
            """
            ALTER TABLE mail_activity_type
            ADD COLUMN suggested_next_type_id integer
            """,
        )
    openupgrade.logged_query(
        cr,
        """
        UPDATE mail_activity_type
        SET suggested_next_type_id = triggered_next_type_id
        WHERE chaining_type = 'trigger'
            AND triggered_next_type_id IS NOT NULL
            AND suggested_next_type_id IS NULL
        RETURNING id
        """,
    )
    triggered = [row[0] for row in cr.fetchall()]
    if triggered:
        _logger.warning(
            "Activity types %s triggered their next activity automatically; "
            "in Odoo 20 the next activity is only suggested",
            triggered,
        )
    if openupgrade.table_exists(cr, "mail_activity_rel"):
        openupgrade.logged_query(
            cr,
            """
            UPDATE mail_activity_type mat
            SET suggested_next_type_id = sub.recommended_id
            FROM (
                SELECT DISTINCT ON (rel.activity_id)
                    rel.activity_id, rel.recommended_id
                FROM mail_activity_rel rel
                JOIN mail_activity_type t ON t.id = rel.recommended_id
                ORDER BY rel.activity_id, t.sequence, t.id
            ) sub
            WHERE sub.activity_id = mat.id
                AND mat.chaining_type = 'suggest'
                AND mat.suggested_next_type_id IS NULL
            """,
        )
        cr.execute(
            """
            SELECT activity_id, count(*)
            FROM mail_activity_rel
            GROUP BY activity_id
            HAVING count(*) > 1
            """
        )
        for type_id, count in cr.fetchall():
            _logger.warning(
                "Activity type %s suggested %s next activity types, only the "
                "first one by sequence is kept in Odoo 20",
                type_id,
                count,
            )


def _server_actions_log_note(cr):
    """Server actions 'Send Message' had mail_post_method email / comment /
    note. A note is a separate action type (log_note) in 20.0, with a static
    body instead of a template: copy the template body (all languages)."""
    if not openupgrade.column_exists(cr, "ir_act_server", "mail_post_method"):
        return
    cr.execute(
        """
        SELECT count(*) FROM ir_act_server
        WHERE state = 'mail_post' AND mail_post_method = 'note'
        """
    )
    if not cr.fetchone()[0]:
        return
    if not openupgrade.column_exists(cr, "ir_act_server", "log_note_note"):
        openupgrade.logged_query(
            cr, "ALTER TABLE ir_act_server ADD COLUMN log_note_note jsonb"
        )
    openupgrade.logged_query(
        cr,
        """
        UPDATE ir_act_server action
        SET state = 'log_note', log_note_note = template.body_html
        FROM mail_template template
        WHERE template.id = action.template_id
            AND action.state = 'mail_post'
            AND action.mail_post_method = 'note'
        RETURNING action.id
        """,
    )
    for (action_id,) in cr.fetchall():
        _logger.warning(
            "Server action %s logged a note from a mail template: it now logs "
            "the static body of that template, review it",
            action_id,
        )


def _starred_to_bookmarked(env):
    """Starred messages are bookmarked messages in 20.0 (same relation)."""
    cr = env.cr
    old_table, new_table = _renamed_tables[0]
    if openupgrade.table_exists(cr, old_table) and not openupgrade.table_exists(
        cr, new_table
    ):
        openupgrade.rename_tables(cr, _renamed_tables)
        openupgrade.logged_query(
            cr,
            "UPDATE ir_model_relation SET name = %s WHERE name = %s",
            (new_table, old_table),
        )
    openupgrade.rename_fields(env, _renamed_fields)


@openupgrade.migrate()
def migrate(env, version):
    cr = env.cr
    _move_tracking_values_to_mail_tracking(cr)
    _snapshot_tracking_fields(cr)
    _tracking_currency_to_field_info(cr)
    _channel_member_xmlids(cr)
    _starred_to_bookmarked(env)
    _activity_type_chaining(cr)
    _server_actions_log_note(cr)
